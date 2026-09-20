"""FastAPI application factory for the job-hunt Web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from openharness.jobhunt.api.routes import (
    applications,
    chat,
    interview,
    jobs,
    profile,
    rag,
    resumes,
)
from openharness.web import WebAgentService, WebSessionManager
from openharness.web_api.routes.agent import router as agent_router


def create_app(*, static_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="OpenHarness Job Hunt Web API", version="0.2.0")
    web_session_manager = WebSessionManager()
    app.state.web_session_manager = web_session_manager
    app.state.web_agent_service = WebAgentService(web_session_manager)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "jobhunt-web"}

    api = FastAPI()
    api.state.web_session_manager = web_session_manager
    api.state.web_agent_service = app.state.web_agent_service
    api.include_router(chat.router)
    api.include_router(agent_router)
    api.include_router(jobs.router)
    api.include_router(applications.router)
    api.include_router(resumes.router)
    api.include_router(interview.router)
    api.include_router(profile.router)
    api.include_router(rag.router)
    app.mount("/api", api)

    @app.on_event("shutdown")
    async def shutdown_web_agent() -> None:
        await web_session_manager.shutdown()

    resolved_static = Path(static_dir) if static_dir else Path.cwd() / "jobhunt-web" / "dist"
    if resolved_static.exists():
        app.mount("/assets", StaticFiles(directory=resolved_static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = resolved_static / path
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(resolved_static / "index.html")

    return app


app = create_app()
