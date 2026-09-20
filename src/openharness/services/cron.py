"""Local cron-style registry helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from dateutil.tz import gettz

from openharness.config.paths import get_cron_registry_path
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

JOBHUNT_CRON_JOBS: tuple[dict[str, Any], ...] = (
    {
        "name": "jobhunt.daily-jobs",
        "schedule": "0 1 * * *",
        "command": "oh job-hunt cron run daily-jobs",
        "description": "每日岗位推送：按求职偏好检索新岗位（北京时间 9:00）",
        "payload": {"kind": "jobhunt", "task": "daily-jobs"},
    },
    {
        "name": "jobhunt.follow-ups",
        "schedule": "0 2 * * *",
        "command": "oh job-hunt cron run follow-ups",
        "description": "投递跟进提醒：检查超过跟进窗口的投递（北京时间 10:00）",
        "payload": {"kind": "jobhunt", "task": "follow-ups"},
    },
    {
        "name": "jobhunt.interview-digest",
        "schedule": "0 1 * * 1",
        "command": "oh job-hunt cron run interview-digest",
        "description": "面经更新推送：汇总目标公司的新面经（北京时间周一 9:00）",
        "payload": {"kind": "jobhunt", "task": "interview-digest"},
    },
    {
        "name": "jobhunt.data-sync",
        "schedule": "0 18 * * *",
        "command": "oh job-hunt cron run data-sync",
        "description": "数据同步：爬虫开启时增量采集新数据（北京时间 2:00）",
        "payload": {"kind": "jobhunt", "task": "data-sync"},
    },
)


def _cron_lock_path() -> Path:
    path = get_cron_registry_path()
    return path.with_suffix(path.suffix + ".lock")


def load_cron_jobs() -> list[dict[str, Any]]:
    """Load stored cron jobs."""
    path = get_cron_registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_cron_jobs(jobs: list[dict[str, Any]]) -> None:
    """Persist cron jobs to disk."""
    atomic_write_text(
        get_cron_registry_path(),
        json.dumps(jobs, indent=2) + "\n",
    )


def validate_cron_expression(expression: str) -> bool:
    """Return True if the expression is a valid cron schedule."""
    return croniter.is_valid(expression)


def _get_timezone(tz: str) -> Any:
    """Resolve an IANA timezone using system data or dateutil's fallback data."""
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        fallback = gettz(tz)
        if fallback is None:
            raise ZoneInfoNotFoundError(tz) from None
        return fallback


def validate_timezone(tz: str | None) -> bool:
    """Return True if *tz* is a valid IANA timezone or empty."""
    if not tz:
        return True
    try:
        _get_timezone(tz)
    except ZoneInfoNotFoundError:
        return False
    return True


def next_run_time(expression: str, base: datetime | None = None, tz: str | None = None) -> datetime:
    """Return the next run time for a cron expression.

    The returned datetime is always UTC. If *tz* is provided, the cron expression
    is interpreted in that IANA timezone.
    """
    base = base or datetime.now(timezone.utc)
    if tz:
        local_base = base.astimezone(_get_timezone(tz))
        local_next = croniter(expression, local_base).get_next(datetime)
        return local_next.astimezone(timezone.utc)
    return croniter(expression, base).get_next(datetime)


def upsert_cron_job(job: dict[str, Any]) -> None:
    """Insert or replace one cron job.

    Automatically sets ``enabled`` to True and computes ``next_run`` when the
    schedule is a valid cron expression.
    """
    job.setdefault("enabled", True)
    job.setdefault("created_at", datetime.now(timezone.utc).isoformat())

    schedule = job.get("schedule", "")
    if validate_cron_expression(schedule):
        job["next_run"] = next_run_time(schedule, tz=job.get("timezone") or job.get("tz")).isoformat()

    with exclusive_file_lock(_cron_lock_path()):
        jobs = [existing for existing in load_cron_jobs() if existing.get("name") != job.get("name")]
        jobs.append(job)
        jobs.sort(key=lambda item: str(item.get("name", "")))
        save_cron_jobs(jobs)


