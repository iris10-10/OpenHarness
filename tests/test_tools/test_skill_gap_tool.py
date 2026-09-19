"""Tests for the skill_gap_analyze tool (deterministic, read-only)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.skill_gap_tool import SkillGapAnalyzeTool, SkillGapAnalyzeToolInput
from openharness.tools.user_profile_tool import ProfileUpdateTool, ProfileUpdateToolInput


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_skill_gap_full_coverage_from_resume(
    jd_text, resume_text, settings, ctx
) -> None:
    result = await SkillGapAnalyzeTool(settings=settings).execute(
        SkillGapAnalyzeToolInput(jd_text=jd_text, resume_text=resume_text), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["coverage"] == 1.0
    assert payload["coverage_level"] == "优秀"
    assert payload["covered"] == ["Python", "FastAPI", "MySQL", "Redis"]
    assert payload["partial"] == []
    assert payload["missing"] == []
    assert payload["missing_preferred"] == []
    assert payload["priority_actions"] == []
    assert payload["candidate_skill_count"] == 6
    assert payload["job_summary"]["title"] == "Python 后端开发工程师"
    assert payload["job_summary"]["required_skills"] == ["Python", "FastAPI", "MySQL", "Redis"]
    assert result.metadata == {"coverage": 1.0, "missing_count": 0}


@pytest.mark.asyncio
async def test_skill_gap_uses_stored_profile_when_resume_missing(
    jd_text, profile_dict, settings, ctx
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )

    result = await SkillGapAnalyzeTool(settings=settings).execute(
        SkillGapAnalyzeToolInput(jd_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["coverage"] == 1.0
    assert payload["candidate_skill_count"] == 6
    assert payload["missing"] == []


@pytest.mark.asyncio
async def test_skill_gap_reports_missing_preferred_actions(jd_text, settings, ctx) -> None:
    result = await SkillGapAnalyzeTool(settings=settings).execute(
        SkillGapAnalyzeToolInput(
            jd_text=jd_text, resume_text="熟悉 Python、FastAPI、MySQL、Redis，有三年经验"
        ),
        ctx,
    )

    payload = _payload(result)
    assert payload["coverage"] == 1.0
    assert payload["missing_preferred"] == ["Docker", "Kubernetes"]
    assert len(payload["priority_actions"]) == 2
    assert all(action.startswith("加分项") for action in payload["priority_actions"])


@pytest.mark.asyncio
async def test_skill_gap_requires_candidate_source(jd_text, settings, ctx) -> None:
    result = await SkillGapAnalyzeTool(settings=settings).execute(
        SkillGapAnalyzeToolInput(jd_text=jd_text), ctx
    )

    assert result.is_error is True
    assert "找不到候选人技能" in result.output


@pytest.mark.asyncio
async def test_skill_gap_rejects_empty_jd(resume_text, settings, ctx) -> None:
    result = await SkillGapAnalyzeTool(settings=settings).execute(
        SkillGapAnalyzeToolInput(jd_text="  ", resume_text=resume_text), ctx
    )

    assert result.is_error is True
    assert "'jd_text' must be non-empty" in result.output
