"""Higher-level system prompt assembly.

Phase 3 扩展：在原有动态上下文（权限模式 / 技能列表 / 项目记忆 / RAG 检索）
基础上，新增求职领域上下文段——用户求职画像与投递状态概览，并按
``项目改造计划.md`` 7.4.2 的 Token 分配策略控制预算：

- RAG 检索结果    ≤ 15% 上下文（``rag/retriever.py``，每次对话自动检索）
- 用户求职画像     ≤ 3%  上下文（``user_profile_tool`` 存储，已设置画像时注入）
- 投递状态概览     ≤ 2%  上下文（``application_tracker_tool`` 存储，有记录时注入）
- 求职扩展内容合计 ≤ 25% 上下文；优先级 RAG 检索 > 用户画像 > 投递状态，
  联合超限时按优先级从低到高截断。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_issue_file,
    get_project_pr_comments_file,
)
from openharness.config.settings import Settings
from openharness.coordinator.coordinator_mode import (
    get_coordinator_system_prompt,
    is_coordinator_mode,
)
from openharness.jobhunt.profile import ai_visible_profile, migrate_profile_store
from openharness.memory import load_memory_prompt
from openharness.memory.relevance import format_relevant_memories, select_relevant_memories
from openharness.memory.usage import mark_memory_used
from openharness.permissions.modes import PermissionMode
from openharness.personalization.rules import load_local_rules
from openharness.prompts.claudemd import load_claude_md_prompt
from openharness.prompts.system_prompt import build_system_prompt
from openharness.rag.utils import estimate_tokens
from openharness.skills.loader import load_skill_registry

# ---------------------------------------------------------------------------
# Phase 3：求职领域上下文的 Token 预算（占总上下文窗口的比例）
# ---------------------------------------------------------------------------

#与 rag.retriever.Retriever.DEFAULT_CONTEXT_WINDOW 保持一致；
#settings 未显式配置 context_window_tokens 时的回退窗口
_DEFAULT_CONTEXT_WINDOW_TOKENS = 16000
_RAG_BUDGET_RATIO = 0.15  # RAG 检索结果 ≤ 15%
_PROFILE_BUDGET_RATIO = 0.03  # 用户求职画像 ≤ 3%
_APPLICATIONS_BUDGET_RATIO = 0.02  # 投递状态概览 ≤ 2%
_JOINT_BUDGET_RATIO = 0.25  # 求职扩展内容合计 ≤ 25%

#联合预算截断的最小可用 token 数：剩余预算连一段碎片都放不下时直接丢弃该段
_MIN_TRUNCATED_SECTION_TOKENS = 32

#投递状态概览最多列出的最近记录条数（受 2% 预算约束，超出部分截断）
_MAX_RECENT_APPLICATIONS = 5

#与 application_tracker_tool 的终态定义保持一致（不重复导入工具模块）
_TERMINAL_APPLICATION_STATUSES = ("已入职", "已拒绝")


def _context_window_tokens(settings: Settings) -> int:
    """Return the effective context window used for job-hunt budgeting."""
    return settings.context_window_tokens or _DEFAULT_CONTEXT_WINDOW_TOKENS


_TRUNCATION_MARKER = "\n…（已按 Token 预算截断）"


def _truncate_to_budget(text: str, budget_tokens: int) -> str:
    """Truncate text to at most ``budget_tokens`` (CJK-aware estimate).

    截断标记本身占用的 token 也计入预算，保证返回值不超限。
    """
    if budget_tokens <= 0 or not text:
        return ""
    if estimate_tokens(text) <= budget_tokens:
        return text
    marker_tokens = estimate_tokens(_TRUNCATION_MARKER)
    target = max(1, budget_tokens - marker_tokens)
    total = estimate_tokens(text)
    char_budget = max(1, int(len(text) * target / max(1, total)))
    result = text[:char_budget].rstrip() + _TRUNCATION_MARKER
    #比例映射对中英混排有少量误差，逐步收缩到预算内
    while estimate_tokens(result) > budget_tokens and char_budget > 1:
        char_budget = max(1, int(char_budget * 0.9))
        result = text[:char_budget].rstrip() + _TRUNCATION_MARKER
    return result


def _apply_joint_budget(sections: Sequence[str], *, joint_budget_tokens: int) -> list[str]:
    """Keep the highest-priority sections within the joint token budget.

    ``sections`` must be ordered by descending priority（RAG 检索 > 用户画像 >
    投递状态）. 高优先级段优先占满预算；溢出发生在哪一段就截断哪一段，
    其后的低优先级段丢弃——即“超限时按优先级从低到高截断”。
    """
    if joint_budget_tokens <= 0 or not sections:
        return []
    kept: list[str] = []
    used = 0
    for index, section in enumerate(sections):
        remaining = joint_budget_tokens - used
        if remaining <= 0:
            break
        tokens = estimate_tokens(section)
        if tokens <= remaining:
            kept.append(section)
            used += tokens
            continue
        if index == 0 or remaining >= _MIN_TRUNCATED_SECTION_TOKENS:
            kept.append(_truncate_to_budget(section, remaining))
        break
    return kept


def _build_skills_section(
    cwd: str | Path,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Build a system prompt section listing available skills."""
    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        "The following skills are available via the `skill` tool. "
        "When a user's request matches a skill, invoke it with `skill(name=\"<skill_name>\")` "
        "to load detailed instructions before proceeding. "
        "User-invocable skills can also be run directly by the user as `/<skill-name>`.",
        "",
    ]
    for skill in skills:
        command_name = skill.command_name or skill.name
        display = f" ({skill.display_name})" if skill.display_name else ""
        lines.append(f"- **{command_name}**{display}: {skill.description}")
    return "\n".join(lines)


