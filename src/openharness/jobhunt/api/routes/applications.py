"""Application tracking endpoints."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from openharness.jobhunt.api.deps import get_store
from openharness.jobhunt.api.schemas import (
    ApplicationCreateRequest,
    ApplicationStatusRequest,
    ApplicationUpdateRequest,
)
from openharness.jobhunt.storage import new_record_id, today_iso, utc_now_iso
from openharness.tools.application_tracker_tool import APPLICATION_STATUSES

router = APIRouter(prefix="/applications", tags=["applications"])


WEB_STATUSES = ("待投递", "已投递", "笔试中", "面试中", "Offer", "已拒绝")
STATUS_ALIASES = {
    "待投递": "待投递",
    "已投递": "已投递",
    "简历筛选中": "已投递",
    "笔试": "笔试中",
    "笔试中": "笔试中",
    "一面": "面试中",
    "二面": "面试中",
    "HR面": "面试中",
    "面试中": "面试中",
    "Offer": "Offer",
    "已入职": "Offer",
    "已拒绝": "已拒绝",
}


def _records() -> list[dict[str, Any]]:
    return get_store().load_applications()


def _save(records: list[dict[str, Any]]) -> None:
    get_store().save_applications(records)


def _normalize_status(status: str) -> str:
    status = status.strip()
    if status in WEB_STATUSES or status in APPLICATION_STATUSES:
        return status
    raise HTTPException(status_code=400, detail=f"unknown status: {status}")


def _display_status(status: str) -> str:
    return STATUS_ALIASES.get(status, status)


def _stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(_display_status(str(item.get("status", ""))) for item in records)
    total = len(records)
    interviews = sum(counts[key] for key in ("面试中", "Offer"))
    offers = counts["Offer"]
    this_week = 0
    today = date.fromisoformat(today_iso())
    for item in records:
        try:
            applied = date.fromisoformat(str(item.get("applied_date", ""))[:10])
        except ValueError:
            continue
        if (today - applied).days <= 7:
            this_week += 1
    return {
        "total": total,
        "by_status": {status: counts[status] for status in WEB_STATUSES},
        "interview_rate": round(interviews / total * 100, 1) if total else 0,
        "offer_rate": round(offers / total * 100, 1) if total else 0,
        "this_week": this_week,
    }


@router.get("")
def list_applications(status: str = "", company: str = "") -> dict[str, object]:
    records = _records()
    if status:
        records = [item for item in records if _display_status(str(item.get("status", ""))) == status or item.get("status") == status]
    if company:
        records = [item for item in records if company in str(item.get("company", ""))]
    return {"items": records, "total": len(records), "stats": _stats(_records())}


@router.get("/stats")
def stats() -> dict[str, object]:
    records = _records()
    return {
        "summary": _stats(records),
        "trend": [
            {"date": item.get("applied_date", "")[:10], "count": 1, "status": _display_status(str(item.get("status", "")))}
            for item in records
        ],
    }


@router.post("")
def create(request: ApplicationCreateRequest) -> dict[str, object]:
    if not request.company.strip() or not request.position.strip():
        raise HTTPException(status_code=400, detail="company and position are required")
    status = _normalize_status(request.status)
    applied = request.applied_date.strip() or today_iso()
    record = {
        "id": new_record_id("app"),
        "company": request.company.strip(),
        "position": request.position.strip(),
        "channel": request.channel.strip() or "其他",
        "applied_date": applied,
        "status": status,
        "status_history": [{"status": status, "date": applied, "note": ""}],
        "notes": [request.notes.strip()] if request.notes.strip() else [],
        "tags": request.tags,
        "job_id": request.job_id,
        "follow_up_date": "",
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "last_update_date": applied,
    }
    records = [*_records(), record]
    _save(records)
    return {"application": record, "stats": _stats(records)}


@router.put("/{application_id}")
def update(application_id: str, request: ApplicationUpdateRequest) -> dict[str, object]:
    records = _records()
    record = next((item for item in records if str(item.get("id")) == application_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="application not found")
    for key in ("company", "position", "channel", "follow_up_date"):
        value = getattr(request, key)
        if value is not None:
            record[key] = value
    if request.notes is not None and request.notes.strip():
        record.setdefault("notes", []).append(request.notes.strip())
    if request.tags is not None:
        record["tags"] = request.tags
    record["updated_at"] = utc_now_iso()
    _save(records)
    return {"application": record, "stats": _stats(records)}


@router.patch("/{application_id}/status")
def update_status(application_id: str, request: ApplicationStatusRequest) -> dict[str, object]:
    records = _records()
    record = next((item for item in records if str(item.get("id")) == application_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="application not found")
    status = _normalize_status(request.status)
    effective = request.updated_date.strip() or today_iso()
    record["status"] = status
    record["last_update_date"] = effective
    record["updated_at"] = utc_now_iso()
    record.setdefault("status_history", []).append({"status": status, "date": effective, "note": request.note})
    if request.note:
        record.setdefault("notes", []).append(request.note)
    _save(records)
    return {"application": record, "stats": _stats(records)}


@router.delete("/{application_id}")
def delete(application_id: str) -> dict[str, object]:
    records = _records()
    kept = [item for item in records if str(item.get("id")) != application_id]
    if len(kept) == len(records):
        raise HTTPException(status_code=404, detail="application not found")
    _save(kept)
    return {"ok": True, "stats": _stats(kept)}
