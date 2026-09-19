"""Tests for the user profile tools (profile_update / profile_query)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.user_profile_tool import (
    ProfileQueryTool,
    ProfileQueryToolInput,
    ProfileUpdateTool,
    ProfileUpdateToolInput,
)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_profile_update_then_query_roundtrip(settings, ctx) -> None:
    update = await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(
            updates={"basic": {"current_city": "杭州"}, "job_search_status": "积极找"}
        ),
        ctx,
    )

    assert update.is_error is False
    payload = _payload(update)
    assert payload["updated_keys"] == ["basic", "job_search_status"]
    assert payload["profile"]["basic"] == {"current_city": "杭州"}
    assert "warnings" not in payload

    query = await ProfileQueryTool(settings=settings).execute(ProfileQueryToolInput(), ctx)
    queried = _payload(query)
    assert queried["persisted"] is True
    assert queried["profile"]["job_search_status"] == "积极找"


@pytest.mark.asyncio
async def test_profile_update_deep_merges_and_flags_unknown_keys(settings, ctx) -> None:
    tool = ProfileUpdateTool(settings=settings)
    await tool.execute(
        ProfileUpdateToolInput(
            updates={"basic": {"current_city": "杭州", "target_cities": ["杭州"]}}
        ),
        ctx,
    )

    second = await tool.execute(
        ProfileUpdateToolInput(
            updates={"basic": {"target_cities": ["上海"]}, "favorites": ["某公司"]}
        ),
        ctx,
    )

    payload = _payload(second)
    assert payload["profile"]["basic"] == {"current_city": "杭州", "target_cities": ["上海"]}
    assert payload["profile"]["favorites"] == ["某公司"]
    warnings = payload["warnings"]
    assert len(warnings) == 1
    assert "未识别的顶层键" in warnings[0]
    assert "favorites" in warnings[0]


@pytest.mark.asyncio
async def test_profile_update_rejects_empty_updates(settings, ctx) -> None:
    result = await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates={}), ctx
    )

    assert result.is_error is True
    assert "'updates' must be a non-empty mapping" in result.output


@pytest.mark.asyncio
async def test_profile_query_without_profile_returns_hint(settings, ctx) -> None:
    result = await ProfileQueryTool(settings=settings).execute(ProfileQueryToolInput(), ctx)

    payload = _payload(result)
    assert payload["persisted"] is False
    assert payload["profile"] == {}
    assert "profile_update" in payload["hint"]


@pytest.mark.asyncio
async def test_profile_query_filters_sections_and_rejects_unknown(settings, ctx) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(
            updates={
                "basic": {"current_city": "杭州"},
                "preferences": {"expected_salary_min": 20000},
            }
        ),
        ctx,
    )

    filtered = await ProfileQueryTool(settings=settings).execute(
        ProfileQueryToolInput(sections=["preferences"]), ctx
    )
    payload = _payload(filtered)
    assert payload["profile"] == {"preferences": {"expected_salary_min": 20000}}

    bad = await ProfileQueryTool(settings=settings).execute(
        ProfileQueryToolInput(sections=["nope"]), ctx
    )
    assert bad.is_error is True
    assert "Unknown sections" in bad.output