def _build_delegation_section() -> str:
    """Build a concise section describing delegation and worker usage."""
    return "\n".join(
        [
            "# Delegation And Subagents",
            "",
            "OpenHarness can delegate background work with the `agent` tool.",
            "Use it when the user explicitly asks for a subagent, background worker, or parallel investigation, "
            "or when the task clearly benefits from splitting off a focused worker.",
            "",
            "Default pattern:",
            '- Spawn with `agent(description=..., prompt=..., subagent_type="worker")`.',
            "- Inspect running or recorded workers with `/agents`.",
            "- Inspect one worker in detail with `/agents show TASK_ID`.",
            "- Send follow-up instructions with `send_message(task_id=..., message=...)`.",
            "- Read worker output with `task_output(task_id=...)`.",
            "",
            "Prefer a normal direct answer for simple tasks. Use subagents only when they materially help.",
        ]
    )


def _build_permission_mode_section(settings: Settings) -> str:
    """Build current permission-mode guidance for the model."""
    mode = settings.permission.mode
    if mode == PermissionMode.PLAN:
        guidance = (
            "Plan mode is enabled. Treat this session as read-only planning and analysis. "
            "Do not call mutating tools such as file writes, edits, package installs, "
            "state-changing shell commands, or task-spawning actions unless the user exits plan mode."
        )
    elif mode == PermissionMode.FULL_AUTO:
        guidance = (
            "Full-auto permission mode is enabled. You may use mutating tools when they are necessary "
            "for the user's request, while still keeping changes scoped and intentional."
        )
    else:
        guidance = (
            "Default permission mode is enabled. Read-only tools can run directly; mutating tools "
            "may require explicit user approval."
        )
    return f"# Current Permission Mode\n{guidance}"


def build_rag_context(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None,
) -> str | None:
    """Build the RAG retrieval section for the runtime system prompt.

    Returns ``None`` when RAG is disabled, no query is available, no
    documents match, or retrieval fails - prompt assembly must never be
    blocked by the retrieval layer.

    Phase 3 预算：检索结果 ≤ 15% 上下文，且不超过用户在 ``rag.retrieval``
    中配置的预算。
    """
    del cwd  # 预留：后续可按项目维度限定检索范围
    if not settings.rag.enabled:
        return None
    if not latest_user_prompt or not latest_user_prompt.strip():
        return None
    try:
        from openharness.rag import build_retriever_from_settings
        from openharness.rag.retriever import format_retrieval_for_prompt

        retriever = build_retriever_from_settings(settings)
        rag_budget = min(
            retriever.context_budget(),
            int(_context_window_tokens(settings) * _RAG_BUDGET_RATIO),
        )
        outcome = retriever.retrieve_sync(latest_user_prompt, budget_tokens=rag_budget)
        if not outcome.hits:
            return None
        body = format_retrieval_for_prompt(outcome)
        if not body:
            return None
        return f"# Retrieved Knowledge\n\n{body}"
    except Exception:  # 检索层不得阻断提示词组装
        return None


