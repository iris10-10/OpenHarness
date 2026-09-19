"""Tests for the cover-letter tool (cover_letter_generate)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.base import ToolResult
from openharness.tools.cover_letter_tool import (
    CoverLetterGenerateTool,
    CoverLetterGenerateToolInput,
)
from openharness.tools.user_profile_tool import ProfileUpdateTool, ProfileUpdateToolInput

HIGHLIGHT = "负责交易系统后端开发，使用 Python、FastAPI、MySQL、Redis，接口延迟降低 40%"


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_cover_letter_chinese_formal_from_resume(
    jd_text, resume_text, settings, ctx
) -> None:
    result = await CoverLetterGenerateTool(settings=settings).execute(
        CoverLetterGenerateToolInput(jd_text=jd_text, resume_text=resume_text), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["style"] == "正式"
    assert payload["language"] == "中文"
    assert payload["company"] == "杭州星辰科技有限公司"
    assert payload["position"] == "Python 后端开发工程师"

    letter = payload["letter"]
    assert letter.startswith("尊敬的杭州星辰科技有限公司招聘团队：")
    assert "我写这封信是希望应聘杭州星辰科技有限公司的Python 后端开发工程师岗位。" in letter
    assert (
        "我的技能与岗位要求高度契合：熟练使用Python、FastAPI、MySQL、Redis。"
        "此外，我还熟悉Docker，可快速上手相关方向。" in letter
    )
    assert f"值得说明的经历：{HIGHLIGHT}。" in letter
    assert "感谢您抽出时间阅读我的申请。期待您的回复，顺祝商祺！" in letter
    assert letter.endswith("张伟\n13800138000 | zhangwei@example.com")
    assert "【姓名】" not in letter

    assert payload["personalization"] == {
        "source": "resume_text",
        "matched_required": ["Python", "FastAPI", "MySQL", "Redis"],
        "matched_preferred": ["Docker"],
        "highlights_used": [HIGHLIGHT],
        "skill_coverage": 1.0,
    }
    assert payload["tips"] == []
    assert "notes" not in payload
    assert result.metadata == {"style": "正式", "language": "中文", "coverage": 1.0}


@pytest.mark.asyncio
async def test_cover_letter_chinese_styles(jd_text, resume_text, settings, ctx) -> None:
    tool = CoverLetterGenerateTool(settings=settings)

    concise = await tool.execute(
        CoverLetterGenerateToolInput(
            jd_text=jd_text, resume_text=resume_text, style="简洁"
        ),
        ctx,
    )
    payload = _payload(concise)
    assert payload["style"] == "简洁"
    letter = payload["letter"]
    assert letter.startswith("您好，")
    assert "我申请杭州星辰科技有限公司的Python 后端开发工程师岗位，简要说明我的匹配点。" in letter
    assert "期待您的回复。谢谢！" in letter

    eager = await tool.execute(
        CoverLetterGenerateToolInput(
            jd_text=jd_text, resume_text=resume_text, style="热情"
        ),
        ctx,
    )
    payload = _payload(eager)
    assert payload["style"] == "热情"
    letter = payload["letter"]
    assert "我对杭州星辰科技有限公司的Python 后端开发工程师岗位非常感兴趣，特此提交申请！" in letter
    assert "非常期待与您进一步交流！感谢您的时间。" in letter


@pytest.mark.asyncio
async def test_cover_letter_english(jd_text, resume_text, settings, ctx) -> None:
    result = await CoverLetterGenerateTool(settings=settings).execute(
        CoverLetterGenerateToolInput(
            jd_text=jd_text, resume_text=resume_text, language="en"
        ),
        ctx,
    )

    payload = _payload(result)
    assert payload["language"] == "英文"
    letter = payload["letter"]
    assert letter.startswith("Dear 杭州星辰科技有限公司 Hiring Team,")
    assert (
        "I am writing to apply for the Python 后端开发工程师 position at "
        "杭州星辰科技有限公司. My background closely matches the role requirements." in letter
    )
    assert (
        "I have hands-on experience with Python, FastAPI, MySQL, Redis, "
        "which are core requirements of the role. I am also familiar with Docker." in letter
    )
    assert f"Highlights from my experience: {HIGHLIGHT}." in letter
    assert "Sincerely," in letter
    assert letter.endswith("张伟\n13800138000 | zhangwei@example.com")
    assert result.metadata == {"style": "正式", "language": "英文", "coverage": 1.0}


@pytest.mark.asyncio
async def test_cover_letter_overrides(jd_text, resume_text, settings, ctx) -> None:
    result = await CoverLetterGenerateTool(settings=settings).execute(
        CoverLetterGenerateToolInput(
            jd_text=jd_text,
            resume_text=resume_text,
            company="甲乙科技",
            position="资深后端工程师",
            candidate_name="张三",
            style="简洁",
        ),
        ctx,
    )

    payload = _payload(result)
    assert payload["company"] == "甲乙科技"
    assert payload["position"] == "资深后端工程师"
    letter = payload["letter"]
    assert "我申请甲乙科技的资深后端工程师岗位" in letter
    assert letter.endswith("张三\n13800138000 | zhangwei@example.com")


@pytest.mark.asyncio
async def test_cover_letter_without_materials_uses_placeholders(
    jd_text, settings, ctx
) -> None:
    result = await CoverLetterGenerateTool(settings=settings).execute(
        CoverLetterGenerateToolInput(jd_text=jd_text), ctx
    )

    payload = _payload(result)
    letter = payload["letter"]
    assert "【姓名】" in letter
    assert "【电话】 | 【邮箱】" in letter
    assert "[建议补充：一段与岗位相关的技术栈或能力描述]" in letter
    assert "[建议补充：1-2 个与岗位相关的量化成果]" in letter

    assert payload["personalization"]["source"] == "none"
    assert payload["personalization"]["matched_required"] == []
    assert payload["personalization"]["skill_coverage"] == 0.0
    assert payload["notes"][0].startswith("信中包含占位符")
    assert payload["tips"] == [
        "岗位要求但材料中未体现的技能：Python、FastAPI、MySQL、Redis",
        "未提供简历文本且用户画像为空：建议先 profile_update 或提供 resume_text",
    ]
    assert result.metadata == {"style": "正式", "language": "中文", "coverage": 0.0}


@pytest.mark.asyncio
async def test_cover_letter_profile_fallback(jd_text, profile_dict, settings, ctx) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )

    result = await CoverLetterGenerateTool(settings=settings).execute(
        CoverLetterGenerateToolInput(jd_text=jd_text), ctx
    )

    payload = _payload(result)
    assert payload["personalization"]["source"] == "profile"
    assert payload["personalization"]["matched_required"] == [
        "Python",
        "FastAPI",
        "MySQL",
        "Redis",
    ]
    assert payload["personalization"]["matched_preferred"] == ["Docker", "Kubernetes"]
    assert payload["personalization"]["skill_coverage"] == 1.0
    # 画像不含姓名 → 保留占位符，不虚构
    assert "【姓名】" in payload["letter"]
    assert payload["tips"] == []


@pytest.mark.asyncio
async def test_cover_letter_validation_errors(jd_text, settings, ctx) -> None:
    tool = CoverLetterGenerateTool(settings=settings)

    empty_jd = await tool.execute(CoverLetterGenerateToolInput(jd_text="  "), ctx)
    assert empty_jd.is_error is True
    assert "'jd_text' 为必填" in empty_jd.output

    bad_style = await tool.execute(
        CoverLetterGenerateToolInput(jd_text=jd_text, style="幽默"), ctx
    )
    assert bad_style.is_error is True
    assert "未知风格 '幽默'" in bad_style.output

    bad_language = await tool.execute(
        CoverLetterGenerateToolInput(jd_text=jd_text, language="法语"), ctx
    )
    assert bad_language.is_error is True
    assert "未知语言 '法语'" in bad_language.output

    assert (
        tool.is_read_only(CoverLetterGenerateToolInput(jd_text="x")) is True
    )
