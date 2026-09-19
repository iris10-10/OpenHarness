"""Skill-gap analysis tool: skill_gap_analyze (Phase 2, read-only).

Compares the candidate's skills (resume text and/or the stored profile)
against one job description and returns the deterministic breakdown from
:func:`openharness.jobhunt.scoring.analyze_skill_gap`: covered / partial /
missing required skills, missing preferred skills, a coverage ratio and
prioritized remediation actions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import parse_jd_text
from openharness.jobhunt.scoring import analyze_skill_gap
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_COVERAGE_LEVELS = ((0.8, "优秀"), (0.6, "良好"), (0.4, "一般"))


def _coverage_level(coverage: float) -> str:
    """Map a 0-1 coverage ratio to a human-readable level."""
    for threshold, label in _COVERAGE_LEVELS:
        if coverage >= threshold:
            return label
    return "偏低"


class SkillGapAnalyzeToolInput(BaseModel):
    """Arguments for the skill_gap_analyze tool."""

    jd_text: str = Field(description="Target job description text")
    resume_text: str | None = Field(
        default=None,
        description="Resume text; falls back to the stored user profile when omitted",
    )


class SkillGapAnalyzeTool(JobHuntToolBase):
    """Compare candidate skills against one job's requirements."""

    name = "skill_gap_analyze"
    description = (
        "Compare the candidate's skills (resume text and/or the stored user "
        "profile) against one job description and return covered / partial / "
        "missing required skills, missing preferred skills, a coverage ratio "
        "and prioritized remediation actions. Deterministic and read-only."
    )
    input_model = SkillGapAnalyzeToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: SkillGapAnalyzeToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not arguments.jd_text.strip():
            return error_result("'jd_text' must be non-empty")
        profile = self.resolve_store(context).load_profile()
        has_resume = bool(arguments.resume_text and arguments.resume_text.strip())
        if not has_resume and not profile:
            return error_result(
                "找不到候选人技能：请提供 resume_text，或先调用 profile_update 建立用户画像"
            )
        candidate = self.candidate_from_texts(arguments.resume_text, profile)
        if not candidate.skills:
            return error_result("未识别到任何技能：请检查 resume_text 或画像中的 skills.technical")

        job = parse_jd_text(arguments.jd_text)
        report = analyze_skill_gap(candidate.skills, job)
        payload: dict[str, Any] = {
            "job_summary": {
                "title": job.title,
                "company": job.company,
                "city": job.city,
                "category": job.category or "",
                "required_skills": list(job.required_skills),
                "preferred_skills": list(job.preferred_skills),
            },
            "candidate_skill_count": len(candidate.skills),
            "covered": list(report.covered),
            "partial": list(report.partial),
            "missing": list(report.missing),
            "missing_preferred": list(report.missing_preferred),
            "coverage": report.coverage,
            "coverage_level": _coverage_level(report.coverage),
            "priority_actions": list(report.priority_actions),
        }
        return ToolResult(
            output=json_output(payload),
            metadata={
                "coverage": report.coverage,
                "missing_count": len(report.missing) + len(report.missing_preferred),
            },
        )
