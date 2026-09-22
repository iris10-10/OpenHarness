"""Dependency and persistence helpers for the job-hunt Web API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openharness.config.settings import load_settings
from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir

JOBS_FILENAME = "jobs.json"
RESUMES_FILENAME = "resumes.json"
CHAT_FILENAME = "chat_history.json"


def get_store() -> JobHuntStore:
    settings = load_settings()
    directory = resolve_jobhunt_dir(configured=settings.job_hunt.data_directory)
    probe = directory / ".web-write-probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        directory = Path.cwd() / ".openharness" / "jobhunt"
        directory.mkdir(parents=True, exist_ok=True)
    return JobHuntStore(directory)


def data_file(name: str) -> Path:
    return get_store().directory / name


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_jobs() -> list[dict[str, Any]]:
    return get_store().load_jobs()


def save_jobs(jobs: list[dict[str, Any]]) -> None:
    get_store().save_jobs(jobs)


def load_companies() -> list[dict[str, Any]]:
    return get_store().load_companies()


def save_companies(companies: list[dict[str, Any]]) -> None:
    get_store().save_companies(companies)


def load_resumes() -> list[dict[str, Any]]:
    payload = read_json_file(data_file(RESUMES_FILENAME), [])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def save_resumes(resumes: list[dict[str, Any]]) -> None:
    write_json_file(data_file(RESUMES_FILENAME), resumes)


def load_chat_history() -> list[dict[str, Any]]:
    payload = read_json_file(data_file(CHAT_FILENAME), [])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def save_chat_history(messages: list[dict[str, Any]]) -> None:
    write_json_file(data_file(CHAT_FILENAME), messages[-200:])
