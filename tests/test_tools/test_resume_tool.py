"""Tests for the resume tools (parse / generate / optimize / compare)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.resume_tool import (
    ResumeCompareTool,
    ResumeCompareToolInput,
    ResumeGenerateTool,
    ResumeGenerateToolInput,
    ResumeOptimizeTool,
    ResumeOptimizeToolInput,
    ResumeParseTool,
    ResumeParseToolInput,
)
from openharness.tools.user_profile_tool import ProfileUpdateTool, ProfileUpdateToolInput


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# resume_parse
# ---------------------------------------------------------------------------


def test_resume_parse_read_only_toggle(settings) -> None:
    tool = ResumeParseTool(settings=settings)

    assert tool.is_read_only(ResumeParseToolInput(text="x", store_to_rag=False)) is True
    assert tool.is_read_only(ResumeParseToolInput(text="x", store_to_rag=True)) is False


@pytest.mark.asyncio
async def test_resume_parse_from_text_with_ats_score(resume_text, settings, ctx) -> None:
    result = await ResumeParseTool(settings=settings).execute(
        ResumeParseToolInput(text=resume_text, store_to_rag=False), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["source"] == "text"
    assert payload["resume"]["personal_info"]["name"] == "张伟"
    assert payload["resume"]["skills"]["technical"] == [
        "Python",
        "FastAPI",
        "MySQL",
        "Redis",
        "Docker",
        "Git",
    ]
    assert payload["resume"]["years_of_experience"] == 4.0
    assert payload["ats"]["total"] == 65.0
    assert [item["name"] for item in payload["ats"]["components"]] == [
        "联系方式",
        "技能清单",
        "工作经历",
        "教育背景",
        "结构完整",
        "内容质量",
    ]
    assert payload["storage"] == {"stored": False}
    assert result.metadata == {"ats_score": 65.0}


@pytest.mark.asyncio
async def test_resume_parse_from_file_path(tmp_path, resume_text, settings, ctx) -> None:
    (tmp_path / "resume.md").write_text(resume_text, encoding="utf-8")

    result = await ResumeParseTool(settings=settings).execute(
        ResumeParseToolInput(file_path="resume.md", store_to_rag=False), ctx
    )

    payload = _payload(result)
    assert result.is_error is False
    assert payload["source"] == "resume.md"
    assert payload["ats"]["total"] == 65.0


@pytest.mark.asyncio
async def test_resume_parse_prefers_text_over_file(tmp_path, resume_text, settings, ctx) -> None:
    (tmp_path / "resume.md").write_text("李四 电话：10086", encoding="utf-8")

    result = await ResumeParseTool(settings=settings).execute(
        ResumeParseToolInput(text=resume_text, file_path="resume.md", store_to_rag=False), ctx
    )

    payload = _payload(result)
    assert payload["source"] == "text"
    assert payload["resume"]["personal_info"]["name"] == "张伟"


@pytest.mark.asyncio
async def test_resume_parse_file_errors(tmp_path, settings, ctx) -> None:
    missing = await ResumeParseTool(settings=settings).execute(
        ResumeParseToolInput(file_path="missing.md", store_to_rag=False), ctx
    )
    assert missing.is_error is True
    assert "Resume file not found" in missing.output

    (tmp_path / "resume.pdf").write_text("pdf", encoding="utf-8")
    bad_format = await ResumeParseTool(settings=settings).execute(
        ResumeParseToolInput(file_path="resume.pdf", store_to_rag=False), ctx
    )
    assert bad_format.is_error is True
    assert "Unsupported resume format" in bad_format.output


@pytest.mark.asyncio
async def test_resume_parse_requires_source(settings, ctx) -> None:
    result = await ResumeParseTool(settings=settings).execute(
        ResumeParseToolInput(store_to_rag=False), ctx
    )

    assert result.is_error is True
    assert "Provide either 'text' or 'file_path'" in result.output


@pytest.mark.asyncio
async def test_resume_parse_ingests_into_rag(resume_text, settings, ctx, retriever) -> None:
    result = await ResumeParseTool(retriever=retriever, settings=settings).execute(
        ResumeParseToolInput(text=resume_text), ctx
    )

    payload = _payload(result)
    assert payload["storage"]["stored"] is True
    assert payload["storage"]["collection"] == "resumes"
    metadata = retriever.store.documents["resumes"][0]["metadata"]
    assert metadata["kind"] == "resume"
    assert metadata["name"] == "张伟"
    assert metadata["years_of_experience"] == 4.0


@pytest.mark.asyncio
async def test_resume_parse_offline_rag_reports_error(
    resume_text, settings, ctx, offline_retriever
) -> None:
    result = await ResumeParseTool(retriever=offline_retriever, settings=settings).execute(
        ResumeParseToolInput(text=resume_text), ctx
    )

    payload = _payload(result)
    assert result.is_error is False
    assert payload["storage"]["stored"] is False
    assert "rag offline (test)" in payload["storage"]["error"]


# ---------------------------------------------------------------------------
# resume_generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_generate_from_resume_text(jd_text, resume_text, settings, ctx) -> None:
    result = await ResumeGenerateTool(settings=settings).execute(
        ResumeGenerateToolInput(jd_text=jd_text, resume_text=resume_text), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["template"] == "classic"
    assert payload["source"] == "resume_text"
    assert payload["coverage"] == {
        "required_total": 4,
        "covered": ["Python", "FastAPI", "MySQL", "Redis"],
        "partial": [],
        "missing": [],
        "coverage": 1.0,
    }
    assert payload["tips"] == []

    markdown = payload["resume_markdown"]
    assert markdown.startswith("# 张伟")
    assert "13800138000 | zhangwei@example.com" in markdown
    assert "Python 后端开发工程师｜杭州" in markdown
    assert "核心技能覆盖 100%" in markdown
    assert "## 教育背景" in markdown


@pytest.mark.asyncio
async def test_resume_generate_minimal_template(jd_text, resume_text, settings, ctx) -> None:
    result = await ResumeGenerateTool(settings=settings).execute(
        ResumeGenerateToolInput(jd_text=jd_text, resume_text=resume_text, template="minimal"), ctx
    )

    payload = _payload(result)
    assert payload["template"] == "minimal"
    assert "【求职意向】" in payload["resume_markdown"]
    assert "【专业技能】" in payload["resume_markdown"]


@pytest.mark.asyncio
async def test_resume_generate_from_stored_profile(jd_text, profile_dict, settings, ctx) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )

    result = await ResumeGenerateTool(settings=settings).execute(
        ResumeGenerateToolInput(jd_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["source"] == "profile"
    assert payload["coverage"]["coverage"] == 1.0
    markdown = payload["resume_markdown"]
    assert "6.5 年相关经验" in markdown
    assert "待补充：未提供简历文本" in markdown


@pytest.mark.asyncio
async def test_resume_generate_without_sources_hints(jd_text, settings, ctx) -> None:
    result = await ResumeGenerateTool(settings=settings).execute(
        ResumeGenerateToolInput(jd_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["source"] == "profile"
    assert payload["coverage"]["coverage"] == 0.0
    assert any("profile_update" in tip for tip in payload["tips"])
    assert "# 姓名" in payload["resume_markdown"]


@pytest.mark.asyncio
async def test_resume_generate_rejects_bad_input(jd_text, resume_text, settings, ctx) -> None:
    empty_jd = await ResumeGenerateTool(settings=settings).execute(
        ResumeGenerateToolInput(jd_text="  ", resume_text=resume_text), ctx
    )
    assert empty_jd.is_error is True
    assert "'jd_text' must be non-empty" in empty_jd.output

    bad_template = await ResumeGenerateTool(settings=settings).execute(
        ResumeGenerateToolInput(jd_text=jd_text, resume_text=resume_text, template="fancy"), ctx
    )
    assert bad_template.is_error is True
    assert "Unknown template" in bad_template.output


# ---------------------------------------------------------------------------
# resume_optimize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_optimize_checklist_without_jd(resume_text, settings, ctx) -> None:
    result = await ResumeOptimizeTool(settings=settings).execute(
        ResumeOptimizeToolInput(resume_text=resume_text), ctx
    )

    payload = _payload(result)
    assert payload["ats"]["total"] == 65.0
    assert payload["jd_alignment"] is None

    checklist = {item["component"]: item for item in payload["checklist"]}
    assert set(checklist) == {"技能清单", "工作经历", "结构完整", "内容质量"}
    assert checklist["技能清单"]["action"].startswith("技能项不足")
    assert checklist["工作经历"]["score"] == 11.0
    assert result.metadata == {"ats_score": 65.0}


@pytest.mark.asyncio
async def test_resume_optimize_with_jd_alignment(jd_text, resume_text, settings, ctx) -> None:
    result = await ResumeOptimizeTool(settings=settings).execute(
        ResumeOptimizeToolInput(resume_text=resume_text, jd_text=jd_text), ctx
    )

    alignment = _payload(result)["jd_alignment"]
    assert alignment["target"] == "Python 后端开发工程师"
    assert alignment["covered"] == ["Python", "FastAPI", "MySQL", "Redis"]
    assert alignment["partial"] == []
    assert alignment["missing"] == []
    #Kubernetes 与已掌握的 Docker 相近，按 partial 处理，不计入 missing_preferred
    assert alignment["missing_preferred"] == []
    assert alignment["coverage"] == 1.0
    assert alignment["priority_actions"] == []


@pytest.mark.asyncio
async def test_resume_optimize_rejects_empty_resume(settings, ctx) -> None:
    result = await ResumeOptimizeTool(settings=settings).execute(
        ResumeOptimizeToolInput(resume_text="  "), ctx
    )

    assert result.is_error is True
    assert "'resume_text' must be non-empty" in result.output


# ---------------------------------------------------------------------------
# resume_compare
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_compare_skill_diff_and_notes(resume_text, settings, ctx) -> None:
    resume_b = resume_text.replace("Docker、Git", "Docker、Git、Kubernetes")

    result = await ResumeCompareTool(settings=settings).execute(
        ResumeCompareToolInput(resume_a=resume_text, resume_b=resume_b), ctx
    )

    payload = _payload(result)
    assert payload["ats"]["delta"] == 2.0
    assert payload["skills"]["only_b"] == ["Kubernetes"]
    assert payload["skills"]["only_a"] == []
    assert payload["skills"]["count"] == {"a": 6, "b": 7}
    assert any("版本 B 新增技能" in note for note in payload["notes"])
    assert any("ATS 友好度：版本 B 更高" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_resume_compare_identical_versions(resume_text, settings, ctx) -> None:
    result = await ResumeCompareTool(settings=settings).execute(
        ResumeCompareToolInput(resume_a=resume_text, resume_b=resume_text), ctx
    )

    payload = _payload(result)
    assert payload["ats"]["delta"] == 0.0
    assert payload["skills"]["only_a"] == []
    assert payload["skills"]["only_b"] == []
    assert "两版本 ATS 友好度基本持平" in payload["notes"]


@pytest.mark.asyncio
async def test_resume_compare_dimension_filter_and_errors(resume_text, settings, ctx) -> None:
    filtered = await ResumeCompareTool(settings=settings).execute(
        ResumeCompareToolInput(resume_a=resume_text, resume_b=resume_text, dimensions=["skills"]),
        ctx,
    )
    assert set(_payload(filtered)) == {"skills", "notes"}

    unknown = await ResumeCompareTool(settings=settings).execute(
        ResumeCompareToolInput(resume_a=resume_text, resume_b=resume_text, dimensions=["typo"]),
        ctx,
    )
    assert unknown.is_error is True
    assert "Unknown dimensions" in unknown.output

    empty = await ResumeCompareTool(settings=settings).execute(
        ResumeCompareToolInput(resume_a="", resume_b=resume_text), ctx
    )
    assert empty.is_error is True
    assert "Both 'resume_a' and 'resume_b' must be non-empty" in empty.output
