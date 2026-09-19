"""Candidate profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from openharness.jobhunt.api.deps import get_store
from openharness.jobhunt.api.schemas import ProfileUpdateRequest

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("")
def get_profile() -> dict[str, object]:
    return {"profile": get_store().load_profile()}


@router.put("")
def update_profile(request: ProfileUpdateRequest) -> dict[str, object]:
    return {"profile": get_store().merge_profile(request.profile)}
