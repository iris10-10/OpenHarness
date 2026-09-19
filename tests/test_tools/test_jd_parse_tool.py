"""Tests for the jd_parse tool (parsing + optional RAG ingestion)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.jd_parse_tool import JDParseTool, JDParseToolInput


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


def test_jd_parse_read_only_toggle(settings) -> None:
    tool = JDParseTool(settings=settings)

    assert tool.is_read_only(JDParseToolInput(text="x", store_to_rag=False)) is True
    assert tool.is_read_only(JDParseToolInput(text="x", store_to_rag=True)) is False


@pytest.mark.asyncio
async def test_jd_parse_extracts_structured_fields(jd_text, settings, ctx) -> None:
    result = await JDParseTool(settings=settings).execute(
        JDParseToolInput(text=jd_text, store_to_rag=False), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    job = payload["jd"]
    assert job["title"] == "Python 后端开发工程师"
    assert job["company"] == "杭州星辰科技有限公司"
    assert job["city"] == "杭州"
    assert (job["salary"]["min_monthly"], job["salary"]["max_monthly"]) == (25000, 40000)
    assert job["salary"]["months_per_year"] == 14
    assert job["experience"] == "3-5年"
    assert job["education"] == "本科及以上"
    assert job["required_skills"] == ["Python", "FastAPI", "MySQL", "Redis"]
    assert job["preferred_skills"] == ["Docker", "Kubernetes"]
    assert job["category"] == "后端"
    assert payload["completeness"] == 1.0
    assert payload["storage"] == {"stored": False}
    assert payload["warnings"] == []
    assert result.metadata == {"completeness": 1.0, "category": "后端"}


@pytest.mark.asyncio
async def test_jd_parse_ingests_posting_into_rag(jd_text, settings, ctx, retriever) -> None:
    result = await JDParseTool(retriever=retriever, settings=settings).execute(
        JDParseToolInput(text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["storage"]["stored"] is True
    assert payload["storage"]["collection"] == "jobs"
    assert payload["storage"]["doc_id"] == "jobs-doc-1"

    stored = retriever.store.documents["jobs"][0]
    metadata = stored["metadata"]
    assert stored["text"] == jd_text
    assert metadata["kind"] == "job"
    assert metadata["source"] == "jd_parse"
    assert metadata["company"] == "杭州星辰科技有限公司"
    assert metadata["salary_min"] == 25000
    assert metadata["salary_max"] == 40000
    assert metadata["months_per_year"] == 14
    assert metadata["required_skills"] == ["Python", "FastAPI", "MySQL", "Redis"]


@pytest.mark.asyncio
async def test_jd_parse_keeps_result_when_rag_is_offline(
    jd_text, settings, ctx, offline_retriever
) -> None:
    result = await JDParseTool(retriever=offline_retriever, settings=settings).execute(
        JDParseToolInput(text=jd_text), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["storage"]["stored"] is False
    assert "rag offline (test)" in payload["storage"]["error"]
    assert payload["jd"]["title"] == "Python 后端开发工程师"


@pytest.mark.asyncio
async def test_jd_parse_low_completeness_warns(settings, ctx) -> None:
    result = await JDParseTool(settings=settings).execute(
        JDParseToolInput(text="急招 Python 工程师，待遇面议", store_to_rag=False), ctx
    )

    payload = _payload(result)
    assert payload["completeness"] < 0.75
    assert payload["warnings"]
    assert "字段完整率" in payload["warnings"][0]


@pytest.mark.asyncio
async def test_jd_parse_rejects_empty_text(settings, ctx) -> None:
    result = await JDParseTool(settings=settings).execute(
        JDParseToolInput(text="   ", store_to_rag=False), ctx
    )

    assert result.is_error is True
    assert "'text' must be non-empty" in result.output
