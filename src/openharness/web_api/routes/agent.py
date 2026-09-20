"""Generic OpenHarness Web Agent routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from openharness.web.service import WebAgentError, WebAgentService
from openharness.web.sessions import WebSessionManager
from openharness.web_api.deps import get_web_agent_service, get_web_session_manager
from openharness.web_api.schemas import (
    AnswerResponse,
    MessageSendRequest,
    PermissionResponse,
    SessionCreateRequest,
    SessionPatchRequest,
)

router = APIRouter(prefix="/agent", tags=["agent"])


def _sse(event: str, payload: dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _raise_http(exc: WebAgentError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    ) from exc


@router.get("/sessions")
async def list_sessions(
    manager: WebSessionManager = Depends(get_web_session_manager),
) -> dict[str, Any]:
    return {"sessions": manager.list_metadata()}


@router.post("/sessions")
async def create_session(
    request: SessionCreateRequest,
    manager: WebSessionManager = Depends(get_web_session_manager),
) -> dict[str, Any]:
    try:
        session = await manager.create_session(
            cwd=request.cwd,
            title=request.title,
            model=request.model,
        )
        return {"session": manager.metadata(session.session_id)}
    except ValueError as exc:
        _raise_http(WebAgentError("INVALID_CWD", str(exc)))
    except RuntimeError as exc:
        _raise_http(WebAgentError("RUNTIME_ERROR", str(exc), status_code=503))


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    manager: WebSessionManager = Depends(get_web_session_manager),
) -> dict[str, Any]:
    try:
        return {"session": manager.metadata(session_id)}
    except KeyError as exc:
        _raise_http(WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404))
        raise exc


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    request: SessionPatchRequest,
    manager: WebSessionManager = Depends(get_web_session_manager),
) -> dict[str, Any]:
    if request.title is None:
        try:
            return {"session": manager.metadata(session_id)}
        except KeyError as exc:
            _raise_http(WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404))
            raise exc
    try:
        return {"session": await manager.rename_session(session_id, request.title)}
    except KeyError as exc:
        _raise_http(WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404))
        raise exc


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    manager: WebSessionManager = Depends(get_web_session_manager),
) -> dict[str, Any]:
    try:
        return {"ok": await manager.delete_session(session_id), "session_id": session_id}
    except KeyError as exc:
        _raise_http(WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404))
        raise exc
    except RuntimeError as exc:
        _raise_http(WebAgentError("SESSION_BUSY", str(exc), status_code=409))


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, Any]:
    try:
        return {"messages": service.session_messages(session_id)}
    except WebAgentError as exc:
        _raise_http(exc)
        raise exc


async def _message_stream(
    service: WebAgentService,
    session_id: str,
    request: MessageSendRequest,
) -> AsyncIterator[str]:
    try:
        async for event, payload in service.open_stream(
            session_id,
            request.message,
            attachments=[item.model_dump(mode="json") for item in request.attachments],
        ):
            yield _sse(event, payload)
    except WebAgentError as exc:
        yield _sse(
            "error",
            {"code": exc.code, "message": exc.message, "recoverable": exc.status_code < 500},
        )
        yield _sse("done", {"session_id": session_id})


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    request: MessageSendRequest,
    service: WebAgentService = Depends(get_web_agent_service),
) -> StreamingResponse:
    return StreamingResponse(
        _message_stream(service, session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/cancel")
async def cancel_message(
    session_id: str,
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, Any]:
    try:
        return await service.cancel(session_id)
    except WebAgentError as exc:
        _raise_http(exc)
        raise exc


@router.post("/requests/{request_id}/permission")
async def answer_permission(
    request_id: str,
    request: PermissionResponse,
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, Any]:
    try:
        return await service.respond(
            request_id,
            allowed=request.allowed,
            reply=request.reply,
        )
    except WebAgentError as exc:
        _raise_http(exc)
        raise exc


@router.post("/requests/{request_id}/answer")
async def answer_question(
    request_id: str,
    request: AnswerResponse,
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, Any]:
    try:
        return await service.respond(request_id, answer=request.answer)
    except WebAgentError as exc:
        _raise_http(exc)
        raise exc


__all__ = ["router"]
