"""Persistent browser sessions and their runtime lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from openharness.api.usage import UsageSnapshot
from openharness.config.paths import get_data_dir
from openharness.config.settings import load_settings
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.services.session_storage import (
    _persistable_tool_metadata,
    _sanitize_snapshot_payload,
)
from openharness.services.session_backend import SessionBackend
from openharness.ui.runtime import RuntimeBundle, build_runtime, close_runtime, start_runtime
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text


MAX_SESSION_MESSAGES = 200


def _utc_timestamp() -> float:
    return time.time()


def _session_root() -> Path:
    configured = os.environ.get("OPENHARNESS_WEB_SESSIONS_DIR")
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [get_data_dir() / "web-sessions", Path.cwd() / ".openharness" / "web-sessions"]
    )
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return root
        except OSError:
            continue
    raise OSError("没有可写的 Web Agent 会话目录，请设置 OPENHARNESS_WEB_SESSIONS_DIR")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def _message_summary(messages: list[ConversationMessage]) -> str:
    for message in messages:
        if message.role == "user" and message.text.strip():
            return message.text.strip()[:80]
    return ""


def _message_count(messages: list[ConversationMessage]) -> int:
    return len([message for message in messages if message.text.strip() or message.tool_uses])


@dataclass
class WebSession:
    """A browser session and the in-memory runtime attached to it."""

    session_id: str
    title: str
    cwd: str
    model: str
    provider: str
    created_at: float
    updated_at: float
    bundle: RuntimeBundle | None = None
    active_task: Any = None
    event_queue: Any = None
    pending_requests: dict[str, Any] = field(default_factory=dict)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def busy(self) -> bool:
        return self.active_task is not None and not self.active_task.done()


class WebSessionStore:
    """Atomic, file-backed storage for independent Web Agent sessions."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else _session_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.index_lock_path = self.root / "index.json.lock"

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"session-{session_id}.json"

    def _load_index(self) -> list[dict[str, Any]]:
        payload = _read_json(self.index_path, [])
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _write_index(self, entries: list[dict[str, Any]]) -> None:
        atomic_write_text(
            self.index_path,
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        )

    def _upsert_index(self, metadata: dict[str, Any]) -> None:
        with exclusive_file_lock(self.index_lock_path):
            entries = self._load_index()
            entries = [
                item for item in entries if item.get("session_id") != metadata.get("session_id")
            ]
            entries.append(metadata)
            entries.sort(key=lambda item: float(item.get("updated_at", 0)), reverse=True)
            self._write_index(entries)

    def _remove_index(self, session_id: str) -> None:
        with exclusive_file_lock(self.index_lock_path):
            self._write_index(
                [item for item in self._load_index() if item.get("session_id") != session_id]
            )

    def list_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        entries = self._load_index()
        if not entries:
            for path in self.root.glob("session-*.json"):
                payload = _read_json(path, {})
                if isinstance(payload, dict) and payload.get("session_id"):
                    entries.append(self._metadata_from_payload(payload))
        entries.sort(key=lambda item: float(item.get("updated_at", 0)), reverse=True)
        return entries[: max(1, limit)]

    def load(self, session_id: str) -> dict[str, Any] | None:
        payload = _read_json(self._session_path(session_id), None)
        if not isinstance(payload, dict):
            return None
        return _sanitize_snapshot_payload(payload)

    def save(
        self,
        *,
        session_id: str,
        title: str,
        cwd: str,
        provider: str,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        tool_metadata: dict[str, object] | None = None,
        created_at: float | None = None,
    ) -> Path:
        sanitized_messages = sanitize_conversation_messages(messages)[-MAX_SESSION_MESSAGES:]
        now = _utc_timestamp()
        existing = self.load(session_id) or {}
        resolved_title = title.strip() or str(existing.get("title") or "").strip()
        if not resolved_title or resolved_title == "新建会话":
            resolved_title = _message_summary(sanitized_messages) or "新建会话"
        payload = {
            "session_id": session_id,
            "title": resolved_title,
            "cwd": str(Path(cwd).expanduser().resolve()),
            "provider": provider,
            "model": model,
            "system_prompt": system_prompt,
            "messages": [message.model_dump(mode="json") for message in sanitized_messages],
            "tool_metadata": _persistable_tool_metadata(tool_metadata),
            "usage": usage.model_dump(mode="json"),
            "created_at": created_at or existing.get("created_at") or now,
            "updated_at": now,
            "message_count": _message_count(sanitized_messages),
            "summary": _message_summary(sanitized_messages),
        }
        path = self._session_path(session_id)
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._upsert_index(self._metadata_from_payload(payload))
        return path

    def rename(self, session_id: str, title: str) -> dict[str, Any] | None:
        payload = self.load(session_id)
        if payload is None:
            return None
        payload["title"] = title.strip() or "新建会话"
        payload["updated_at"] = _utc_timestamp()
        atomic_write_text(
            self._session_path(session_id),
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        self._upsert_index(self._metadata_from_payload(payload))
        return payload

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        self._remove_index(session_id)
        return existed

    @staticmethod
    def _metadata_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": str(payload.get("session_id", "")),
            "title": str(payload.get("title") or payload.get("summary") or "新建会话"),
            "cwd": str(payload.get("cwd", "")),
            "provider": str(payload.get("provider", "")),
            "model": str(payload.get("model", "")),
            "message_count": int(payload.get("message_count", len(payload.get("messages", [])))),
            "created_at": payload.get("created_at", 0),
            "updated_at": payload.get("updated_at", payload.get("created_at", 0)),
        }


