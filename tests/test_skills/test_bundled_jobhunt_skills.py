"""Phase-3 bundled job-hunt skills: loading, trigger keywords, prompt listing.

验证 项目改造计划.md 7.3 / 7.4.3：六个求职技能以 ``content/<name>.md`` 平铺
布局交付，frontmatter 仅使用 name / description，触发关键词写入 description，
由 get_bundled_skills() 自动加载并进入系统提示的 "Available Skills" 列表。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.config.settings import Settings
from openharness.prompts.context import build_runtime_system_prompt
from openharness.skills import load_skill_registry
from openharness.skills.bundled import get_bundled_skills

#技能名 -> description 必须携带的触发关键词
JOBHUNT_SKILL_TRIGGERS: dict[str, tuple[str, ...]] = {
    "resume_optimize": ("简历优化", "STAR 法则", "ATS"),
    "interview_sim": ("模拟面试", "追问", "评估报告"),
    "job_strategy": ("求职策略", "市场分析", "Offer"),
    "system_design": ("系统设计", "分步引导", "评分标准"),
    "cs_knowledge": ("八股文", "分类", "答题模板"),
    "salary_negotiation": ("薪资谈判", "总包", "话术"),
}


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(home))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(home / "data"))
    monkeypatch.setenv("OPENHARNESS_JOBHUNT_DIR", str(home / "data" / "jobhunt"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)


def test_all_jobhunt_skills_are_bundled() -> None:
    skills = {skill.name: skill for skill in get_bundled_skills()}
    for name in JOBHUNT_SKILL_TRIGGERS:
        assert name in skills, f"{name} missing from bundled skills"
        skill = skills[name]
        assert skill.source == "bundled"
        assert skill.command_name == name
        # 允许模型自动触发，也允许用户以 /resume_optimize 直接调用
        assert skill.user_invocable is True
        assert skill.disable_model_invocation is False
        # 正文包含触发说明、流程与内联参考资料
        assert "何时使用" in skill.content


def test_jobhunt_skill_descriptions_carry_trigger_keywords() -> None:
    skills = {skill.name: skill for skill in get_bundled_skills()}
    for name, keywords in JOBHUNT_SKILL_TRIGGERS.items():
        description = skills[name].description
        for keyword in keywords:
            assert keyword in description, f"{name} description missing trigger {keyword!r}"


def test_bundled_frontmatter_uses_only_supported_fields() -> None:
    """求职技能 frontmatter 只允许 name / description（不存在 triggers 字段）。"""
    import yaml

    skills = {skill.name: skill for skill in get_bundled_skills()}
    for name in JOBHUNT_SKILL_TRIGGERS:
        content = skills[name].content
        assert content.startswith("---\n"), f"{name} lacks frontmatter"
        end = content.find("\n---\n", 4)
        assert end != -1, f"{name} frontmatter not terminated"
        frontmatter = yaml.safe_load(content[4:end])
        assert isinstance(frontmatter, dict)
        unknown = set(frontmatter) - {"name", "description"}
        assert not unknown, f"{name} has unsupported frontmatter fields: {unknown}"


def test_registry_lists_jobhunt_skills(tmp_path: Path) -> None:
    registry = load_skill_registry(str(tmp_path), settings=Settings())
    names = {skill.command_name or skill.name for skill in registry.list_skills()}
    assert set(JOBHUNT_SKILL_TRIGGERS) <= names


def test_runtime_prompt_lists_jobhunt_skills(tmp_path: Path) -> None:
    prompt = build_runtime_system_prompt(
        Settings(),
        cwd=tmp_path,
        latest_user_prompt="帮我优化一下简历",
        include_project_memory=False,
    )
    assert "# Available Skills" in prompt
    for name in JOBHUNT_SKILL_TRIGGERS:
        assert f"**{name}**" in prompt, f"{name} missing from Available Skills"
    # 触发关键词随 description 进入列表，供模型语义匹配
    assert "STAR 法则" in prompt
    assert "模拟面试" in prompt
