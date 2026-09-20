"""Request models for the generic Web Agent API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """An optional image attachment sent with a user message."""

    media_type: str
    data: str
    source_path: str | None = None


class SessionCreateRequest(BaseModel):
    title: str = ""
    cwd: str | None = None
    model: str | None = None
    provider: str | None = None


class SessionPatchRequest(BaseModel):
    title: str | None = None


class MessageSendRequest(BaseModel):
    message: str
    attachments: list[Attachment] = Field(default_factory=list)
    cwd: str | None = None


class PermissionResponse(BaseModel):
    allowed: bool = False
    reply: Literal["once", "always", "reject"] | None = None


class AnswerResponse(BaseModel):
    answer: str = ""


class ErrorPayload(BaseModel):
    code: str
    message: str
    recoverable: bool = True


__all__ = [
    "AnswerResponse",
    "Attachment",
    "MessageSendRequest",
    "PermissionResponse",
    "SessionCreateRequest",
    "SessionPatchRequest",
]
