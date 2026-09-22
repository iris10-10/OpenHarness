"""Candidate profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from openharness.config.settings import load_settings
from openharness.jobhunt.api.deps import get_store
from openharness.jobhunt.api.schemas import ProfileUpdateRequest
from openharness.jobhunt.profile import (
    ai_visibility_summary,
    migrate_profile_store,
    profile_with_defaults,
)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("")
def get_profile() -> dict[str, object]:
    store = get_store()
    profile = migrate_profile_store(store, load_settings())
    return {
        "profile": profile_with_defaults(profile),
        "ai_visibility": ai_visibility_summary(profile),
    }


@router.get("/context-preview")
def get_context_preview() -> dict[str, object]:
    store = get_store()
    profile = migrate_profile_store(store, load_settings())
    return ai_visibility_summary(profile)


@router.put("")
def update_profile(request: ProfileUpdateRequest) -> dict[str, object]:
    store = get_store()
    migrate_profile_store(store, load_settings())
    merged = store.merge_profile(request.profile)
    merged = profile_with_defaults(merged)
    if merged != store.load_profile():
        store.save_profile(merged)
    return {"profile": merged, "ai_visibility": ai_visibility_summary(merged)}