class WebSessionBackend(SessionBackend):
    """SessionBackend adapter that writes into the Web session namespace."""

    def __init__(self, store: WebSessionStore, session_id: str, *, title: str, provider: str) -> None:
        self.store = store
        self.session_id = session_id
        self.title = title
        self.provider = provider

    def get_session_dir(self, cwd: str | Path) -> Path:
        del cwd
        return self.store.root

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        return self.store.save(
            session_id=session_id or self.session_id,
            title=self.title,
            cwd=str(cwd),
            provider=self.provider,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            tool_metadata=tool_metadata,
        )

    def load_latest(self, cwd: str | Path) -> dict[str, Any] | None:
        del cwd
        return self.store.load(self.session_id)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        del cwd
        return self.store.list_sessions(limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict[str, Any] | None:
        del cwd
        return self.store.load(session_id)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        del cwd
        path = self.store.root / f"session-{self.session_id}.md"
        parts = ["# OpenHarness Web Agent Session"]
        for message in sanitize_conversation_messages(messages):
            text = message.text.strip()
            if text:
                parts.append(f"\n## {message.role.capitalize()}\n\n{text}")
        atomic_write_text(path, "\n".join(parts).strip() + "\n")
        return path


RuntimeBuilder = Callable[..., Awaitable[RuntimeBundle]]


class WebSessionManager:
    """Own WebSession objects and create/restore shared OpenHarness runtimes."""

    def __init__(
        self,
        *,
        store: WebSessionStore | None = None,
        runtime_builder: RuntimeBuilder | None = None,
    ) -> None:
        self.store = store or WebSessionStore()
        self.runtime_builder = runtime_builder or build_runtime
        self.sessions: dict[str, WebSession] = {}
        self.callback_factory: Callable[[WebSession], dict[str, object]] | None = None

    @staticmethod
    def allowed_cwd(cwd: str | None) -> str:
        candidate = Path(cwd or Path.cwd()).expanduser().resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError(f"工作目录不存在或不是目录: {candidate}")
        configured_roots = os.environ.get("OPENHARNESS_WEB_ALLOWED_ROOTS", "")
        roots = [
            Path(item).expanduser().resolve()
            for item in configured_roots.split(os.pathsep)
            if item.strip()
        ]
        if not roots:
            roots = [Path.cwd().resolve()]
        if not any(candidate == root or root in candidate.parents for root in roots):
            raise ValueError(f"工作目录不在 Web Agent 允许范围内: {candidate}")
        return str(candidate)

    async def create_session(
        self,
        *,
        cwd: str | None = None,
        title: str = "",
        model: str | None = None,
        active_profile: str | None = None,
    ) -> WebSession:
        settings = load_settings()
        resolved_cwd = self.allowed_cwd(cwd)
        session_id = f"session_{uuid4().hex[:12]}"
        session = WebSession(
            session_id=session_id,
            title=title.strip() or "新建会话",
            cwd=resolved_cwd,
            model=model or settings.model,
            provider=settings.provider or settings.api_format,
            created_at=_utc_timestamp(),
            updated_at=_utc_timestamp(),
        )
        self.sessions[session_id] = session
        self.store.save(
            session_id=session.session_id,
            title=session.title,
            cwd=session.cwd,
            provider=session.provider,
            model=session.model,
            system_prompt="",
            messages=[],
            usage=UsageSnapshot(),
            created_at=session.created_at,
        )
        return session

    async def get_or_restore(self, session_id: str) -> WebSession:
        active = self.sessions.get(session_id)
        if active is not None:
            if active.bundle is None:
                await self._build_runtime(active)
            return active
        payload = self.store.load(session_id)
        if payload is None:
            raise KeyError(session_id)
        session = WebSession(
            session_id=session_id,
            title=str(payload.get("title") or payload.get("summary") or "新建会话"),
            cwd=self.allowed_cwd(str(payload.get("cwd") or Path.cwd())),
            model=str(payload.get("model") or load_settings().model),
            provider=str(payload.get("provider") or ""),
            created_at=float(payload.get("created_at") or _utc_timestamp()),
            updated_at=float(payload.get("updated_at") or _utc_timestamp()),
        )
        self.sessions[session_id] = session
        try:
            await self._build_runtime(
                session,
                restore_messages=payload.get("messages") or [],
                restore_tool_metadata=payload.get("tool_metadata") or {},
            )
        except Exception:
            self.sessions.pop(session_id, None)
            raise
        return session

    async def _build_runtime(
        self,
        session: WebSession,
        *,
        active_profile: str | None = None,
        restore_messages: list[dict[str, Any]] | None = None,
        restore_tool_metadata: dict[str, object] | None = None,
    ) -> None:
        backend = WebSessionBackend(
            self.store,
            session.session_id,
            title=session.title,
            provider=session.provider,
        )
        callbacks = self.callback_factory(session) if self.callback_factory else {}
        try:
            bundle = await self.runtime_builder(
                session_id=session.session_id,
                cwd=session.cwd,
                model=session.model,
                active_profile=active_profile,
                restore_messages=restore_messages,
                restore_tool_metadata=restore_tool_metadata,
                session_backend=backend,
                permission_prompt=callbacks.get("permission_prompt"),
                ask_user_prompt=callbacks.get("ask_user_prompt"),
                edit_approval_prompt=callbacks.get("edit_approval_prompt"),
            )
        except SystemExit as exc:
            message = str(exc) or "当前没有配置模型认证，请先执行 oh setup 或 oh auth login"
            raise RuntimeError(f"AUTH_REQUIRED: {message}") from exc
        session.bundle = bundle
        session.model = bundle.engine.model
        state = bundle.app_state.get()
        session.provider = state.provider
        backend.provider = state.provider
        await start_runtime(bundle)

    async def close_session(self, session: WebSession) -> None:
        if session.active_task is not None and not session.active_task.done():
            session.active_task.cancel()
        for pending in list(session.pending_requests.values()):
            future = getattr(pending, "future", None)
            if future is not None and not future.done():
                future.cancel()
        if session.bundle is not None:
            await close_runtime(session.bundle)
            session.bundle = None

    async def delete_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is not None and session.busy:
            raise RuntimeError("会话正在运行中，无法删除")
        if session is not None:
            await self.close_session(session)
            self.sessions.pop(session_id, None)
        return self.store.delete(session_id)

    async def shutdown(self) -> None:
        for session in list(self.sessions.values()):
            await self.close_session(session)
        self.sessions.clear()

    def metadata(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is not None:
            payload = self.store.load(session_id) or {}
            metadata = self.store._metadata_from_payload(payload)
            metadata.update(
                {
                    "session_id": session.session_id,
                    "title": session.title,
                    "cwd": session.cwd,
                    "model": session.model,
                    "provider": session.provider,
                    "busy": session.busy,
                    "updated_at": session.updated_at,
                }
            )
            if session.bundle is not None:
                metadata["message_count"] = len(session.bundle.engine.messages)
            return metadata
        payload = self.store.load(session_id)
        if payload is None:
            raise KeyError(session_id)
        metadata = self.store._metadata_from_payload(payload)
        metadata["busy"] = False
        return metadata

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                **metadata,
                "busy": self.sessions.get(str(metadata.get("session_id"))).busy
                if self.sessions.get(str(metadata.get("session_id"))) is not None
                else False,
            }
            for metadata in self.store.list_sessions()
        ]

    async def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is not None:
            session.title = title.strip() or "新建会话"
            if session.bundle is not None:
                backend = session.bundle.session_backend
                if isinstance(backend, WebSessionBackend):
                    backend.title = session.title
            self.store.rename(session_id, session.title)
        elif self.store.rename(session_id, title) is None:
            raise KeyError(session_id)
        return self.metadata(session_id)

    def snapshot(self, session: WebSession) -> dict[str, Any] | None:
        return self.store.load(session.session_id)


__all__ = [
    "WebSession",
    "WebSessionBackend",
    "WebSessionManager",
    "WebSessionStore",
]
