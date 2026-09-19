"""Tests for the application tracking tools (create / update / list / remind)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from openharness.jobhunt.storage import JobHuntStore, today_iso
from openharness.tools.application_tracker_tool import (
    ApplicationCreateTool,
    ApplicationCreateToolInput,
    ApplicationListTool,
    ApplicationListToolInput,
    ApplicationRemindTool,
    ApplicationRemindToolInput,
    ApplicationUpdateTool,
    ApplicationUpdateToolInput,
)
from openharness.tools.base import ToolResult


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


def _days_ago(days: int) -> str:
    return (date.fromisoformat(today_iso()) - timedelta(days=days)).isoformat()


async def _create(settings, ctx, **kwargs: Any) -> tuple[dict[str, Any], ToolResult]:
    fields: dict[str, Any] = {
        "company": "杭州星辰科技有限公司",
        "position": "Python 后端开发工程师",
        **kwargs,
    }
    result = await ApplicationCreateTool(settings=settings).execute(
        ApplicationCreateToolInput(**fields), ctx
    )
    return _payload(result)["created"], result


# ---------------------------------------------------------------------------
# application_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_application_create_defaults_and_persistence(settings, ctx) -> None:
    created, result = await _create(settings, ctx)

    assert result.is_error is False
    assert created["id"].startswith("app-")
    assert created["company"] == "杭州星辰科技有限公司"
    assert created["position"] == "Python 后端开发工程师"
    assert created["channel"] == "其他"
    assert created["status"] == "已投递"
    assert created["applied_date"] == today_iso()
    assert created["status_history"] == [
        {"status": "已投递", "date": today_iso(), "note": ""}
    ]
    assert created["notes"] == []
    assert created["last_update_date"] == today_iso()
    assert result.metadata == {"application_id": created["id"], "status": "已投递"}

    stored = JobHuntStore(ctx.cwd).load_applications()
    assert len(stored) == 1
    assert stored[0]["id"] == created["id"]


@pytest.mark.asyncio
async def test_application_create_validation_errors(settings, ctx) -> None:
    result = await ApplicationCreateTool(settings=settings).execute(
        ApplicationCreateToolInput(company="  ", position="后端"), ctx
    )
    assert result.is_error is True
    assert "'company' 和 'position' 均为必填" in result.output

    result = await ApplicationCreateTool(settings=settings).execute(
        ApplicationCreateToolInput(company="A", position="B", status="面试中"), ctx
    )
    assert result.is_error is True
    assert "未知状态 '面试中'" in result.output

    result = await ApplicationCreateTool(settings=settings).execute(
        ApplicationCreateToolInput(company="A", position="B", applied_date="2026-13-40"), ctx
    )
    assert result.is_error is True
    assert "日期格式无效" in result.output


@pytest.mark.asyncio
async def test_application_create_backfills_history(settings, ctx) -> None:
    created, _ = await _create(
        settings,
        ctx,
        applied_date="2026-08-20",
        status="一面",
        channel="内推",
        notes="补录：已过初面",
    )

    assert created["status"] == "一面"
    assert created["applied_date"] == "2026-08-20"
    assert created["channel"] == "内推"
    assert created["status_history"] == [{"status": "一面", "date": "2026-08-20", "note": ""}]
    assert created["notes"] == ["补录：已过初面"]
    assert created["last_update_date"] == "2026-08-20"


# ---------------------------------------------------------------------------
# application_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_application_update_forward_and_skip_ahead(settings, ctx) -> None:
    created, _ = await _create(settings, ctx)
    application_id = created["id"]

    result = await ApplicationUpdateTool(settings=settings).execute(
        ApplicationUpdateToolInput(
            application_id=application_id, status="简历筛选中", note="HR 已收简历"
        ),
        ctx,
    )

    assert result.is_error is False
    payload = _payload(result)
    updated = payload["updated"]
    assert updated["status"] == "简历筛选中"
    assert len(updated["status_history"]) == 2
    assert updated["status_history"][-1] == {
        "status": "简历筛选中",
        "date": today_iso(),
        "note": "HR 已收简历",
    }
    assert updated["notes"] == ["HR 已收简历"]
    assert updated["last_update_date"] == today_iso()
    assert result.metadata == {"application_id": application_id, "status": "简历筛选中"}

    # 跨级前移（跳过笔试 / 一面）被允许：流程可能省略阶段
    result = await ApplicationUpdateTool(settings=settings).execute(
        ApplicationUpdateToolInput(application_id=application_id, status="HR面"), ctx
    )
    payload = _payload(result)
    assert payload["updated"]["status"] == "HR面"
    assert len(payload["updated"]["status_history"]) == 3


@pytest.mark.asyncio
async def test_application_update_rejects_backward_same_and_terminal(settings, ctx) -> None:
    created, _ = await _create(settings, ctx)
    application_id = created["id"]
    tool = ApplicationUpdateTool(settings=settings)

    await tool.execute(
        ApplicationUpdateToolInput(application_id=application_id, status="一面"), ctx
    )

    backward = await tool.execute(
        ApplicationUpdateToolInput(application_id=application_id, status="已投递"), ctx
    )
    assert backward.is_error is True
    assert "不允许状态回退" in backward.output

    same = await tool.execute(
        ApplicationUpdateToolInput(application_id=application_id, status="一面"), ctx
    )
    assert same.is_error is True
    assert "状态未变化" in same.output

    rejected = await tool.execute(
        ApplicationUpdateToolInput(application_id=application_id, status="已拒绝"), ctx
    )
    assert rejected.is_error is False
    assert _payload(rejected)["updated"]["status"] == "已拒绝"

    terminal = await tool.execute(
        ApplicationUpdateToolInput(application_id=application_id, status="Offer"), ctx
    )
    assert terminal.is_error is True
    assert "'已拒绝' 为终态" in terminal.output


@pytest.mark.asyncio
async def test_application_update_unknown_id_status_and_date(settings, ctx) -> None:
    tool = ApplicationUpdateTool(settings=settings)

    missing = await tool.execute(
        ApplicationUpdateToolInput(application_id="app-missing", status="一面"), ctx
    )
    assert missing.is_error is True
    assert "未找到投递记录" in missing.output

    created, _ = await _create(settings, ctx)
    unknown = await tool.execute(
        ApplicationUpdateToolInput(application_id=created["id"], status="面试中"), ctx
    )
    assert unknown.is_error is True
    assert "未知状态 '面试中'" in unknown.output

    bad_date = await tool.execute(
        ApplicationUpdateToolInput(
            application_id=created["id"], status="一面", updated_date="08/20/2026"
        ),
        ctx,
    )
    assert bad_date.is_error is True
    assert "日期格式无效" in bad_date.output


# ---------------------------------------------------------------------------
# application_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_application_list_summary_and_filters(settings, ctx) -> None:
    created_a, _ = await _create(settings, ctx, company="A 公司", position="后端")
    created_b, _ = await _create(settings, ctx, company="B 公司", position="前端")
    created_c, _ = await _create(settings, ctx, company="C 公司", position="测试")

    updater = ApplicationUpdateTool(settings=settings)
    await updater.execute(
        ApplicationUpdateToolInput(application_id=created_b["id"], status="一面"), ctx
    )
    await updater.execute(
        ApplicationUpdateToolInput(application_id=created_c["id"], status="已拒绝"), ctx
    )

    result = await ApplicationListTool(settings=settings).execute(
        ApplicationListToolInput(), ctx
    )
    payload = _payload(result)
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["active"] == 2
    assert payload["summary"]["by_status"]["已投递"] == 1
    assert payload["summary"]["by_status"]["一面"] == 1
    assert payload["summary"]["by_status"]["已拒绝"] == 1
    assert payload["matched"] == 3
    assert len(payload["applications"]) == 3
    # 按 updated_at 倒序（时间戳为秒级，同秒内稳定排序保持写入顺序）
    updated_at = [application["updated_at"] for application in payload["applications"]]
    assert updated_at == sorted(updated_at, reverse=True)
    assert {application["id"] for application in payload["applications"]} == {
        created_a["id"],
        created_b["id"],
        created_c["id"],
    }
    assert result.metadata == {"total": 3, "matched": 3}

    by_status = await ApplicationListTool(settings=settings).execute(
        ApplicationListToolInput(status="一面"), ctx
    )
    payload = _payload(by_status)
    assert payload["matched"] == 1
    assert payload["applications"][0]["company"] == "B 公司"

    by_company = await ApplicationListTool(settings=settings).execute(
        ApplicationListToolInput(company="C 公司"), ctx
    )
    payload = _payload(by_company)
    assert payload["matched"] == 1
    assert payload["applications"][0]["id"] == created_c["id"]

    limited = await ApplicationListTool(settings=settings).execute(
        ApplicationListToolInput(limit=2), ctx
    )
    payload = _payload(limited)
    assert payload["matched"] == 3
    assert len(payload["applications"]) == 2

    unknown = await ApplicationListTool(settings=settings).execute(
        ApplicationListToolInput(status="面试中"), ctx
    )
    assert unknown.is_error is True
    assert "未知状态 '面试中'" in unknown.output


# ---------------------------------------------------------------------------
# application_remind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_application_remind_levels_and_windows(settings, ctx) -> None:
    await _create(settings, ctx, company="停滞公司", applied_date=_days_ago(20))
    await _create(settings, ctx, company="跟进公司", applied_date=_days_ago(10))
    await _create(settings, ctx, company="新投公司", applied_date=_days_ago(1))
    await _create(
        settings,
        ctx,
        company="拒绝公司",
        applied_date=_days_ago(30),
        status="已拒绝",
    )

    result = await ApplicationRemindTool(settings=settings).execute(
        ApplicationRemindToolInput(), ctx
    )
    payload = _payload(result)
    assert payload["follow_up_days"] == 7
    assert payload["stale_days"] == 14
    assert payload["count"] == 2
    assert [
        (item["company"], item["level"], item["days_since_update"])
        for item in payload["reminders"]
    ] == [("停滞公司", "停滞", 20), ("跟进公司", "待跟进", 10)]
    assert payload["hints"]
    assert "note" not in payload
    assert result.metadata == {"reminder_count": 2}

    # follow_up_days 参数覆盖默认窗口：1 天前的投递也进入待跟进
    wider = await ApplicationRemindTool(settings=settings).execute(
        ApplicationRemindToolInput(follow_up_days=1), ctx
    )
    payload = _payload(wider)
    assert payload["follow_up_days"] == 1
    assert payload["count"] == 3
    assert payload["reminders"][-1]["company"] == "新投公司"
    assert payload["reminders"][-1]["level"] == "待跟进"


@pytest.mark.asyncio
async def test_application_remind_within_window_note(settings, ctx) -> None:
    await _create(settings, ctx, applied_date=_days_ago(1))

    result = await ApplicationRemindTool(settings=settings).execute(
        ApplicationRemindToolInput(), ctx
    )
    payload = _payload(result)
    assert payload["count"] == 0
    assert payload["reminders"] == []
    assert "所有进行中的投递都在跟进窗口内" in payload["note"]
    assert "hints" not in payload
    assert result.metadata == {"reminder_count": 0}


def test_application_tools_read_only_flags() -> None:
    assert (
        ApplicationCreateTool().is_read_only(
            ApplicationCreateToolInput(company="A", position="B")
        )
        is False
    )
    assert (
        ApplicationUpdateTool().is_read_only(
            ApplicationUpdateToolInput(application_id="app-1", status="一面")
        )
        is False
    )
    assert ApplicationListTool().is_read_only(ApplicationListToolInput()) is True
    assert ApplicationRemindTool().is_read_only(ApplicationRemindToolInput()) is True
