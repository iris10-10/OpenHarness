"""RAG knowledge search tools (read-only).

Three tools are registered in the default registry:

- ``rag_search``: generic hybrid search across any collection.
- ``rag_search_jobs``: job-posting search with city / salary / experience filters.
- ``rag_search_interview``: interview-experience search with company / position filters.

All tools are read-only. The retrieval stack is built lazily from settings on
first use; construction failures and retrieval errors are returned as tool
errors instead of raising so the agent loop keeps running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

if TYPE_CHECKING:
    from openharness.config.settings import Settings
    from openharness.rag.retriever import Retriever


class _RagSearchToolBase(BaseTool):
    """Shared plumbing for RAG search tools."""

    def __init__(
        self,
        retriever: Retriever | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._retriever = retriever
        self._settings = settings

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    def _resolve_retriever(self) -> Retriever:
        if self._retriever is not None:
            return self._retriever
        from openharness.rag import build_retriever_from_settings

        settings = self._settings
        if settings is None:
            from openharness.config.settings import load_settings

            settings = load_settings()
        return build_retriever_from_settings(settings)

    async def _run_search(
        self,
        *,
        query: str,
        collection: str | None,
        where: dict[str, Any] | None,
        top_k: int,
    ) -> ToolResult:
        """Run a retrieval and format it as a tool result."""
        from openharness.rag.retriever import format_retrieval_for_tool

        try:
            retriever = self._resolve_retriever()
            outcome = await retriever.retrieve(
                query, collection=collection, where=where, top_n=top_k
            )
        except Exception as exc:  # 工具调用失败不应中断 Agent 循环
            return ToolResult(output=f"RAG search failed: {exc}", is_error=True)
        metadata: dict[str, Any] = {
            "result_count": len(outcome.hits),
            "used_tokens": outcome.used_tokens,
            "budget_tokens": outcome.budget_tokens,
            "collections": outcome.collections,
        }
        backend_name = getattr(retriever.store, "backend_name", "")
        if backend_name:
            metadata["backend"] = backend_name
        return ToolResult(output=format_retrieval_for_tool(outcome), metadata=metadata)


class RAGSearchToolInput(BaseModel):
    """Arguments for the rag_search tool."""

    query: str = Field(description="Natural-language search query")
    collection: str | None = Field(
        default=None,
        description="Target collection: jobs / resumes / interview / companies / knowledge "
        "(defaults to the configured default collection)",
    )
    top_k: int = Field(default=5, ge=1, le=20, description="Maximum number of results")


class RAGSearchTool(_RagSearchToolBase):
    """Search the local RAG knowledge base with hybrid retrieval."""

    name = "rag_search"
    description = (
        "Search the local RAG knowledge base (job postings, resumes, interview notes, "
        "company profiles, general knowledge) with hybrid semantic + keyword retrieval. "
        "Read-only, fast and local; documents must be ingested first."
    )
    input_model = RAGSearchToolInput

    async def execute(self, arguments: RAGSearchToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        return await self._run_search(
            query=arguments.query,
            collection=arguments.collection,
            where=None,
            top_k=arguments.top_k,
        )


class RAGSearchJobsToolInput(BaseModel):
    """Arguments for the rag_search_jobs tool."""

    query: str = Field(description="What kind of jobs to look for (skills, role, keywords)")
    city: str | None = Field(default=None, description="Filter by city, e.g. 北京 / 上海")
    salary_min: int | None = Field(
        default=None,
        ge=0,
        description="Only jobs whose salary range can reach this monthly value (CNY)",
    )
    salary_max: int | None = Field(
        default=None,
        ge=0,
        description="Only jobs whose salary range starts at or below this monthly value (CNY)",
    )
    experience: str | None = Field(default=None, description="Filter by experience requirement")
    education: str | None = Field(default=None, description="Filter by education requirement")
    top_k: int = Field(default=5, ge=1, le=20, description="Maximum number of results")


class RAGSearchJobsTool(_RagSearchToolBase):
    """Search ingested job postings with structured filters."""

    name = "rag_search_jobs"
    description = (
        "Search ingested job postings (jobs collection) with hybrid retrieval plus "
        "city / salary / experience / education metadata filters. Read-only."
    )
    input_model = RAGSearchJobsToolInput

    async def execute(
        self, arguments: RAGSearchJobsToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        filters: dict[str, Any] = {}
        if arguments.city:
            filters["city"] = arguments.city
        if arguments.salary_min is not None:
            filters["salary_max"] = {"$gte": arguments.salary_min}
        if arguments.salary_max is not None:
            filters["salary_min"] = {"$lte": arguments.salary_max}
        if arguments.experience:
            filters["experience"] = arguments.experience
        if arguments.education:
            filters["education"] = arguments.education
        return await self._run_search(
            query=arguments.query,
            collection="jobs",
            where=filters or None,
            top_k=arguments.top_k,
        )


class RAGSearchInterviewToolInput(BaseModel):
    """Arguments for the rag_search_interview tool."""

    query: str = Field(description="Interview question or topic to search for")
    company: str | None = Field(default=None, description="Filter by company name")
    position: str | None = Field(default=None, description="Filter by position/role")
    top_k: int = Field(default=5, ge=1, le=20, description="Maximum number of results")


class RAGSearchInterviewTool(_RagSearchToolBase):
    """Search ingested interview notes with company / position filters."""

    name = "rag_search_interview"
    description = (
        "Search ingested interview experiences and question banks (interview collection) "
        "with hybrid retrieval plus company / position metadata filters. Read-only."
    )
    input_model = RAGSearchInterviewToolInput

    async def execute(
        self, arguments: RAGSearchInterviewToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        filters: dict[str, Any] = {}
        if arguments.company:
            filters["company"] = arguments.company
        if arguments.position:
            filters["position"] = arguments.position
        return await self._run_search(
            query=arguments.query,
            collection="interview",
            where=filters or None,
            top_k=arguments.top_k,
        )
