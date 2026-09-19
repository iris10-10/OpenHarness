"""Phase-3 job-hunt context injection tests: profile and application status.

验证 项目改造计划.md 7.4.2：用户画像与投递状态按触发条件注入 runtime
system prompt，并遵守 Token 预算（画像 ≤ 3%、投递 ≤ 2%、联合 ≤ 25%），
优先级 RAG 检索 > 用户画像 > 投递状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openharness.config.settings import Settings
from openharness.jobhunt.storage import JobHuntStore
from openharness.prompts.context import (
    _apply_joint_budget,
    build_application_status_context,
    build_profile_context,
    build_runtime_system_prompt,
)
from openharness.rag.utils import estimate_tokens

_PROFILE_BUDGET = int(16000 * 0.03)
_APPLICATIONS_BUDGET = int(16000 * 0.02)


@pytest.fixture
def settings() -> Settings:
    """Default settings (RAG disabled)."""
    return Settings()


@pytest.fixture
def jobhunt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate config / data / job-hunt directories inside tmp_path."""
    home = tmp_path / "home"
    directory = tmp_path / "jobhunt"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(home))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(home / "data"))
    monkeypatch.setenv("OPENHARNESS_JOBHUNT_DIR", str(directory))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _store(jobhunt_dir: Path) -> JobHuntStore:
    return JobHuntStore(jobhunt_dir)


def _sample_profile() -> dict[str, Any]:
    return {
        "basic": {
            "current_city": "北京",
            "target_cities": ["上海", "深圳"],
            "years_of_experience": 3,
            "current_title": "后端开发工程师",
            "current_salary": 20,
        },
        "skills": {
            "technical": [
                {"name": "Python", "level": "advanced"},
                {"name": "MySQL", "level": "intermediate"},
            ],
            "domains": ["电商", "金融"],
        },
        "education": {"school": "某某大学", "degree": "硕士", "major": "计算机科学"},
        "preferences": {
            "target_positions": ["后端开发", "基础设施"],
            "expected_salary_min": 25,
            "expected_salary_max": 40,
            "company_types": ["互联网大厂", "外企"],
            "work_mode": "混合",
        },
        "job_search_status": "积极找",
    }


def _sample_applications() -> list[dict[str, Any]]:
    return [
        {
            "id": "app-1",
            "company": "字节跳动",
            "position": "后端开发",
            "status": "一面",
            "applied_date": "2026-09-01",
            "last_update_date": "2026-09-15",
            "updated_at": "2026-09-15T10:00:00Z",
        },
        {
            "id": "app-2",
            "company": "美团",
            "position": "后端开发",
            "status": "已投递",
            "applied_date": "2026-09-10",
            "last_update_date": "2026-09-10",
            "updated_at": "2026-09-10T08:00:00Z",
        },
        {
            "id": "app-3",
            "company": "某小公司",
            "position": "测试工程师",
            "status": "已拒绝",
            "applied_date": "2026-08-01",
            "last_update_date": "2026-08-05",
            "updated_at": "2026-08-05T08:00:00Z",
        },
    ]


# ---------------------------------------------------------------- 画像注入


def test_profile_context_absent_without_profile(settings: Settings, jobhunt_dir: Path) -> None:
    assert build_profile_context(settings) is None


def test_profile_context_injected_when_stored(settings: Settings, jobhunt_dir: Path) -> None:
    _store(jobhunt_dir).merge_profile(_sample_profile())
    section = build_profile_context(settings)
    assert section is not None
    assert section.startswith("# Candidate Profile")
    assert "后端开发工程师" in section
    assert "3 年经验" in section
    assert "目标城市 上海、深圳" in section
    assert "Python(advanced)" in section
    assert "领域经验 电商、金融" in section
    assert "目标岗位 后端开发、基础设施" in section
    assert "期望月薪 25-40K" in section
    assert "混合" in section
    assert "积极找" in section


def test_profile_context_renders_partial_profile(settings: Settings, jobhunt_dir: Path) -> None:
    _store(jobhunt_dir).merge_profile({"basic": {"current_title": "前端工程师"}})
    section = build_profile_context(settings)
    assert section is not None
    assert "前端工程师" in section


# ------------------------------------------------------------- 投递状态注入


def test_application_context_absent_without_records(settings: Settings, jobhunt_dir: Path) -> None:
    assert build_application_status_context(settings) is None


def test_application_context_injected_when_records_exist(
    settings: Settings, jobhunt_dir: Path
) -> None:
    _store(jobhunt_dir).save_applications(_sample_applications())
    section = build_application_status_context(settings)
    assert section is not None
    assert section.startswith("# Application Status")
    assert "共 3 条投递" in section
    assert "进行中 2 条" in section
    # 最近动态按 updated_at 倒序，最靠前的是字节跳动
    assert section.index("字节跳动") < section.index("美团")
    assert "application_remind" in section


# ------------------------------------------------------------- Token 预算


