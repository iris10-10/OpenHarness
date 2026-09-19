"""User profile management tools: profile_update / profile_query.

The profile is the candidate's self-declared state of record and the baseline
for matching, gap analysis, resume generation and cover letters. It is stored
as JSON under the job-hunt data directory (see
:mod:`openharness.jobhunt.storage`) with the plan's structure::

    {
      "basic": {"current_city", "target_cities", "years_of_experience",
                 "current_title", "current_salary"},
      "skills": {"technical": [{"name", "level"}], "domains": [...]},
      "education": {"school", "degree", "major"},
      "preferences": {"target_positions", "expected_salary_min",
                       "expected_salary_max", "company_types", "work_mode"},
      "job_search_status": "积极找/观望/不找"
    }
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_PROFILE_SECTIONS = ("basic", "skills", "education", "preferences", "job_search_status")


class ProfileUpdateToolInput(BaseModel):
    """Arguments for the profile_update tool."""

    updates: dict[str, Any] = Field(
        description=(
            "Nested profile updates, deep-merged into the stored profile. "
            "Top-level keys: basic / skills / education / preferences / "
            "job_search_status. Example: "
            '{"basic": {"target_cities": ["北京"]}, '
            '"preferences": {"expected_salary_min": 20000}}'
        )
    )


class ProfileUpdateTool(JobHuntToolBase):
    """Deep-merge updates into the stored user profile."""

    name = "profile_update"
    description = (
        "Create or update the candidate's job-hunt profile with deep-merged nested "
        "updates (basic info, skills, education, preferences, job-search status). "
        "The merged profile is persisted locally and returned. Writes to the local "
        "job-hunt data directory."
    )
    input_model = ProfileUpdateToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    async def execute(
        self, arguments: ProfileUpdateToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not arguments.updates:
            return error_result("'updates' must be a non-empty mapping")
        unknown = [key for key in arguments.updates if key not in _PROFILE_SECTIONS]
        store = self.resolve_store(context)
        merged = store.merge_profile(arguments.updates)
        payload: dict[str, Any] = {
            "updated_keys": sorted(arguments.updates.keys()),
            "profile": merged,
        }
        if unknown:
            payload["warnings"] = [
                "未识别的顶层键："
                + "、".join(unknown)
                + f"（标准分节：{'、'.join(_PROFILE_SECTIONS)}），已按原样保存"
            ]
        return ToolResult(output=json_output(payload))


class ProfileQueryToolInput(BaseModel):
    """Arguments for the profile_query tool."""

    sections: list[str] = Field(
        default_factory=list,
        description=(
            "Subset of: basic / skills / education / preferences / "
            "job_search_status (default: all sections)"
        ),
    )


class ProfileQueryTool(JobHuntToolBase):
    """Read the stored user profile."""

    name = "profile_query"
    description = (
        "Return the stored candidate profile (basic info, skills, education, "
        "preferences, job-search status). Read-only."
    )
    input_model = ProfileQueryToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ProfileQueryToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        requested = [item.strip() for item in arguments.sections if item.strip()]
        unknown = [item for item in requested if item not in _PROFILE_SECTIONS]
        if unknown:
            return error_result(
                f"Unknown sections {unknown}: use {', '.join(_PROFILE_SECTIONS)}"
            )

        store = self.resolve_store(context)
        profile = store.load_profile()
        if not profile:
            return ToolResult(
                output=json_output(
                    {
                        "persisted": False,
                        "profile": {},
                        "hint": "尚未创建用户画像，请先调用 profile_update 写入基本信息",
                    }
                )
            )
        if requested:
            profile = {key: profile.get(key) for key in requested if key in profile}
        return ToolResult(output=json_output({"persisted": True, "profile": profile}))
