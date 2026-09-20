"""Web Agent orchestration over the shared OpenHarness runtime."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import uuid4

from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock
from openharness.engine.query import MaxTurnsExceeded
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.web.sessions import WebSession, WebSessionManager


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
)


@dataclass
class PendingWebRequest:
    """A browser response awaited by a running Agent task."""

    request_id: str
    session_id: str
    kind: str
    payload: dict[str, Any]
    future: asyncio.Future[Any]
    created_at: float


class WebAgentError(RuntimeError):
    """An error that can be mapped to a stable Web API response."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _redact(text: str) -> str:
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", result)
    return result


def _message_payload(message: ConversationMessage, message_id: str) -> dict[str, Any]:
    tools = [
        {
            "name": block.name,
            "status": "started",
            "input": block.input,
        }
        for block in message.tool_uses
    ]
    return {
        "id": message_id,
        "role": message.role,
        "content": _redact(message.text),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tools": tools,
    }


class WebAgentService:
    """Translate QueryEngine stream events into browser-safe SSE events."""

    def __init__(self, manager: WebSessionManager) -> None:
        self.manager = manager
        self.manager.callback_factory = self._callbacks_for_session

    def _callbacks_for_session(self, session: WebSession) -> dict[str, object]:
        return {
            "permission_prompt": lambda tool_name, reason: self._ask_permission(
                session, tool_name, reason
            ),
            "ask_user_prompt": lambda question: self._ask_question(session, question),
            "edit_approval_prompt": lambda path, diff, added, removed: self._ask_edit(
                session, path, diff, added, removed
            ),
        }

    async def _create_request(
        self,
        session: WebSession,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> PendingWebRequest:
        queue = session.event_queue
        if queue is None:
            raise WebAgentError("SESSION_NOT_RUNNING", "当前会话没有正在运行的 Agent 任务")
        loop = asyncio.get_running_loop()
        request = PendingWebRequest(
            request_id=f"req_{uuid4().hex[:12]}",
            session_id=session.session_id,
            kind=kind,
            payload=payload,
            future=loop.create_future(),
            created_at=time.time(),
        )
        session.pending_requests[request.request_id] = request
        await queue.put(
            (
                "permission_required" if kind in {"permission", "edit"} else "question_required",
                {"request_id": request.request_id, **payload},
            )
        )
        return request

    async def _await_request(self, session: WebSession, request: PendingWebRequest) -> Any:
        timeout = float(os.environ.get("OPENHARNESS_WEB_REQUEST_TIMEOUT", "600"))
        try:
            return await asyncio.wait_for(asyncio.shield(request.future), timeout=timeout)
        except asyncio.TimeoutError:
            if not request.future.done():
                request.future.cancel()
            return False if request.kind == "permission" else "reject" if request.kind == "edit" else ""
        finally:
            session.pending_requests.pop(request.request_id, None)

    async def _ask_permission(self, session: WebSession, tool_name: str, reason: str) -> bool:
        request = await self._create_request(
            session,
            kind="permission",
            payload={"tool_name": tool_name, "reason": _redact(reason)},
        )
        response = await self._await_request(session, request)
        if isinstance(response, dict):
            return bool(response.get("allowed"))
        return bool(response)

    async def _ask_question(self, session: WebSession, question: str) -> str:
        request = await self._create_request(
            session,
            kind="question",
            payload={"question": _redact(question)},
        )
        response = await self._await_request(session, request)
        return str(response or "")

    async def _ask_edit(
        self,
        session: WebSession,
        path: str,
        diff: str,
        added: int,
        removed: int,
    ) -> str:
        request = await self._create_request(
            session,
            kind="edit",
            payload={
                "tool_name": "file_edit",
                "path": path,
                "diff": _redact(diff),
                "added": added,
                "removed": removed,
                "reason": "即将修改项目文件，请确认 Diff 后再执行。",
            },
        )
        response = await self._await_request(session, request)
        if isinstance(response, dict):
            return str(response.get("reply") or ("once" if response.get("allowed") else "reject"))
        return str(response or "reject")

    async def respond(
        self,
        request_id: str,
        *,
        allowed: bool | None = None,
        reply: str | None = None,
        answer: str | None = None,
    ) -> dict[str, Any]:
        for session in self.manager.sessions.values():
            request = session.pending_requests.get(request_id)
            if request is None:
                continue
            if request.future.done():
                raise WebAgentError("REQUEST_ALREADY_RESOLVED", "这个请求已经处理过了", status_code=409)
            if request.kind == "question":
                request.future.set_result(answer or "")
            elif request.kind == "edit":
                normalized = (reply or ("once" if allowed else "reject")).strip().lower()
                if normalized not in {"once", "always", "reject"}:
                    raise WebAgentError("INVALID_REPLY", "编辑确认只能是 once、always 或 reject")
                request.future.set_result({"allowed": normalized != "reject", "reply": normalized})
            else:
                request.future.set_result(bool(allowed))
            return {"ok": True, "request_id": request_id}
        raise WebAgentError("REQUEST_NOT_FOUND", "权限或问答请求不存在", status_code=404)

    async def _persist(self, session: WebSession) -> None:
        bundle = session.bundle
        if bundle is None:
            return
        bundle.session_backend.save_snapshot(
            cwd=bundle.cwd,
            model=bundle.engine.model,
            system_prompt=bundle.engine.system_prompt,
            messages=bundle.engine.messages,
            usage=bundle.engine.total_usage,
            session_id=session.session_id,
            tool_metadata=bundle.engine.tool_metadata,
        )
        session.updated_at = time.time()

    async def _put(self, session: WebSession, event: str, payload: dict[str, Any]) -> None:
        if session.event_queue is not None:
            await session.event_queue.put((event, payload))

    async def _run(
        self,
        session: WebSession,
        prompt: str,
        attachments: list[dict[str, Any]],
        user_message_id: str,
        assistant_message_id: str,
    ) -> None:
        bundle = session.bundle
        if bundle is None:
            raise WebAgentError("RUNTIME_UNAVAILABLE", "当前会话运行时尚未建立", status_code=503)

        try:
            await self._put(
                session,
                "user_message",
                {
                    "message": {
                        "id": user_message_id,
                        "role": "user",
                        "content": prompt,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "tools": [],
                    }
                },
            )
            user_content = [TextBlock(text=prompt)]
            for attachment in attachments:
                media_type = str(attachment.get("media_type") or "")
                data = str(attachment.get("data") or "")
                if media_type.startswith("image/") and data:
                    user_content.append(ImageBlock(media_type=media_type, data=data))
            user_message = ConversationMessage.from_user_content(user_content)
            async for event in bundle.engine.submit_message(user_message):
                await self._emit_engine_event(session, event, assistant_message_id)
            await self._persist(session)
        except asyncio.CancelledError:
            await self._put(
                session,
                "error",
                {"code": "CANCELLED", "message": "当前 Agent 任务已停止", "recoverable": True},
            )
            await self._persist(session)
        except MaxTurnsExceeded as exc:
            await self._put(
                session,
                "error",
                {
                    "code": "MAX_TURNS_EXCEEDED",
                    "message": f"Agent 已达到本轮最大执行轮数（{exc.max_turns}）",
                    "recoverable": True,
                },
            )
            await self._persist(session)
        except WebAgentError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._put(
                session,
                "error",
                {"code": "AGENT_ERROR", "message": _redact(str(exc)), "recoverable": True},
            )
            await self._persist(session)
        finally:
            await self._put(session, "done", {"session_id": session.session_id})
            session.event_queue = None
            session.active_task = None

    async def _emit_engine_event(
        self,
        session: WebSession,
        event: StreamEvent,
        assistant_message_id: str,
    ) -> None:
        if isinstance(event, AssistantTextDelta):
            await self._put(
                session,
                "assistant_delta",
                {"id": assistant_message_id, "message": _redact(event.text)},
            )
        elif isinstance(event, AssistantTurnComplete):
            await self._put(
                session,
                "assistant_complete",
                {"message": _message_payload(event.message, assistant_message_id)},
            )
        elif isinstance(event, ToolExecutionStarted):
            await self._put(
                session,
                "tool_started",
                {
                    "tool_name": event.tool_name,
                    "tool_input": event.tool_input,
                    "started_at": time.time(),
                },
            )
        elif isinstance(event, ToolExecutionCompleted):
            await self._put(
                session,
                "tool_completed",
                {
                    "tool_name": event.tool_name,
                    "output": _redact(event.output),
                    "is_error": event.is_error,
                    "metadata": event.metadata or {},
                },
            )
        elif isinstance(event, ErrorEvent):
            await self._put(
                session,
                "error",
                {
                    "code": "AGENT_ERROR",
                    "message": _redact(event.message),
                    "recoverable": event.recoverable,
                },
            )
        elif isinstance(event, StatusEvent):
            await self._put(session, "status", {"message": _redact(event.message)})
        elif isinstance(event, CompactProgressEvent):
            await self._put(
                session,
                "status",
                {
                    "message": _redact(event.message or event.phase),
                    "phase": event.phase,
                    "trigger": event.trigger,
                },
            )

    async def open_stream(
        self,
        session_id: str,
        prompt: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        prompt = prompt.strip()
        if not prompt:
            raise WebAgentError("INVALID_MESSAGE", "message must be non-empty")
        try:
            session = await self.manager.get_or_restore(session_id)
        except KeyError as exc:
            raise WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404) from exc
        except ValueError as exc:
            raise WebAgentError("INVALID_CWD", str(exc), status_code=400) from exc
        except RuntimeError as exc:
            message = str(exc)
            code = "AUTH_REQUIRED" if message.startswith("AUTH_REQUIRED:") else "RUNTIME_ERROR"
            raise WebAgentError(code, message.removeprefix("AUTH_REQUIRED:").strip(), status_code=503) from exc

        async with session.state_lock:
            if session.busy:
                raise WebAgentError("SESSION_BUSY", "当前会话正在运行中，请先停止当前任务", status_code=409)
            session.event_queue = asyncio.Queue()
            session.active_task = asyncio.create_task(
                self._run(
                    session,
                    prompt,
                    attachments or [],
                    f"msg_{uuid4().hex[:12]}",
                    f"msg_{uuid4().hex[:12]}",
                )
            )
            queue = session.event_queue
            task = session.active_task

        completed = False
        try:
            while True:
                event, payload = await queue.get()
                yield event, payload
                if event == "done":
                    completed = True
                    break
        finally:
            if not completed and task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def cancel(self, session_id: str) -> dict[str, Any]:
        try:
            session = await self.manager.get_or_restore(session_id)
        except KeyError as exc:
            raise WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404) from exc
        task = session.active_task
        if task is None or task.done():
            return {"ok": True, "cancelled": False, "session_id": session_id}
        task.cancel()
        return {"ok": True, "cancelled": True, "session_id": session_id}

    async def clear(self, session_id: str) -> dict[str, Any]:
        session = await self.manager.get_or_restore(session_id)
        if session.busy:
            raise WebAgentError("SESSION_BUSY", "当前会话正在运行中", status_code=409)
        if session.bundle is not None:
            session.bundle.engine.clear()
            await self._persist(session)
        return {"ok": True, "messages": []}

    def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        payload = self.manager.store.load(session_id)
        if payload is None:
            raise WebAgentError("SESSION_NOT_FOUND", "会话不存在", status_code=404)
        raw_messages = payload.get("messages") or []
        try:
            messages = [ConversationMessage.model_validate(item) for item in raw_messages]
        except Exception as exc:  # pragma: no cover - defensive corrupted data path
            raise WebAgentError("SESSION_CORRUPT", "会话消息无法读取", status_code=500) from exc
        result: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if message.role == "user" and not message.text.strip():
                continue
            result.append(
                _message_payload(message, f"{session_id}-message-{index}")
            )
        return result


__all__ = ["PendingWebRequest", "WebAgentError", "WebAgentService"]
