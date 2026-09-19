"""Shared plumbing for the Phase 2 job-hunt tools.

Job-hunt tools are thin adapters over :mod:`openharness.jobhunt`: parsing,
scoring and persistence live there; this module owns the cross-cutting
pieces so every tool behaves identically:

- :class:`JobHuntToolBase` — lazy settings / RAG retriever / job-hunt store
  resolution, mirroring the ``_RagSearchToolBase`` pattern so tool
  construction never touches the filesystem or the network.
- :func:`json_output` / :func:`error_result` — uniform tool output helpers.
- Candidate assembly shared by matching, gap analysis, cover-letter and
  career-planning tools.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from openharness.config.schema import JobHuntSettings
    from openharness.config.settings import Settings
    from openharness.jobhunt.scoring import CandidateProfile
    from openharness.jobhunt.storage import JobHuntStore
    from openharness.rag.retriever import Retriever

__all__ = ["JobHuntToolBase", "error_result", "json_output"]


def json_output(payload: Any) -> str:
    """Serialize a payload as pretty, non-ASCII-escaped JSON."""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def error_result(message: str) -> ToolResult:
    """Build a normalized tool error result."""
    return ToolResult(output=message, is_error=True)


class JobHuntToolBase(BaseTool):
    """Base class for job-hunt tools with lazy dependency resolution."""

    def __init__(
        self,
        retriever: Retriever | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._retriever = retriever
        self._settings = settings

    # --------------------------------------------------------------- 依赖解析

    def resolve_settings(self) -> Settings:
        """Return injected settings or load (and cache) them lazily."""
        if self._settings is not None:
            return self._settings
        from openharness.config.settings import load_settings

        settings = load_settings()
        self._settings = settings
        return settings

    def job_hunt_settings(self) -> JobHuntSettings:
        """Return the ``job_hunt`` configuration section."""
        return self.resolve_settings().job_hunt

    def resolve_retriever(self) -> Retriever:
        """Return the injected retriever or build one from settings."""
        if self._retriever is not None:
            return self._retriever
        from openharness.rag import build_retriever_from_settings

        retriever = build_retriever_from_settings(self.resolve_settings())
        self._retriever = retriever
        return retriever

    def resolve_store(self, context: ToolExecutionContext) -> JobHuntStore:
        """Resolve the job-hunt data directory for this invocation.

        Precedence: ``context.metadata["jobhunt_data_dir"]`` (tests and
        embedding hosts) > ``OPENHARNESS_JOBHUNT_DIR`` env var >
        ``job_hunt.data_directory`` setting > ``<data_dir>/jobhunt``.
        """
        from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir

        override = context.metadata.get("jobhunt_data_dir")
        directory = resolve_jobhunt_dir(
            override=override,
            configured=self.job_hunt_settings().data_directory,
        )
        return JobHuntStore(directory)

    # --------------------------------------------------------------- RAG 写入

    def ingest_document(
        self,
        *,
        collection: str,
        text: str,
        metadata: dict[str, Any],
        doc_id: str = "",
    ) -> str:
        """Store a document in a RAG collection; returns the stored id.

        Raises the underlying error — callers decide whether an ingestion
        failure should fail the tool call or only add a warning, so the main
        result (e.g. a parsed resume) is never lost because RAG is offline.
        """
        retriever = self.resolve_retriever()
        document: dict[str, Any] = {"text": text, "metadata": metadata}
        if doc_id:
            document["id"] = doc_id
        ids = retriever.store.add_documents(collection, [document])
        return ids[0] if ids else doc_id

    # --------------------------------------------------------------- 候选人构建

    @staticmethod
    def candidate_from_texts(
        resume_text: str | None,
        profile: dict[str, Any] | None = None,
    ) -> CandidateProfile:
        """Assemble a matching candidate from resume text and stored profile.

        When both are available the stored profile overlays the resume so
        explicit user preferences (cities, salary expectations) win.
        """
        from openharness.jobhunt.parsing import parse_resume_text
        from openharness.jobhunt.scoring import CandidateProfile

        profile = profile or {}
        if resume_text and resume_text.strip():
            candidate = CandidateProfile.from_resume(parse_resume_text(resume_text))
            if profile:
                candidate = candidate.merged_with_profile(profile)
            return candidate
        return CandidateProfile.from_profile_dict(profile)
