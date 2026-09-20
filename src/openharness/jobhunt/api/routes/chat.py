"""Compatibility chat endpoints backed by the generic Web Agent service."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from openharness.jobhunt.api.schemas import ChatSendRequest
from openharness.web.service import WebAgentError, WebAgentService
from openharness.web.sessions import WebSessionManager
from openharness.web_api.deps import get_web_agent_service, get_web_session_manager

router = APIRouter(prefix="/chat", tags=["chat"])


def _event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/history")
async def history(
    session_id: str | None = None,
    manager: WebSessionManager = Depends(get_web_session_manager),
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, object]:
    if session_id:
        try:
            return {"session_id": session_id, "messages": service.session_messages(session_id)}
        except WebAgentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    sessions = manager.list_metadata()
    if not sessions:
        return {"messages": []}
    latest_id = str(sessions[0]["session_id"])
    return {"session_id": latest_id, "messages": service.session_messages(latest_id)}


@router.delete("/clear")
async def clear(
    session_id: str | None = None,
    manager: WebSessionManager = Depends(get_web_session_manager),
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, object]:
    if session_id:
        try:
            return await service.clear(session_id)
        except WebAgentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    sessions = manager.list_metadata()
    if sessions:
        try:
            return await service.clear(str(sessions[0]["session_id"]))
        except WebAgentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "messages": []}


@router.post("/stop")
async def stop(
    session_id: str | None = None,
    manager: WebSessionManager = Depends(get_web_session_manager),
    service: WebAgentService = Depends(get_web_agent_service),
) -> dict[str, object]:
    if session_id:
        try:
            return await service.cancel(session_id)
        except WebAgentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    sessions = manager.list_metadata()
    if not sessions:
        return {"ok": True, "cancelled": False}
    try:
        return await service.cancel(str(sessions[0]["session_id"]))
    except WebAgentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


async def _stream_legacy(
    service: WebAgentService,
    session_id: str,
    request: ChatSendRequest,
) -> AsyncIterator[str]:
    latest_message: dict[str, Any] | None = None
    try:
        async for event, payload in service.open_stream(
            session_id,
            request.message,
            attachments=request.attachments,
        ):
            if event == "user_message":
                yield _event("message", payload)
            elif event == "assistant_delta":
                yield _event(
                    "delta",
                    {
                        "id": payload.get("id"),
                        "delta": payload.get("message", ""),
                    },
                )
            elif event == "assistant_complete":
                latest_message = payload.get("message")
            else:
                yield _event(event, payload)
        if latest_message is not None:
            yield _event("done", {"session_id": session_id, "message": latest_message})
    except WebAgentError as exc:
        yield _event(
            "error",
            {"code": exc.code, "message": exc.message, "recoverable": exc.status_code < 500},
        )
        yield _event("done", {"session_id": session_id})


@router.post("/send")
async def send(
    request: ChatSendRequest,
    manager: WebSessionManager = Depends(get_web_session_manager),
    service: WebAgentService = Depends(get_web_agent_service),
) -> StreamingResponse:
    prompt = request.message.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="message must be non-empty")
    if request.session_id:
        session_id = request.session_id
        try:
            manager.metadata(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
    else:
        try:
            session = await manager.create_session(cwd=request.cwd, title=prompt[:80])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session_id = session.session_id
    return StreamingResponse(
        _stream_legacy(
            service,
            session_id,
            request,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
