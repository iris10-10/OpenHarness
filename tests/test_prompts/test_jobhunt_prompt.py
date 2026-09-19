"""Phase-3 system-prompt identity tests: the agent is a career consultant.

验证 项目改造计划.md 7.4.1：身份从“编码助手”改造为“计算机行业求职顾问”，
并注入五条行为准则与新的语气 / 输出风格要求。
"""

from __future__ import annotations

from openharness.prompts.environment import EnvironmentInfo
from openharness.prompts.system_prompt import build_system_prompt, get_base_system_prompt


def _make_env(**overrides) -> EnvironmentInfo:
    defaults = dict(
        os_name="Linux",
        os_version="5.15.0",
        platform_machine="x86_64",
        shell="bash",
        cwd="/home/user/project",
        home_dir="/home/user",
        date="2026-09-19",
        python_version="3.10.17",
        python_executable="/home/user/.venv/bin/python",
        virtual_env="/home/user/.venv",
        is_git_repo=False,
        git_branch=None,
        hostname="testhost",
    )
    defaults.update(overrides)
    return EnvironmentInfo(**defaults)


def test_base_prompt_declares_career_consultant_identity() -> None:
    prompt = build_system_prompt(env=_make_env())
    # ohmo 引导提示词要求基础人设以 "You are OpenHarness" 开头
    assert prompt.startswith("You are OpenHarness")
    assert "求职顾问" in prompt
    # 领域知识：简历优化、面试技巧、职业规划、薪资谈判
    for keyword in ("简历优化", "面试技巧", "职业规划", "薪资谈判"):
        assert keyword in prompt


def test_base_prompt_contains_behavior_rules() -> None:
    prompt = get_base_system_prompt()
    # 1. 建议而非代劳（不代投简历）
    assert "建议而非代劳" in prompt
    assert "不代替用户投递简历" in prompt
    # 2. 诚实客观
    assert "诚实客观" in prompt
    # 3. 不造假经历
    assert "不造假" in prompt
    assert "编造工作经历" in prompt
    # 4. 隐私保护（默认脱敏）
    assert "隐私保护" in prompt
    assert "脱敏" in prompt
    # 5. 鼓励为主
    assert "鼓励为主" in prompt
    # 6. 数据驱动
    assert "数据驱动" in prompt


def test_base_prompt_forbids_acting_on_behalf_of_user() -> None:
    prompt = get_base_system_prompt()
    assert "投递简历、发送求职消息" in prompt
    assert "代替用户回复 HR" in prompt


def test_base_prompt_tone_and_output_style() -> None:
    prompt = get_base_system_prompt()
    # 语气：专业、鼓励、务实、有同理心
    assert "有同理心" in prompt
    # 输出偏好：结构化、可操作、有案例
    assert "结构化输出" in prompt
    assert "可操作" in prompt
    assert "有案例" in prompt


def test_base_prompt_directs_to_domain_tools_and_skills() -> None:
    prompt = get_base_system_prompt()
    # 引导优先使用求职工具与 skill 加载机制
    assert "专用工具" in prompt
    assert "`skill` 工具" in prompt


def test_coding_assistant_wording_is_gone() -> None:
    prompt = get_base_system_prompt()
    assert "coding assistant" not in prompt
    assert "software engineering tasks" not in prompt
