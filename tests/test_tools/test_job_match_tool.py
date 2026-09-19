"""Tests for the job-matching tools (job_match / candidate_match)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.job_match_tool import (
    CandidateMatchTool,
    CandidateMatchToolInput,
    JobMatchTool,
    JobMatchToolInput,
)
from openharness.tools.user_profile_tool import ProfileUpdateTool, ProfileUpdateToolInput

FRONTEND_JD = """岗位名称：前端开发工程师
公司名称：上海某某科技有限公司
工作城市：上海
薪资范围：20-30K
岗位职责：
1. 负责前端页面开发
"""


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# job_match
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_match_inline_with_profile(
    jd_text, resume_text, profile_dict, settings, ctx
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )

    result = await JobMatchTool(settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text, jd_texts=[jd_text]), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["source"] == "inline"
    assert payload["evaluated"] == 1
    assert payload["filters"] == {}
    assert "notes" not in payload

    match = payload["matches"][0]
    assert match["rank"] == 1
    assert match["score"] == 92.7
    assert match["recommendation"] == "保底"
    assert match["skill_coverage"] == 1.0
    assert match["covered_skills"] == ["Python", "FastAPI", "MySQL", "Redis"]
    assert match["partial_skills"] == []
    assert match["missing_skills"] == []
    assert match["job"]["title"] == "Python 后端开发工程师"
    assert match["job"]["salary"] == "25-40K·14薪"
    assert match["job"]["source"] == "inline"
    assert match["job"]["doc_id"] == "inline-1"
    assert result.metadata == {"match_count": 1, "top_score": 92.7}


@pytest.mark.asyncio
async def test_job_match_inline_resume_only(jd_text, resume_text, settings, ctx) -> None:
    result = await JobMatchTool(settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text, jd_texts=[jd_text]), ctx
    )

    payload = _payload(result)
    assert payload["matches"][0]["score"] == 87.8
    assert result.metadata == {"match_count": 1, "top_score": 87.8}


@pytest.mark.asyncio
async def test_job_match_rag_mode_ranks_and_carries_notes(
    jd_text, resume_text, profile_dict, settings, ctx, make_hit, make_retriever
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )
    retriever = make_retriever(
        hits={
            "jobs": [
                make_hit(
                    jd_text,
                    doc_id="job-1",
                    collection="jobs",
                    title="Python 后端开发工程师",
                    company="杭州星辰科技有限公司",
                    city="杭州",
                ),
                make_hit(
                    FRONTEND_JD,
                    doc_id="job-2",
                    collection="jobs",
                    title="前端开发工程师",
                    company="上海某某科技有限公司",
                    city="上海",
                ),
            ]
        },
        notes={"jobs": ["样本提示：结果来自本地岗位库"]},
    )

    result = await JobMatchTool(retriever=retriever, settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text), ctx
    )

    payload = _payload(result)
    assert payload["source"] == "rag"
    assert payload["evaluated"] == 2
    assert [match["job"]["doc_id"] for match in payload["matches"]] == ["job-1", "job-2"]
    assert payload["matches"][0]["score"] == 92.7
    assert payload["matches"][0]["job"]["source"] == "rag"
    assert "样本提示：结果来自本地岗位库" in payload["notes"]
    assert result.metadata["match_count"] == 2


@pytest.mark.asyncio
async def test_job_match_filters_apply_locally(
    jd_text, resume_text, settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(
        hits={
            "jobs": [
                make_hit(jd_text, doc_id="job-1", collection="jobs", city="杭州"),
                make_hit(FRONTEND_JD, doc_id="job-2", collection="jobs", city="上海"),
            ]
        }
    )

    result = await JobMatchTool(retriever=retriever, settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text, filters={"keywords": ["前端"]}), ctx
    )

    payload = _payload(result)
    assert payload["source"] == "rag"
    assert payload["evaluated"] == 1
    assert payload["matches"][0]["job"]["doc_id"] == "job-2"
    assert "按 filters 过滤掉 1 个岗位" in payload["notes"]


@pytest.mark.asyncio
async def test_job_match_all_filtered_hints(
    jd_text, resume_text, settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(hits={"jobs": [make_hit(jd_text, doc_id="job-1", collection="jobs")]})

    result = await JobMatchTool(retriever=retriever, settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text, filters={"keywords": ["不存在的关键词"]}), ctx
    )

    payload = _payload(result)
    assert payload["evaluated"] == 0
    assert payload["matches"] == []
    assert "所有岗位都被 filters 过滤" in payload["notes"][-1]


@pytest.mark.asyncio
async def test_job_match_empty_pool_hints(resume_text, settings, ctx) -> None:
    result = await JobMatchTool(settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text), ctx
    )

    payload = _payload(result)
    assert payload["evaluated"] == 0
    assert "没有可评估的岗位" in payload["notes"][-1]


@pytest.mark.asyncio
async def test_job_match_rag_offline_errors(jd_text, resume_text, settings, ctx, offline_retriever) -> None:
    result = await JobMatchTool(retriever=offline_retriever, settings=settings).execute(
        JobMatchToolInput(resume_text=resume_text), ctx
    )

    assert result.is_error is True
    assert "岗位检索失败" in result.output
    assert "jd_texts" in result.output


@pytest.mark.asyncio
async def test_job_match_requires_candidate(jd_text, settings, ctx) -> None:
    result = await JobMatchTool(settings=settings).execute(
        JobMatchToolInput(jd_texts=[jd_text]), ctx
    )

    assert result.is_error is True
    assert "找不到候选人信息" in result.output


@pytest.mark.asyncio
async def test_job_match_empty_candidate_errors(jd_text, settings, ctx) -> None:
    result = await JobMatchTool(settings=settings).execute(
        JobMatchToolInput(resume_text="你好世界", jd_texts=[jd_text]), ctx
    )

    assert result.is_error is True
    assert "候选人信息为空" in result.output


# ---------------------------------------------------------------------------
# candidate_match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_match_ranks_rag_resume_and_profile(
    jd_text, resume_text, profile_dict, settings, ctx, make_hit, make_retriever
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )
    retriever = make_retriever(
        hits={
            "resumes": [
                make_hit(resume_text, doc_id="resume-1", collection="resumes", name="张伟")
            ]
        }
    )

    result = await CandidateMatchTool(retriever=retriever, settings=settings).execute(
        CandidateMatchToolInput(job_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["job"]["title"] == "Python 后端开发工程师"
    assert payload["pool_size"] == 2
    candidates = payload["candidates"]
    assert [item["candidate"] for item in candidates] == ["张伟", "本地画像"]
    assert [item["source"] for item in candidates] == ["rag", "profile"]
    assert candidates[0]["score"] == 87.8
    assert candidates[1]["score"] == 86.5
    assert candidates[0]["resume_summary"]["doc_id"] == "resume-1"
    assert candidates[0]["covered_skills"] == ["Python", "FastAPI", "MySQL", "Redis"]
    assert result.metadata == {"candidate_count": 2}


@pytest.mark.asyncio
async def test_candidate_match_excludes_profile_when_disabled(
    jd_text, resume_text, profile_dict, settings, ctx, make_hit, make_retriever
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )
    retriever = make_retriever(
        hits={"resumes": [make_hit(resume_text, doc_id="resume-1", collection="resumes")]}
    )

    result = await CandidateMatchTool(retriever=retriever, settings=settings).execute(
        CandidateMatchToolInput(job_text=jd_text, include_profile=False), ctx
    )

    payload = _payload(result)
    assert payload["pool_size"] == 1
    assert payload["candidates"][0]["candidate"] == "resume-1"


@pytest.mark.asyncio
async def test_candidate_match_empty_pool_hints(jd_text, settings, ctx, make_retriever) -> None:
    result = await CandidateMatchTool(retriever=make_retriever(), settings=settings).execute(
        CandidateMatchToolInput(job_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["pool_size"] == 0
    assert payload["candidates"] == []
    assert "候选池为空" in payload["notes"][0]


@pytest.mark.asyncio
async def test_candidate_match_offline_rag_still_uses_profile(
    jd_text, profile_dict, settings, ctx, offline_retriever
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )

    result = await CandidateMatchTool(retriever=offline_retriever, settings=settings).execute(
        CandidateMatchToolInput(job_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["pool_size"] == 1
    assert payload["candidates"][0]["candidate"] == "本地画像"
    assert any("RAG resumes 检索不可用" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_candidate_match_rejects_empty_job(settings, ctx) -> None:
    result = await CandidateMatchTool(settings=settings).execute(
        CandidateMatchToolInput(job_text="  "), ctx
    )

    assert result.is_error is True
    assert "'job_text' must be non-empty" in result.output