def delete_cron_job(name: str) -> bool:
    """Delete one cron job by name."""
    with exclusive_file_lock(_cron_lock_path()):
        jobs = load_cron_jobs()
        filtered = [job for job in jobs if job.get("name") != name]
        if len(filtered) == len(jobs):
            return False
        save_cron_jobs(filtered)
    return True


def get_cron_job(name: str) -> dict[str, Any] | None:
    """Return one cron job by name."""
    for job in load_cron_jobs():
        if job.get("name") == name:
            return job
    return None


def set_job_enabled(name: str, enabled: bool) -> bool:
    """Enable or disable a cron job. Returns False if job not found."""
    with exclusive_file_lock(_cron_lock_path()):
        jobs = load_cron_jobs()
        for job in jobs:
            if job.get("name") == name:
                job["enabled"] = enabled
                save_cron_jobs(jobs)
                return True
    return False


def mark_job_run(name: str, *, success: bool) -> None:
    """Update last_run and recompute next_run after a job executes."""
    with exclusive_file_lock(_cron_lock_path()):
        jobs = load_cron_jobs()
        now = datetime.now(timezone.utc)
        for job in jobs:
            if job.get("name") == name:
                job["last_run"] = now.isoformat()
                job["last_status"] = "success" if success else "failed"
                schedule = job.get("schedule", "")
                if validate_cron_expression(schedule):
                    job["next_run"] = next_run_time(schedule, now, tz=job.get("timezone") or job.get("tz")).isoformat()
                save_cron_jobs(jobs)
                return


def install_jobhunt_cron_jobs() -> list[str]:
    """Install the default job-hunt cron jobs and return their names."""
    names: list[str] = []
    for job in JOBHUNT_CRON_JOBS:
        upsert_cron_job(dict(job))
        names.append(str(job["name"]))
    return names


def run_jobhunt_cron_task(task: str) -> dict[str, Any]:
    """Run a lightweight job-hunt cron task implementation synchronously.

    The scheduler still owns process execution and notifications. This helper
    keeps the default job commands useful even without a running model session.
    """
    from openharness.config.settings import load_settings
    from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir

    settings = load_settings()
    directory = resolve_jobhunt_dir(configured=settings.job_hunt.data_directory)
    store = JobHuntStore(directory)

    if task == "daily-jobs":
        positions = settings.job_hunt.target_positions or ["工程师"]
        cities = settings.job_hunt.target_cities or ["不限"]
        return {
            "task": task,
            "status": "ok",
            "message": "岗位推送任务已生成检索条件",
            "queries": [
                {"position": position, "city": city}
                for position in positions
                for city in cities
            ],
        }
    if task == "follow-ups":
        from openharness.tools.application_tracker_tool import _TERMINAL_STATUSES, _days_since

        reminders: list[dict[str, Any]] = []
        follow_up_days = settings.job_hunt.reminder.follow_up_days
        for record in store.load_applications():
            if str(record.get("status", "")) in _TERMINAL_STATUSES:
                continue
            last = str(record.get("last_update_date") or record.get("applied_date") or "")
            age = _days_since(last) if last else None
            if age is not None and age >= follow_up_days:
                reminders.append(
                    {
                        "id": record.get("id", ""),
                        "company": record.get("company", ""),
                        "position": record.get("position", ""),
                        "days_since_update": age,
                    }
                )
        return {"task": task, "status": "ok", "reminders": reminders, "count": len(reminders)}
    if task == "interview-digest":
        profile = store.load_profile()
        preferences = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}
        companies = settings.job_hunt.default_company_types
        return {
            "task": task,
            "status": "ok",
            "message": "面经更新任务已准备",
            "target_positions": preferences.get("target_positions") or settings.job_hunt.target_positions,
            "target_company_types": companies,
        }
    if task == "data-sync":
        return {
            "task": task,
            "status": "skipped" if not settings.scraping.enabled else "ok",
            "scraping_enabled": settings.scraping.enabled,
            "message": "scraping.enabled=false，跳过采集" if not settings.scraping.enabled else "采集任务已准备",
        }
    raise ValueError(f"Unknown job-hunt cron task: {task}")
