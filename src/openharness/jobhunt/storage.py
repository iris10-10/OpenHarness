"""Local-first JSON storage for job-hunt state (Phase 2).

All job-hunt state lives under ``<data_dir>/jobhunt/`` (default
``~/.openharness/data/jobhunt``), keeping private material such as the
candidate profile and application records on the local machine:

- ``profile.json``            求职者画像 (user_profile_tool)
- ``applications.json``       投递记录 (application_tracker_tool)
- ``interview_sessions.json`` 模拟面试会话 (interview_tool)

Writes are crash-safe (temp file + atomic rename) and serialised through an
exclusive lock so two concurrent ``oh`` processes cannot clobber each other's
read-modify-write cycles, mirroring the settings/credentials storage pattern.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

__all__ = [
    "JOBHUNT_DIR_ENV",
    "JobHuntStore",
    "deep_merge",
    "new_record_id",
    "resolve_jobhunt_dir",
    "today_iso",
    "utc_now_iso",
]

JOBHUNT_DIR_ENV = "OPENHARNESS_JOBHUNT_DIR"
_JOBHUNT_SUBDIR = "jobhunt"

PROFILE_FILENAME = "profile.json"
APPLICATIONS_FILENAME = "applications.json"
SESSIONS_FILENAME = "interview_sessions.json"
JOBS_FILENAME = "jobs.json"
COMPANIES_FILENAME = "companies.json"
SYNC_RUNS_FILENAME = "job_sync_runs.json"

#模拟面试会话的保留上限，避免文件无限增长
MAX_STORED_SESSIONS = 20


def resolve_jobhunt_dir(
    override: str | Path | None = None,
    *,
    configured: str = "",
) -> Path:
    """Resolve the job-hunt data directory and ensure it exists.

    Precedence: explicit ``override`` (tests / tool context) >
    ``OPENHARNESS_JOBHUNT_DIR`` env var > ``job_hunt.data_directory`` setting
    (when non-empty) > ``~/.openharness/data/jobhunt``.
    """
    if override is not None:
        target = Path(override)
    else:
        env_dir = os.environ.get(JOBHUNT_DIR_ENV, "").strip()
        if env_dir:
            target = Path(env_dir)
        elif configured.strip():
            target = Path(configured.strip()).expanduser()
        else:
            from openharness.config.paths import get_data_dir

            target = get_data_dir() / _JOBHUNT_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 (seconds precision)."""
    #datetime.UTC 别名需要 Python 3.11+，项目仍支持 3.10，故沿用 timezone.utc
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def today_iso() -> str:
    """Return today's local date as ``YYYY-MM-DD``."""
    return datetime.now().astimezone().date().isoformat()


def new_record_id(prefix: str) -> str:
    """Return a short unique record id such as ``app-3f9c2b1d``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``updates`` into ``base``.

    Nested dicts merge key by key; all other values (including lists) are
    replaced wholesale, so callers can reset a list by passing a new one.
    Neither argument is mutated.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in updates.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _read_json(path: Path, default: Any) -> Any:
    """Load JSON from ``path``; missing or corrupt files fall back to default."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    """Atomically persist ``payload`` guarded by an exclusive lock."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    with exclusive_file_lock(lock_path):
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class JobHuntStore:
    """JSON-file backed store rooted at a job-hunt data directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- 路径

    @property
    def profile_path(self) -> Path:
        return self.directory / PROFILE_FILENAME

    @property
    def applications_path(self) -> Path:
        return self.directory / APPLICATIONS_FILENAME

    @property
    def sessions_path(self) -> Path:
        return self.directory / SESSIONS_FILENAME

    @property
    def jobs_path(self) -> Path:
        """Return the local canonical job snapshot path."""
        return self.directory / JOBS_FILENAME

    @property
    def companies_path(self) -> Path:
        """Return the derived company library snapshot path."""
        return self.directory / COMPANIES_FILENAME

    @property
    def sync_runs_path(self) -> Path:
        """Return the bounded synchronization audit history path."""
        return self.directory / SYNC_RUNS_FILENAME

    # -------------------------------------------------------------- 画像

    def load_profile(self) -> dict[str, Any]:
        """Return the stored candidate profile (empty dict when unset)."""
        payload = _read_json(self.profile_path, {})
        return payload if isinstance(payload, dict) else {}

    def save_profile(self, profile: dict[str, Any]) -> None:
        _write_json(self.profile_path, profile)

    def merge_profile(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge ``updates`` into the stored profile and persist it."""
        merged = deep_merge(self.load_profile(), updates)
        self.save_profile(merged)
        return merged

    # -------------------------------------------------------------- 投递

    def load_applications(self) -> list[dict[str, Any]]:
        """Return the stored application records (empty list when unset)."""
        payload = _read_json(self.applications_path, [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def save_applications(self, applications: list[dict[str, Any]]) -> None:
        _write_json(self.applications_path, applications)

    # -------------------------------------------------------------- 会话

    def load_sessions(self) -> list[dict[str, Any]]:
        """Return stored interview-practice sessions (newest first)."""
        payload = _read_json(self.sessions_path, [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def save_sessions(self, sessions: list[dict[str, Any]]) -> None:
        #只保留最近 N 个会话，列表约定为 newest first
        _write_json(self.sessions_path, sessions[:MAX_STORED_SESSIONS])

    # -------------------------------------------------------------- 岗位快照

    def load_jobs(self) -> list[dict[str, Any]]:
        """Return canonical job snapshots, ignoring malformed entries."""
        payload = _read_json(self.jobs_path, [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def save_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Persist canonical job snapshots atomically."""
        _write_json(self.jobs_path, jobs)

    def load_companies(self) -> list[dict[str, Any]]:
        """Return derived company library records."""
        payload = _read_json(self.companies_path, [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def save_companies(self, companies: list[dict[str, Any]]) -> None:
        """Persist derived company library records atomically."""
        _write_json(self.companies_path, companies)

    def load_sync_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return newest synchronization audit records."""
        payload = _read_json(self.sync_runs_path, [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)][: max(1, limit)]

    def append_sync_run(self, run: dict[str, Any]) -> None:
        """Append a redacted synchronization audit record."""
        runs = [run, *self.load_sync_runs(limit=99)]
        _write_json(self.sync_runs_path, runs[:100])
