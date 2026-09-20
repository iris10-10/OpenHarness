"""FastAPI dependencies for Web Agent services."""

from __future__ import annotations

from fastapi import Request

from openharness.web.service import WebAgentService
from openharness.web.sessions import WebSessionManager


def get_web_session_manager(request: Request) -> WebSessionManager:
    return request.app.state.web_session_manager


def get_web_agent_service(request: Request) -> WebAgentService:
    return request.app.state.web_agent_service


__all__ = ["get_web_agent_service", "get_web_session_manager"]
