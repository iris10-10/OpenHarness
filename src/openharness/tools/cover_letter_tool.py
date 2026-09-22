"""Cover-letter tool: cover_letter_generate.

Drafts a personalised cover letter from the target JD plus resume text or
the stored user profile. Three styles (正式 / 简洁 / 热情) and two languages
(中文 / 英文) are supported.

Everything in the letter comes from the provided materials: matched skills,
resume highlights, parsed contact info. Missing information stays as
bracketed placeholders (【姓名】/【电话】/[建议补充：…]) instead of being
invented, and every fact actually used is reported back in the payload so
the user can verify the claims before sending.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import (
    ParsedJD,
    ParsedResume,
    parse_jd_text,
    parse_resume_text,
)
from openharness.jobhunt.scoring import CandidateProfile, analyze_skill_gap
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_STYLES: tuple[str, ...] = ("正式", "简洁", "热情")
_LANGUAGES: tuple[str, ...] = ("中文", "英文")

_LANGUAGE_ALIASES: dict[str, str] = {
    "zh": "中文",
    "chinese": "中文",
    "中文": "中文",
    "en": "英文",
    "english": "英文",
    "英文": "英文",
    "英语": "英文",
}

_MAX_HIGHLIGHTS = 2
_MAX_SKILLS_IN_LETTER = 6

_NAME_PLACEHOLDER_CN = "【姓名】"
_CONTACT_PLACEHOLDER_CN = "【电话】 | 【邮箱】"


def _resolve_choice(value: str, options: tuple[str, ...], aliases: dict[str, str]) -> str:
    cleaned = value.strip()
    cleaned = aliases.get(cleaned.lower(), cleaned)
    return cleaned if cleaned in options else ""


def _skill_facts(candidate: CandidateProfile, job: ParsedJD) -> dict[str, Any]:
    """Matching skills and coverage between candidate and JD."""
    report = analyze_skill_gap(candidate.skills, job)
    return {
        "matched_required": [
            skill for skill in job.required_skills if skill in candidate.skills
        ],
        "matched_preferred": [
            skill for skill in job.preferred_skills if skill in candidate.skills
        ],
        "fallback_skills": list(candidate.skills[:_MAX_SKILLS_IN_LETTER]),
        "missing": list(report.missing),
        "coverage": report.coverage,
    }


def _contact_facts(parsed: ParsedResume | None) -> dict[str, str]:
    info = parsed.personal_info if parsed is not None else {}
    return {
        "name": str(info.get("name", "")).strip(),
        "phone": str(info.get("phone", "")).strip(),
        "email": str(info.get("email", "")).strip(),
    }


def _cn_letter(
    facts: dict[str, Any], *, style: str, company: str, position: str
) -> tuple[str, bool]:
    """Assemble the Chinese letter; returns (text, placeholder_used)."""
    placeholders = False
    company_label = company or "贵司"
    position_label = position or "该岗位"
    greeting_target = f"{company}招聘团队" if company else "招聘团队"

    if style == "简洁":
        greeting = "您好，"
    else:
        greeting = f"尊敬的{greeting_target}："
    opening = {
        "正式": f"我写这封信是希望应聘{company_label}的{position_label}岗位。",
        "简洁": f"我申请{company_label}的{position_label}岗位，简要说明我的匹配点。",
        "热情": f"我对{company_label}的{position_label}岗位非常感兴趣，特此提交申请！",
    }[style]

    paragraphs: list[str] = [greeting, opening]

    matched = facts["matched_required"]
    preferred = facts["matched_preferred"]
    if matched:
        skill_line = f"我的技能与岗位要求高度契合：熟练使用{'、'.join(matched)}。"
        if preferred:
            skill_line += f"此外，我还熟悉{'、'.join(preferred)}，可快速上手相关方向。"
        paragraphs.append(skill_line)
    elif facts["fallback_skills"]:
        paragraphs.append(
            f"我具备{'、'.join(facts['fallback_skills'])}等技术背景，与岗位的技术方向一致。"
        )
    else:
        placeholders = True
        paragraphs.append("[建议补充：一段与岗位相关的技术栈或能力描述]")

    highlights = facts["highlights"]
    if highlights:
        joined = "；".join(highlights)
        paragraphs.append(f"值得说明的经历：{joined}。")
    else:
        placeholders = True
        paragraphs.append("[建议补充：1-2 个与岗位相关的量化成果]")

    paragraphs.append(
        {
            "正式": (
                f"我认同{company_label}的技术与产品方向，"
                f"期待在{position_label}岗位上贡献经验并与团队共同成长。"
            ),
            "简洁": f"期待加入{company_label}，一起把{position_label}相关的工作做成。",
            "热情": (
                f"我期待加入{company_label}的{position_label}团队，"
                "把过去的经验转化成结果，也为团队带来新的活力！"
            ),
        }[style]
    )
    paragraphs.append(
        {
            "正式": "感谢您抽出时间阅读我的申请。期待您的回复，顺祝商祺！",
            "简洁": "期待您的回复。谢谢！",
            "热情": "非常期待与您进一步交流！感谢您的时间。",
        }[style]
    )

    name = facts["name"] or _NAME_PLACEHOLDER_CN
    if not facts["name"]:
        placeholders = True
    contact_parts = [part for part in (facts["phone"], facts["email"]) if part]
    if not contact_parts:
        placeholders = True
        contact_line = _CONTACT_PLACEHOLDER_CN
    else:
        contact_line = " | ".join(contact_parts)
    signature = f"{name}\n{contact_line}"

    body = "\n\n".join([*paragraphs, signature])
    return body, placeholders


def _en_letter(
    facts: dict[str, Any], *, style: str, company: str, position: str
) -> tuple[str, bool]:
    """Assemble the English letter; returns (text, placeholder_used)."""
    placeholders = False
    company_label = company or "your company"
    position_label = position or "this role"

    greeting = {
        "正式": f"Dear {company or 'Hiring'} Hiring Team,",
        "简洁": "Hello,",
        "热情": f"Dear {company} Team," if company else "Hello there,",
    }[style]
    opening = {
        "正式": (
            f"I am writing to apply for the {position_label} position at "
            f"{company_label}. My background closely matches the role requirements."
        ),
        "简洁": f"I would like to apply for the {position_label} position.",
        "热情": (
            f"I am excited to apply for the {position_label} position — "
            "I believe my background is a strong match!"
        ),
    }[style]

    paragraphs: list[str] = [greeting, opening]

    matched = facts["matched_required"]
    preferred = facts["matched_preferred"]
    if matched:
        skill_line = (
            f"I have hands-on experience with {', '.join(matched)}, "
            "which are core requirements of the role."
        )
        if preferred:
            skill_line += f" I am also familiar with {', '.join(preferred)}."
        paragraphs.append(skill_line)
    elif facts["fallback_skills"]:
        paragraphs.append(
            "My technical background ("
            + ", ".join(facts["fallback_skills"])
            + ") aligns with the direction of this role."
        )
    else:
        placeholders = True
        paragraphs.append("[Add 1-2 sentences about your relevant technical skills]")

    highlights = facts["highlights"]
    if highlights:
        paragraphs.append("Highlights from my experience: " + "; ".join(highlights) + ".")
    else:
        placeholders = True
        paragraphs.append("[Add 1-2 quantified achievements relevant to this role]")

    paragraphs.append(
        {
            "正式": (
                f"I am drawn to {company_label}'s track record and would welcome "
                f"the opportunity to contribute to the {position_label} team and grow with it."
            ),
            "简洁": f"I would welcome the chance to contribute to {company_label}.",
            "热情": (
                f"I would be thrilled to bring my experience to {company_label} "
                f"and make an impact with the {position_label} team!"
            ),
        }[style]
    )
    paragraphs.append(
        {
            "正式": "Thank you for your time and consideration. I look forward to your reply.",
            "简洁": "Thanks for your time — looking forward to your reply.",
            "热情": "I would love the chance to talk further — thank you for your consideration!",
        }[style]
    )

    name = facts["name"] or "[Your Name]"
    if not facts["name"]:
        placeholders = True
    contact_parts = [part for part in (facts["phone"], facts["email"]) if part]
    if not contact_parts:
        placeholders = True
        contact_line = "[Phone] | [Email]"
    else:
        contact_line = " | ".join(contact_parts)
    closing = {"正式": "Sincerely,", "简洁": "Best,", "热情": "Best regards,"}[style]
    signature = f"{closing}\n{name}\n{contact_line}"

    body = "\n\n".join([*paragraphs, signature])
    return body, placeholders


# ---------------------------------------------------------------------------
# cover_letter_generate
# ---------------------------------------------------------------------------


class CoverLetterGenerateToolInput(BaseModel):
    """Arguments for the cover_letter_generate tool."""

    jd_text: str = Field(description="Target job description text")
    resume_text: str = Field(
        default="",
        description="Resume text for personalisation; falls back to the stored profile",
    )
    company: str = Field(default="", description="Override the company name from the JD")
    position: str = Field(default="", description="Override the position from the JD")
    style: str = Field(default="正式", description="Style: 正式 / 简洁 / 热情")
    language: str = Field(default="中文", description="Language: 中文 / 英文")
    candidate_name: str = Field(default="", description="Override the candidate name")


class CoverLetterGenerateTool(JobHuntToolBase):
    """Generate a personalised cover letter from JD + materials."""

    name = "cover_letter_generate"
    description = (
        "Generate a personalised cover letter from a target JD plus resume text "
        "or the stored user profile. Styles: 正式 / 简洁 / 热情; languages: 中文 / "
        "英文. Only facts from the provided materials are used — missing info "
        "stays as bracketed placeholders — and the matched skills / highlights "
        "actually used are reported for verification. Read-only."
    )
    input_model = CoverLetterGenerateToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: CoverLetterGenerateToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not arguments.jd_text.strip():
            return error_result("'jd_text' 为必填")
        style = _resolve_choice(arguments.style, _STYLES, {})
        if not style:
            return error_result(
                f"未知风格 '{arguments.style}'：可选 {' / '.join(_STYLES)}"
            )
        language = _resolve_choice(arguments.language, _LANGUAGES, _LANGUAGE_ALIASES)
        if not language:
            return error_result(
                f"未知语言 '{arguments.language}'：可选 {' / '.join(_LANGUAGES)}"
                "（也接受 zh / en）"
            )

        job = parse_jd_text(arguments.jd_text)
        company = arguments.company.strip() or job.company
        position = arguments.position.strip() or job.title

        profile = self.resolve_profile(context)
        parsed = parse_resume_text(arguments.resume_text) if arguments.resume_text.strip() else None
        candidate = self.candidate_from_texts(arguments.resume_text, profile)

        facts: dict[str, Any] = {}
        facts.update(_skill_facts(candidate, job))
        facts.update(_contact_facts(parsed))
        if arguments.candidate_name.strip():
            facts["name"] = arguments.candidate_name.strip()
        facts["highlights"] = [
            str(item).strip() for item in candidate.highlights if str(item).strip()
        ][:_MAX_HIGHLIGHTS]

        builder = _cn_letter if language == "中文" else _en_letter
        letter, placeholders = builder(
            facts, style=style, company=company, position=position
        )

        tips: list[str] = []
        if facts["missing"]:
            tips.append(
                "岗位要求但材料中未体现的技能：" + "、".join(facts["missing"])
            )
        if parsed is None and not profile:
            tips.append("未提供简历文本且用户画像为空：建议先 profile_update 或提供 resume_text")
        if not facts["highlights"] and parsed is not None:
            tips.append("未从简历中解析出经历亮点：请检查“工作经历/项目经历”分节与格式")

        source = "resume_text" if arguments.resume_text.strip() else (
            "profile" if profile else "none"
        )
        payload: dict[str, Any] = {
            "letter": letter,
            "style": style,
            "language": language,
            "position": position,
            "company": company,
            "personalization": {
                "source": source,
                "matched_required": facts["matched_required"],
                "matched_preferred": facts["matched_preferred"],
                "highlights_used": facts["highlights"],
                "skill_coverage": facts["coverage"],
            },
            "tips": tips,
        }
        if placeholders:
            payload["notes"] = [
                (
                    "信中包含占位符（【姓名】/【电话】/[建议补充：…]）：发送前请替换为真实信息；"
                    "求职信不会虚构任何经历"
                )
            ]
        return ToolResult(
            output=json_output(payload),
            metadata={
                "style": style,
                "language": language,
                "coverage": facts["coverage"],
            },
        )
