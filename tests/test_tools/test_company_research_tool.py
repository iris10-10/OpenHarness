"""Tests for the company research tools (search / culture / techstack / history)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.company_research_tool import (
    CompanyCultureTool,
    CompanyCultureToolInput,
    CompanyHistoryTool,
    CompanyHistoryToolInput,
    CompanySearchTool,
    CompanySearchToolInput,
    CompanyTechstackTool,
    CompanyTechstackToolInput,
)

COMPANY = "杭州星辰科技有限公司"


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# company_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_search_profile_and_postings(
    settings, ctx, make_hit, make_retriever
) -> None:
    profile_text = "杭州星辰科技是一家专注企业 SaaS 的后端服务公司。"
    retriever = make_retriever(
        hits={
            "companies": [
                make_hit(
                    profile_text,
                    doc_id="co-1",
                    collection="companies",
                    company=COMPANY,
                    score=0.93,
                )
            ],
            "jobs": [
                make_hit(
                    "岗位名称：Python 后端开发工程师",
                    doc_id="job-1",
                    collection="jobs",
                    company=COMPANY,
                    title="Python 后端开发工程师",
                    city="杭州",
                    required_skills=["Python", "FastAPI"],
                    preferred_skills=["Redis"],
                ),
                make_hit(
                    "岗位名称：前端开发工程师",
                    doc_id="job-2",
                    collection="jobs",
                    company=COMPANY,
                    title="前端开发工程师",
                    city="杭州",
                    required_skills=["JavaScript", "React"],
                ),
            ],
        }
    )

    result = await CompanySearchTool(retriever=retriever, settings=settings).execute(
        CompanySearchToolInput(company=COMPANY), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["profile"]["snippet_count"] == 1
    snippet = payload["profile"]["snippets"][0]
    assert snippet == {
        "doc_id": "co-1",
        "collection": "companies",
        "company": COMPANY,
        "excerpt": profile_text,
        "score": 0.93,
    }
    assert payload["postings"]["count"] == 2
    assert payload["postings"]["titles"] == ["Python 后端开发工程师", "前端开发工程师"]
    assert payload["postings"]["tech_stack"] == [
        {"skill": "FastAPI", "count": 1, "share": 0.5},
        {"skill": "JavaScript", "count": 1, "share": 0.5},
        {"skill": "Python", "count": 1, "share": 0.5},
        {"skill": "React", "count": 1, "share": 0.5},
        {"skill": "Redis", "count": 1, "share": 0.5},
    ]
    assert "notes" not in payload
    assert result.metadata == {"snippet_count": 1, "posting_count": 2}


@pytest.mark.asyncio
async def test_company_search_empty_and_offline(
    settings, ctx, make_retriever, offline_retriever
) -> None:
    empty = await CompanySearchTool(
        retriever=make_retriever(), settings=settings
    ).execute(CompanySearchToolInput(company=COMPANY), ctx)

    payload = _payload(empty)
    assert payload["profile"]["snippet_count"] == 0
    assert payload["postings"]["count"] == 0
    assert f"知识库中没有 '{COMPANY}' 的资料" in payload["notes"][0]
    assert empty.metadata == {"snippet_count": 0, "posting_count": 0}

    offline = await CompanySearchTool(
        retriever=offline_retriever, settings=settings
    ).execute(CompanySearchToolInput(company=COMPANY), ctx)

    assert offline.is_error is False
    payload = _payload(offline)
    assert "本地知识库检索不可用" in payload["notes"][0]
    assert payload["profile"]["snippet_count"] == 0

    missing = await CompanySearchTool(settings=settings).execute(
        CompanySearchToolInput(company="  "), ctx
    )
    assert missing.is_error is True
    assert "'company' 为必填" in missing.output


# ---------------------------------------------------------------------------
# company_culture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_culture_queries_both_collections(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(
        hits={
            "companies": [
                make_hit(
                    "员工评价：团队氛围融洽，弹性工作。",
                    doc_id="co-1",
                    collection="companies",
                    company=COMPANY,
                )
            ],
            "interview": [
                make_hit(
                    "面经：面试流程规范，加班强度中等。",
                    doc_id="exp-1",
                    collection="interview",
                    company=COMPANY,
                )
            ],
        }
    )

    result = await CompanyCultureTool(retriever=retriever, settings=settings).execute(
        CompanyCultureToolInput(company=COMPANY), ctx
    )

    payload = _payload(result)
    assert payload["snippet_count"] == 2
    assert {snippet["collection"] for snippet in payload["snippets"]} == {
        "companies",
        "interview",
    }
    assert "notes" not in payload
    assert result.metadata == {"snippet_count": 2}
    assert retriever.calls[0]["collections"] == ["companies", "interview"]

    empty = await CompanyCultureTool(
        retriever=make_retriever(), settings=settings
    ).execute(CompanyCultureToolInput(company=COMPANY), ctx)
    payload = _payload(empty)
    assert payload["snippet_count"] == 0
    assert f"知识库中没有 '{COMPANY}' 的文化/评价资料" in payload["notes"][0]


# ---------------------------------------------------------------------------
# company_techstack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_techstack_ranks_by_posting_count(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(
        hits={
            "jobs": [
                make_hit(
                    "岗位名称：后端开发工程师",
                    doc_id="job-1",
                    collection="jobs",
                    company=COMPANY,
                    title="后端开发工程师",
                    city="杭州",
                    required_skills=["Python", "MySQL"],
                    preferred_skills=["Redis"],
                ),
                make_hit(
                    "岗位名称：服务端开发工程师",
                    doc_id="job-2",
                    collection="jobs",
                    company=COMPANY,
                    title="服务端开发工程师",
                    city="杭州",
                    required_skills=["Python", "MySQL"],
                    preferred_skills=["Docker"],
                ),
                make_hit(
                    "岗位名称：数据平台工程师",
                    doc_id="job-3",
                    collection="jobs",
                    company=COMPANY,
                    title="数据平台工程师",
                    city="上海",
                    required_skills=["Python"],
                    preferred_skills=["Redis"],
                ),
            ]
        }
    )

    result = await CompanyTechstackTool(retriever=retriever, settings=settings).execute(
        CompanyTechstackToolInput(company=COMPANY), ctx
    )

    payload = _payload(result)
    assert payload["posting_count"] == 3
    assert payload["tech_stack"] == [
        {"skill": "Python", "count": 3, "share": 1.0},
        {"skill": "MySQL", "count": 2, "share": 0.67},
        {"skill": "Redis", "count": 2, "share": 0.67},
        {"skill": "Docker", "count": 1, "share": 0.33},
    ]
    assert [sample["doc_id"] for sample in payload["samples"]] == [
        "job-1",
        "job-2",
        "job-3",
    ]
    assert payload["samples"][2]["city"] == "上海"
    assert "notes" not in payload
    assert result.metadata == {"posting_count": 3}


@pytest.mark.asyncio
async def test_company_techstack_text_fallback_and_empty(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(
        hits={
            "jobs": [
                make_hit(
                    "岗位职责：熟悉 Python 与 K8s，负责容器化平台建设。",
                    doc_id="job-text",
                    collection="jobs",
                    company=COMPANY,
                    title="平台工程师",
                )
            ]
        }
    )

    result = await CompanyTechstackTool(retriever=retriever, settings=settings).execute(
        CompanyTechstackToolInput(company=COMPANY), ctx
    )

    payload = _payload(result)
    assert payload["tech_stack"] == [
        {"skill": "Kubernetes", "count": 1, "share": 1.0},
        {"skill": "Python", "count": 1, "share": 1.0},
    ]

    empty = await CompanyTechstackTool(
        retriever=make_retriever(), settings=settings
    ).execute(CompanyTechstackToolInput(company=COMPANY), ctx)
    payload = _payload(empty)
    assert payload["posting_count"] == 0
    assert payload["tech_stack"] == []
    assert f"没有 '{COMPANY}' 的已入库岗位" in payload["notes"][0]
    assert empty.metadata == {"posting_count": 0}


# ---------------------------------------------------------------------------
# company_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_history_snippets_and_empty(
    settings, ctx, make_hit, make_retriever
) -> None:
    history_text = "公司 2018 年成立，2023 年完成 B 轮融资，团队规模 300 人。"
    retriever = make_retriever(
        hits={
            "companies": [
                make_hit(
                    history_text,
                    doc_id="co-2",
                    collection="companies",
                    company=COMPANY,
                    score=0.81,
                )
            ]
        }
    )

    result = await CompanyHistoryTool(retriever=retriever, settings=settings).execute(
        CompanyHistoryToolInput(company=COMPANY), ctx
    )

    payload = _payload(result)
    assert payload["snippet_count"] == 1
    assert payload["snippets"][0]["excerpt"] == history_text
    assert result.metadata == {"snippet_count": 1}
    assert retriever.calls[0]["collections"] == ["companies"]

    empty = await CompanyHistoryTool(
        retriever=make_retriever(), settings=settings
    ).execute(CompanyHistoryToolInput(company=COMPANY), ctx)
    payload = _payload(empty)
    assert f"知识库中没有 '{COMPANY}' 的发展历程资料" in payload["notes"][0]


def test_company_tools_read_only_and_validation() -> None:
    assert (
        CompanySearchTool().is_read_only(CompanySearchToolInput(company="A")) is True
    )
    assert (
        CompanyCultureTool().is_read_only(CompanyCultureToolInput(company="A")) is True
    )
    assert (
        CompanyTechstackTool().is_read_only(CompanyTechstackToolInput(company="A"))
        is True
    )
    assert (
        CompanyHistoryTool().is_read_only(CompanyHistoryToolInput(company="A")) is True
    )


@pytest.mark.asyncio
async def test_company_tools_require_company(settings, ctx) -> None:
    for tool, input_model in (
        (CompanyCultureTool(settings=settings), CompanyCultureToolInput),
        (CompanyTechstackTool(settings=settings), CompanyTechstackToolInput),
        (CompanyHistoryTool(settings=settings), CompanyHistoryToolInput),
    ):
        result = await tool.execute(input_model(company="  "), ctx)
        assert result.is_error is True
        assert "'company' 为必填" in result.output
