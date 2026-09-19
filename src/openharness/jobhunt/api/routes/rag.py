"""RAG management endpoints used by the Web UI."""

from __future__ import annotations

from fastapi import APIRouter

from openharness.config.settings import load_settings
from openharness.jobhunt.api.deps import get_store, load_jobs, load_resumes

router = APIRouter(prefix="/rag", tags=["rag"])


@router.get("/status")
def status() -> dict[str, object]:
    settings = load_settings()
    return {
        "enabled": settings.rag.enabled,
        "persist_directory": settings.rag.persist_directory,
        "jobhunt_directory": str(get_store().directory),
        "collections": {
            "jobs": len(load_jobs()),
            "resumes": len(load_resumes()),
        },
    }