# ---------------------------------------------------------------------------
# Phase 3：用户求职画像与投递状态概览
# ---------------------------------------------------------------------------


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _format_profile_context(profile: Mapping[str, Any]) -> str:
    """Render the stored candidate profile as a compact prompt section.

    结构与 user_profile_tool 存储的画像一致：basic / skills / education /
    experience / preferences / job_search_status；只渲染隐私设置允许的
    标准字段，未知字段永远不会自动进入模型上下文。
    """
    profile = ai_visible_profile(profile)
    if not profile:
        return ""
    lines = [
        "# Candidate Profile",
        "",
        "用户允许 AI 使用的本地用户画像（联系方式默认不包含）：",
    ]

    basic = _as_mapping(profile.get("basic"))
    basic_parts: list[str] = []
    if str(basic.get("current_title", "")).strip():
        basic_parts.append(f"现任 {basic['current_title']}")
    if basic.get("years_of_experience") is not None:
        basic_parts.append(f"{basic['years_of_experience']} 年经验")
    if str(basic.get("current_city", "")).strip():
        basic_parts.append(f"现居 {basic['current_city']}")
    if target_cities := _as_str_list(basic.get("target_cities")):
        basic_parts.append("目标城市 " + "、".join(target_cities))
    if basic.get("current_salary") is not None:
        basic_parts.append(f"当前月薪 {basic['current_salary']}K")
    if basic_parts:
        lines.append("- 基础：" + "｜".join(basic_parts))

    skills = _as_mapping(profile.get("skills"))
    technical: list[str] = []
    for item in skills.get("technical") or []:
        if isinstance(item, Mapping):
            name = str(item.get("name", "")).strip()
            level = str(item.get("level", "")).strip()
            if name:
                technical.append(f"{name}({level})" if level else name)
        elif str(item).strip():
            technical.append(str(item).strip())
    skill_parts: list[str] = []
    if technical:
        skill_parts.append("、".join(technical))
    if domains := _as_str_list(skills.get("domains")):
        skill_parts.append("领域经验 " + "、".join(domains))
    if soft := _as_str_list(skills.get("soft")):
        skill_parts.append("软技能 " + "、".join(soft))
    if languages := _as_str_list(skills.get("languages")):
        skill_parts.append("语言 " + "、".join(languages))
    if skill_parts:
        lines.append("- 技能：" + "｜".join(skill_parts))

    education = _as_mapping(profile.get("education"))
    edu_parts = [
        str(education.get(key, "")).strip()
        for key in ("school", "degree", "major", "graduation_year")
        if str(education.get(key, "")).strip()
    ]
    if edu_parts:
        lines.append("- 教育：" + " ".join(edu_parts))

    experience = _as_mapping(profile.get("experience"))
    experience_parts = [
        str(experience.get(key, "")).strip()
        for key in ("summary", "current_company", "projects")
        if str(experience.get(key, "")).strip()
    ]
    if experience_parts:
        lines.append("- 经历：" + "｜".join(experience_parts))

    preferences = _as_mapping(profile.get("preferences"))
    pref_parts: list[str] = []
    if positions := _as_str_list(preferences.get("target_positions")):
        pref_parts.append("目标岗位 " + "、".join(positions))
    salary_min = preferences.get("expected_salary_min")
    salary_max = preferences.get("expected_salary_max")
    if salary_min is not None or salary_max is not None:
        pref_parts.append(f"期望月薪 {salary_min if salary_min is not None else ''}-"
                          f"{salary_max if salary_max is not None else ''}K")
    if company_types := _as_str_list(preferences.get("company_types")):
        pref_parts.append("公司类型 " + "、".join(company_types))
    if work_mode := str(preferences.get("work_mode", "")).strip():
        pref_parts.append(f"工作方式 {work_mode}")
    if pref_parts:
        lines.append("- 偏好：" + "｜".join(pref_parts))

    if status := str(profile.get("job_search_status", "")).strip():
        lines.append(f"- 求职状态：{status}")

    if len(lines) <= 3:  # 没有可识别字段时退化为紧凑 JSON，保证画像仍被注入
        lines.append("- " + str(dict(profile)))
    return "\n".join(lines)


def build_profile_context(settings: Settings, *, budget_tokens: int | None = None) -> str | None:
    """Build the candidate-profile section for the runtime system prompt.

    触发条件：用户已通过 ``profile_update`` 写入画像。读取失败（目录不可
    访问、JSON 损坏等）时返回 ``None``——本地存储问题不得阻断提示词组装。
    """
    try:
        from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir

        directory = resolve_jobhunt_dir(configured=settings.job_hunt.data_directory)
        profile = migrate_profile_store(JobHuntStore(directory), settings)
    except Exception:  # 本地存储不得阻断提示词组装
        return None
    if not profile:
        return None
    section = _format_profile_context(profile)
    if not section:
        return None
    if budget_tokens is not None:
        section = _truncate_to_budget(section, budget_tokens)
    return section or None


