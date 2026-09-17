"""Tests for the RAG search tools and their registration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openharness.config.settings import Settings
from openharness.rag.retriever import Retriever
from openharness.rag.vectorstore import VectorStore
from openharness.tools import create_default_tool_registry
from openharness.tools.base import ToolExecutionContext
from openharness.tools.rag_search_tool import (
    RAGSearchInterviewTool,
    RAGSearchInterviewToolInput,
    RAGSearchJobsTool,
    RAGSearchJobsToolInput,
    RAGSearchTool,
    RAGSearchToolInput,
)


class BrokenRetriever:
    """Retriever stub that always fails, used to test tool error handling."""

    class _Store:
        backend_name = "broken"

    store = _Store()

    async def retrieve(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("boom")


@pytest.fixture
def retriever(seeded_store: VectorStore) -> Retriever:
    return Retriever(seeded_store, default_collection="jobs")


def _context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path)


# --------------------------------------------------------------------------- 注册


def test_rag_tools_are_registered_and_read_only() -> None:
    registry = create_default_tool_registry()
    for name in ("rag_search", "rag_search_jobs", "rag_search_interview"):
        tool = registry.get(name)
        assert tool is not None, name
        assert tool.is_read_only(None) is True


def test_tool_input_validation() -> None:
    assert RAGSearchToolInput(query="x").top_k == 5
    assert RAGSearchToolInput(query="x", collection="jobs").collection == "jobs"
    with pytest.raises(ValidationError):
        RAGSearchToolInput(query="x", top_k=0)
    with pytest.raises(ValidationError):
        RAGSearchToolInput(query="x", top_k=21)


# --------------------------------------------------------------------------- 执行


async def test_rag_search_executes_query(retriever: Retriever, tmp_path: Path) -> None:
    tool = RAGSearchTool(retriever=retriever)
    result = await tool.execute(
        RAGSearchToolInput(query="Python 后端开发", collection="jobs"), _context(tmp_path)
    )
    assert result.is_error is False
    assert result.metadata["result_count"] >= 1
    assert result.metadata["backend"] == "simple"
    assert "Found" in result.output


async def test_rag_search_defaults_to_configured_collection(
    retriever: Retriever, tmp_path: Path
) -> None:
    tool = RAGSearchTool(retriever=retriever)
    result = await tool.execute(RAGSearchToolInput(query="Python", top_k=2), _context(tmp_path))
    assert result.is_error is False
    assert result.metadata["collections"] == ["jobs"]
    assert result.metadata["result_count"] <= 2


async def test_rag_search_jobs_applies_filters(retriever: Retriever, tmp_path: Path) -> None:
    tool = RAGSearchJobsTool(retriever=retriever)
    beijing = await tool.execute(RAGSearchJobsToolInput(query="工程师", city="北京"), _context(tmp_path))
    assert beijing.is_error is False
    assert beijing.metadata["result_count"] == 2

    rich = await tool.execute(RAGSearchJobsToolInput(query="工程师", salary_min=50000), _context(tmp_path))
    assert rich.metadata["result_count"] == 1

    junior = await tool.execute(RAGSearchJobsToolInput(query="工程师", salary_max=20000), _context(tmp_path))
    assert junior.metadata["result_count"] == 1


async def test_rag_search_jobs_no_matches(retriever: Retriever, tmp_path: Path) -> None:
    tool = RAGSearchJobsTool(retriever=retriever)
    result = await tool.execute(RAGSearchJobsToolInput(query="工程师", city="广州"), _context(tmp_path))
    assert result.is_error is False
    assert result.metadata["result_count"] == 0
    assert result.output.startswith("No matches found")


async def test_rag_search_interview_filters_by_company(retriever: Retriever, tmp_path: Path) -> None:
    tool = RAGSearchInterviewTool(retriever=retriever)
    result = await tool.execute(
        RAGSearchInterviewToolInput(query="一面", company="字节跳动"), _context(tmp_path)
    )
    assert result.is_error is False
    assert result.metadata["result_count"] == 1
    assert "字节跳动" in result.output

    by_position = await tool.execute(
        RAGSearchInterviewToolInput(query="SQL", position="数据分析师"), _context(tmp_path)
    )
    assert by_position.metadata["result_count"] == 1


async def test_tools_report_retrieval_failures(tmp_path: Path) -> None:
    tool = RAGSearchTool(retriever=BrokenRetriever())  # type: ignore[arg-type]
    result = await tool.execute(RAGSearchToolInput(query="x"), _context(tmp_path))
    assert result.is_error is True
    assert "RAG search failed" in result.output


# --------------------------------------------------------------------------- 懒构建


async def test_tool_builds_retriever_lazily_from_settings(
    monkeypatch: pytest.MonkeyPatch, retriever: Retriever, tmp_path: Path
) -> None:
    import openharness.rag

    monkeypatch.setattr(
        openharness.rag, "build_retriever_from_settings", lambda settings, **kwargs: retriever
    )
    tool = RAGSearchTool(settings=Settings())
    result = await tool.execute(
        RAGSearchToolInput(query="Python", collection="jobs"), _context(tmp_path)
    )
    assert result.is_error is False
    assert result.metadata["result_count"] >= 1


async def test_tool_reports_factory_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import openharness.rag

    def _boom(settings: Any, **kwargs: Any) -> Retriever:
        del settings, kwargs
        raise RuntimeError("stack unavailable")

    monkeypatch.setattr(openharness.rag, "build_retriever_from_settings", _boom)
    tool = RAGSearchTool(settings=Settings())
    result = await tool.execute(RAGSearchToolInput(query="x"), _context(tmp_path))
    assert result.is_error is True
    assert "stack unavailable" in result.output


# --------------------------------------------------------------------------- 并发


async def test_rag_tools_run_concurrently(retriever: Retriever, tmp_path: Path) -> None:
    jobs_tool = RAGSearchJobsTool(retriever=retriever)
    interview_tool = RAGSearchInterviewTool(retriever=retriever)
    contexts = (_context(tmp_path), _context(tmp_path))
    jobs_result, interview_result = await asyncio.gather(
        jobs_tool.execute(RAGSearchJobsToolInput(query="Python"), contexts[0]),
        interview_tool.execute(RAGSearchInterviewToolInput(query="Python"), contexts[1]),
    )
    assert jobs_result.is_error is False
    assert interview_result.is_error is False