def test_profile_context_respects_budget(settings: Settings, jobhunt_dir: Path) -> None:
    # 200 个技能条目足以击穿 3% 预算（480 token）
    technical = [{"name": f"技能关键词{i}", "level": "advanced"} for i in range(200)]
    _store(jobhunt_dir).merge_profile({"skills": {"technical": technical}})
    section = build_profile_context(settings, budget_tokens=_PROFILE_BUDGET)
    assert section is not None
    assert "已按 Token 预算截断" in section
    assert estimate_tokens(section) <= _PROFILE_BUDGET


def test_application_context_respects_budget(settings: Settings, jobhunt_dir: Path) -> None:
    records = []
    for index in range(200):
        records.append(
            {
                "company": f"某某科技有限公司{index}",
                "position": "后端开发工程师",
                "status": "已投递",
                "applied_date": "2026-09-01",
                "updated_at": f"2026-09-01T00:00:{index % 60:02d}Z",
            }
        )
    _store(jobhunt_dir).save_applications(records)
    section = build_application_status_context(settings, budget_tokens=_APPLICATIONS_BUDGET)
    assert section is not None
    assert estimate_tokens(section) <= _APPLICATIONS_BUDGET
    # 概要行优先保留：总数统计必须在截断后仍然可见
    assert "共 200 条投递" in section


def test_joint_budget_truncates_lowest_priority_first() -> None:
    high = "# Retrieved Knowledge\n" + "检索结果内容 " * 600  # ~3600 tokens
    mid = "# Candidate Profile\n" + "用户画像内容 " * 200  # ~1400 tokens
    low = "# Application Status\n" + "投递状态内容 " * 100  # ~700 tokens
    kept = _apply_joint_budget([high, mid, low], joint_budget_tokens=5000)
    # 高、中优先级完整保留，最低优先级（投递状态）被截断
    assert kept[0] == high
    assert kept[1] == mid
    assert len(kept) == 3
    assert "已按 Token 预算截断" in kept[2]
    assert estimate_tokens(kept[2]) <= 5000 - estimate_tokens(high) - estimate_tokens(mid)


def test_joint_budget_drops_sections_that_cannot_fit() -> None:
    high = "# Retrieved Knowledge\n" + "内容 " * 400
    mid = "# Candidate Profile\n" + "画像 " * 100
    kept = _apply_joint_budget([high, mid], joint_budget_tokens=estimate_tokens(high) + 10)
    # 剩余预算不足以放下画像段（< 32 token）时整段丢弃
    assert kept == [high]


def test_joint_budget_zero_returns_empty() -> None:
    assert _apply_joint_budget(["# A\n内容"], joint_budget_tokens=0) == []


# ------------------------------------------------------------- 集成注入


def test_runtime_prompt_includes_jobhunt_sections(
    settings: Settings, jobhunt_dir: Path, tmp_path: Path
) -> None:
    _store(jobhunt_dir).merge_profile(_sample_profile())
    _store(jobhunt_dir).save_applications(_sample_applications())

    prompt = build_runtime_system_prompt(
        settings,
        cwd=tmp_path,
        latest_user_prompt="帮我优化一下简历",
        include_project_memory=False,
    )
    assert "# Candidate Profile" in prompt
    assert "后端开发工程师" in prompt
    assert "# Application Status" in prompt
    assert "共 3 条投递" in prompt


def test_runtime_prompt_excludes_sections_without_data(
    settings: Settings, jobhunt_dir: Path, tmp_path: Path
) -> None:
    prompt = build_runtime_system_prompt(
        settings, cwd=tmp_path, latest_user_prompt="你好", include_project_memory=False
    )
    assert "# Candidate Profile" not in prompt
    assert "# Application Status" not in prompt
    assert prompt  # 提示词本身仍然组装成功


def test_runtime_prompt_survives_corrupt_jobhunt_files(
    settings: Settings, jobhunt_dir: Path, tmp_path: Path
) -> None:
    (jobhunt_dir / "profile.json").write_text("{broken json", encoding="utf-8")
    (jobhunt_dir / "applications.json").write_text("not json", encoding="utf-8")

    prompt = build_runtime_system_prompt(
        settings, cwd=tmp_path, latest_user_prompt="你好", include_project_memory=False
    )
    assert prompt
    assert "# Candidate Profile" not in prompt
    assert "# Application Status" not in prompt


def test_runtime_prompt_respects_configured_jobhunt_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """job_hunt.data_directory 配置生效（未设置环境变量覆盖时）。"""
    home = tmp_path / "home"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(home))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(home / "data"))
    monkeypatch.delenv("OPENHARNESS_JOBHUNT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)

    configured_dir = tmp_path / "configured"
    settings = Settings()
    settings.job_hunt.data_directory = str(configured_dir)
    _store(configured_dir).merge_profile({"basic": {"current_title": "算法工程师"}})

    prompt = build_runtime_system_prompt(
        settings, cwd=tmp_path, latest_user_prompt="你好", include_project_memory=False
    )
    assert "# Candidate Profile" in prompt
    assert "算法工程师" in prompt