def _format_applications_context(records: Sequence[Mapping[str, Any]]) -> str:
    """Render the application tracker as a compact status overview."""
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status", "")).strip()
        if status:
            counts[status] = counts.get(status, 0) + 1
    active = sum(count for status, count in counts.items() if status not in _TERMINAL_APPLICATION_STATUSES)

    lines = [
        "# Application Status",
        "",
        f"本地投递追踪：共 {len(records)} 条投递，进行中 {active} 条"
        + ("（" + "、".join(f"{s} {n}" for s, n in counts.items()) + "）" if counts else ""),
    ]

    def _sort_key(record: Mapping[str, Any]) -> str:
        return str(record.get("updated_at", ""))

    recent = sorted(records, key=_sort_key, reverse=True)[:_MAX_RECENT_APPLICATIONS]
    if recent:
        lines.append("- 最近动态：")
        for record in recent:
            company = str(record.get("company", "")).strip()
            position = str(record.get("position", "")).strip()
            status = str(record.get("status", "")).strip()
            updated = str(record.get("last_update_date") or record.get("applied_date") or "").strip()
            lines.append(f"  - {company}｜{position}｜{status}｜{updated}")
    lines.append("- 明细用 application_list 查询，待跟进记录用 application_remind 提醒。")
    return "\n".join(lines)


def build_application_status_context(
    settings: Settings, *, budget_tokens: int | None = None
) -> str | None:
    """Build the application-status overview section for the runtime prompt.

    触发条件：投递追踪中有记录。读取失败时返回 ``None``。
    """
    try:
        from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir

        directory = resolve_jobhunt_dir(configured=settings.job_hunt.data_directory)
        records = JobHuntStore(directory).load_applications()
    except Exception:  # 本地存储不得阻断提示词组装
        return None
    if not records:
        return None
    section = _format_applications_context(records)
    if budget_tokens is not None:
        section = _truncate_to_budget(section, budget_tokens)
    return section or None


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    include_project_memory: bool = True,
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    if is_coordinator_mode():
        sections = [get_coordinator_system_prompt()]
    else:
        sections = [build_system_prompt(custom_prompt=settings.system_prompt, cwd=str(cwd))]

    if not is_coordinator_mode() and settings.system_prompt is None:
        sections[0] = build_system_prompt(cwd=str(cwd))

    sections.append(_build_permission_mode_section(settings))

    if settings.fast_mode:
        sections.append(
            "# Session Mode\nFast mode is enabled. Prefer concise replies, minimal tool use, and quicker progress over exhaustive exploration."
        )

    sections.append(
        "# Reasoning Settings\n"
        f"- Effort: {settings.effort}\n"
        f"- Passes: {settings.passes}\n"
        "Adjust depth and iteration count to match these settings while still completing the task."
    )

    skills_section = _build_skills_section(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    if skills_section and not is_coordinator_mode():
        sections.append(skills_section)

    if not is_coordinator_mode():
        sections.append(_build_delegation_section())

    claude_md = load_claude_md_prompt(cwd)
    if claude_md:
        sections.append(claude_md)

    local_rules = load_local_rules()
    if local_rules:
        sections.append(f"# Local Environment Rules\n\n{local_rules}")

    for title, path in (
        ("Issue Context", get_project_issue_file(cwd)),
        ("Pull Request Comments", get_project_pr_comments_file(cwd)),
        ("Active Repo Context", get_project_active_repo_context_path(cwd)),
    ):
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    if include_project_memory and settings.memory.enabled:
        memory_section = load_memory_prompt(
            cwd,
            max_entrypoint_lines=settings.memory.max_entrypoint_lines,
            max_entrypoint_bytes=settings.memory.max_entrypoint_bytes,
        )
        if memory_section:
            sections.append(memory_section)

        if latest_user_prompt:
            relevant = select_relevant_memories(
                latest_user_prompt,
                cwd,
                max_results=settings.memory.max_files,
            )
            if relevant:
                try:
                    headers = [item.header for item in relevant]
                    mark_memory_used(cwd, headers, memory_dir=headers[0].path.parent)
                except OSError:
                    pass
                sections.append(format_relevant_memories(relevant))

    # Phase 3：求职领域动态上下文（RAG 检索 / 用户画像 / 投递状态概览）。
    # 各段独立预算（15% / 3% / 2%），联合预算 25%；优先级 RAG > 画像 > 投递，
    # 联合超限时按优先级从低到高截断（见 _apply_joint_budget）。
    window_tokens = _context_window_tokens(settings)
    jobhunt_sections = [
        section
        for section in (
            build_rag_context(settings, cwd=cwd, latest_user_prompt=latest_user_prompt),
            build_profile_context(
                settings, budget_tokens=int(window_tokens * _PROFILE_BUDGET_RATIO)
            ),
            build_application_status_context(
                settings, budget_tokens=int(window_tokens * _APPLICATIONS_BUDGET_RATIO)
            ),
        )
        if section
    ]
    sections.extend(
        _apply_joint_budget(
            jobhunt_sections,
            joint_budget_tokens=int(window_tokens * _JOINT_BUDGET_RATIO),
        )
    )

    return "\n\n".join(section for section in sections if section.strip())
