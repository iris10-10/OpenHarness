"""Application tracking tools: create / update / list / remind (Phase 2).

The tracker keeps every job application on the local machine
(``applications.json`` under the job-hunt data directory) and enforces the
plan's status machine::

    已投递 → 简历筛选中 → 笔试 → 一面 → 二面 → HR面 → Offer → 已入职

Any active record may additionally jump to 已拒绝 at any point. Forward
skips (e.g. 已投递 → 一面) are allowed because some hiring processes omit
stages; backwards moves and edits to a terminal record (已入职 / 已拒绝)
are rejected.

``application_create`` / ``application_update`` write locally; the list and
reminder tools are read-only.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.storage import new_record_id, today_iso, utc_now_iso
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

APPLICATION_STATUSES: tuple[str, ...] = (
    "已投递",
    "简历筛选中",
    "笔试",
    "一面",
    "二面",
    "HR面",
    "Offer",
    "已入职",
    "已拒绝",
)

_PROGRESS_ORDER: tuple[str, ...] = (
    "已投递",
    "简历筛选中",
    "笔试",
    "一面",
    "二面",
    "HR面",
    "Offer",
    "已入职",
)
_REJECTED = "已拒绝"
_TERMINAL_STATUSES = ("已入职", "已拒绝")
_STATUS_INDEX = {status: index for index, status in enumerate(_PROGRESS_ORDER)}


def _transition_error(current: str, target: str) -> str | None:
    """Return an error message when ``current -> target`` is not allowed."""
    if target not in APPLICATION_STATUSES:
        return f"未知状态 '{target}'：可选 {' / '.join(APPLICATION_STATUSES)}"
    if current == target:
        return f"状态未变化：当前已经是 '{current}'"
    if current in _TERMINAL_STATUSES:
        return f"'{current}' 为终态，不能再变更状态；如需重新投递请创建新记录"
    if target == _REJECTED:
        return None
    if _STATUS_INDEX[target] <= _STATUS_INDEX[current]:
        return f"不允许状态回退：'{current}' 不能回到 '{target}'（仅支持向前推进或标记已拒绝）"
    return None


def _normalize_date(value: str, *, default: str) -> tuple[str, str | None]:
    """Validate/normalize a ``YYYY-MM-DD`` string; returns ``(date, error)``."""
    text = value.strip()
    if not text:
        return default, None
    try:
        return date.fromisoformat(text[:10]).isoformat(), None
    except ValueError:
        return "", f"日期格式无效：'{value}'（应为 YYYY-MM-DD）"


def _days_since(day: str) -> int | None:
    """Return the number of days between ``day`` and today, or None."""
    try:
        then = date.fromisoformat(day[:10])
    except ValueError:
        return None
    try:
        now = date.fromisoformat(today_iso())
    except ValueError:
        return None
    return (now - then).days


# ---------------------------------------------------------------------------
# application_create
# ---------------------------------------------------------------------------


class ApplicationCreateToolInput(BaseModel):
    """Arguments for the application_create tool."""

    company: str = Field(description="Company name")
    position: str = Field(description="Position / job title applied to")
    channel: str = Field(
        default="其他",
        description="Submission channel, e.g. 官网 / BOSS直聘 / 拉勾 / 内推",
    )
    applied_date: str = Field(
        default="", description="Application date YYYY-MM-DD (defaults to today)"
    )
    status: str = Field(
        default="已投递",
        description="Initial status, defaults 已投递; set explicitly when back-filling history",
    )
    notes: str = Field(default="", description="Free-form note recorded with the application")


class ApplicationCreateTool(JobHuntToolBase):
    """Record a new job application."""

    name = "application_create"
    description = (
        "Record a new job application (company / position / channel / date / status) "
        "in the local job-hunt tracker. Writes to the local job-hunt data directory."
    )
    input_model = ApplicationCreateToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    async def execute(
        self, arguments: ApplicationCreateToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        company = arguments.company.strip()
        position = arguments.position.strip()
        if not company or not position:
            return error_result("'company' 和 'position' 均为必填")
        if arguments.status not in APPLICATION_STATUSES:
            return error_result(
                f"未知状态 '{arguments.status}'：可选 {' / '.join(APPLICATION_STATUSES)}"
            )
        applied, date_error = _normalize_date(arguments.applied_date, default=today_iso())
        if date_error:
            return error_result(date_error)

        note = arguments.notes.strip()
        record: dict[str, Any] = {
            "id": new_record_id("app"),
            "company": company,
            "position": position,
            "channel": arguments.channel.strip() or "其他",
            "applied_date": applied,
            "status": arguments.status,
            "status_history": [{"status": arguments.status, "date": applied, "note": ""}],
            "notes": [note] if note else [],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "last_update_date": applied,
        }
        store = self.resolve_store(context)
        records = store.load_applications()
        records.append(record)
        store.save_applications(records)
        return ToolResult(
            output=json_output({"created": record, "total": len(records)}),
            metadata={"application_id": record["id"], "status": record["status"]},
        )


# ---------------------------------------------------------------------------
# application_update
# ---------------------------------------------------------------------------


class ApplicationUpdateToolInput(BaseModel):
    """Arguments for the application_update tool."""

    application_id: str = Field(description="Application id from application_create / application_list")
    status: str = Field(
        description=(
            "New status: 已投递 / 简历筛选中 / 笔试 / 一面 / 二面 / HR面 / Offer / "
            "已入职 / 已拒绝 (forward moves and 已拒绝 only)"
        )
    )
    note: str = Field(default="", description="Optional note recorded with this status change")
    updated_date: str = Field(
        default="", description="Effective date YYYY-MM-DD (defaults to today)"
    )


class ApplicationUpdateTool(JobHuntToolBase):
    """Advance an application's status with state-machine validation."""

    name = "application_update"
    description = (
        "Update one application's status with state-machine validation: forward "
        "transitions along 已投递 → 简历筛选中 → 笔试 → 一面 → 二面 → HR面 → Offer → "
        "已入职, plus 已拒绝 from any active state. Backwards moves and edits to "
        "terminal records are rejected. Writes locally."
    )
    input_model = ApplicationUpdateToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    async def execute(
        self, arguments: ApplicationUpdateToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        record_id = arguments.application_id.strip()
        store = self.resolve_store(context)
        records = store.load_applications()
        record = next((item for item in records if item.get("id") == record_id), None)
        if record is None:
            return error_result(f"未找到投递记录 '{record_id}'：请用 application_list 查看现有记录")
        current = str(record.get("status", ""))
        transition_error = _transition_error(current, arguments.status)
        if transition_error:
            return error_result(transition_error)
        effective, date_error = _normalize_date(arguments.updated_date, default=today_iso())
        if date_error:
            return error_result(date_error)

        note = arguments.note.strip()
        history = record.setdefault("status_history", [])
        history.append({"status": arguments.status, "date": effective, "note": note})
        if note:
            record.setdefault("notes", []).append(note)
        record["status"] = arguments.status
        record["updated_at"] = utc_now_iso()
        record["last_update_date"] = effective
        store.save_applications(records)
        return ToolResult(
            output=json_output({"updated": record}),
            metadata={"application_id": record_id, "status": arguments.status},
        )


# ---------------------------------------------------------------------------
# application_list
# ---------------------------------------------------------------------------


class ApplicationListToolInput(BaseModel):
    """Arguments for the application_list tool."""

    status: str = Field(default="", description="Filter by status (exact match)")
    company: str = Field(default="", description="Filter by company substring")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum records to return")


class ApplicationListTool(JobHuntToolBase):
    """List applications with filters and status statistics."""

    name = "application_list"
    description = (
        "List tracked job applications, optionally filtered by status / company, "
        "with status counts and the total / active breakdown. Read-only."
    )
    input_model = ApplicationListToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ApplicationListToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        records = self.resolve_store(context).load_applications()
        status_filter = arguments.status.strip()
        company_filter = arguments.company.strip()
        if status_filter and status_filter not in APPLICATION_STATUSES:
            return error_result(
                f"未知状态 '{status_filter}'：可选 {' / '.join(APPLICATION_STATUSES)}"
            )

        matched = [record for record in records if not _is_filtered_out(record, status_filter, company_filter)]
        matched.sort(key=lambda record: str(record.get("updated_at", "")), reverse=True)

        counts: dict[str, int] = {status: 0 for status in APPLICATION_STATUSES}
        for record in records:
            status = str(record.get("status", ""))
            if status in counts:
                counts[status] += 1
        active = sum(count for status, count in counts.items() if status not in _TERMINAL_STATUSES)

        return ToolResult(
            output=json_output(
                {
                    "summary": {
                        "total": len(records),
                        "active": active,
                        "by_status": counts,
                    },
                    "matched": len(matched),
                    "applications": matched[: arguments.limit],
                }
            ),
            metadata={"total": len(records), "matched": len(matched)},
        )


def _is_filtered_out(record: dict[str, Any], status: str, company: str) -> bool:
    if status and str(record.get("status", "")) != status:
        return True
    return bool(company and company not in str(record.get("company", "")))


# ---------------------------------------------------------------------------
# application_remind
# ---------------------------------------------------------------------------


class ApplicationRemindToolInput(BaseModel):
    """Arguments for the application_remind tool."""

    follow_up_days: int = Field(
        default=0,
        ge=0,
        le=90,
        description="Days without updates before a follow-up is suggested (0 = configured default)",
    )


class ApplicationRemindTool(JobHuntToolBase):
    """List active applications that need a follow-up."""

    name = "application_remind"
    description = (
        "List active applications that have not been updated for a while: "
        "follow-up candidates after job_hunt.reminder.follow_up_days and stalled "
        "ones after stale_days. Returns per-record waiting days with 待跟进 / 停滞 "
        "levels. Read-only."
    )
    input_model = ApplicationRemindToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ApplicationRemindToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        settings = self.job_hunt_settings().reminder
        follow_up_days = arguments.follow_up_days or settings.follow_up_days
        stale_days = settings.stale_days
        records = self.resolve_store(context).load_applications()

        reminders: list[dict[str, Any]] = []
        for record in records:
            status = str(record.get("status", ""))
            if status in _TERMINAL_STATUSES:
                continue
            last = str(record.get("last_update_date") or record.get("applied_date") or "")
            age = _days_since(last) if last else None
            if age is None:
                continue
            if age >= stale_days:
                level = "停滞"
            elif age >= follow_up_days:
                level = "待跟进"
            else:
                continue
            reminders.append(
                {
                    "id": record.get("id", ""),
                    "company": record.get("company", ""),
                    "position": record.get("position", ""),
                    "status": status,
                    "last_update_date": last,
                    "days_since_update": age,
                    "level": level,
                }
            )
        reminders.sort(key=lambda item: int(item["days_since_update"]), reverse=True)

        payload: dict[str, Any] = {
            "follow_up_days": follow_up_days,
            "stale_days": stale_days,
            "count": len(reminders),
            "reminders": reminders,
        }
        if not reminders:
            payload["note"] = "所有进行中的投递都在跟进窗口内，暂无需要催办或调整策略的记录"
        else:
            payload["hints"] = [
                "「待跟进」记录建议主动联系 HR / 招聘平台询问进度",
                "「停滞」记录建议准备备选机会，避免单纯等待",
            ]
        return ToolResult(
            output=json_output(payload),
            metadata={"reminder_count": len(reminders)},
        )
