"""Resume management tools: parse / generate / optimize / compare.

Four tools backed by the deterministic parsing and ATS-scoring engines in
:mod:`openharness.jobhunt`:

- ``resume_parse`` — extract the structured resume model, compute the ATS
  score and (optionally) ingest the resume into the RAG ``resumes``
  collection.
- ``resume_generate`` — draft a JD-targeted resume from resume text or the
  stored profile.
- ``resume_optimize`` — ATS improvement checklist plus, when a JD is given,
  keyword-alignment analysis.
- ``resume_compare`` — side-by-side comparison of two resume versions.

``resume_parse`` writes to the RAG collection only when ``store_to_rag`` is
true and reports ingestion failures as warnings so the parse result is never
lost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import (
    ParsedJD,
    ParsedResume,
    parse_jd_text,
    parse_resume_text,
)
from openharness.jobhunt.scoring import (
    CandidateProfile,
    analyze_skill_gap,
    score_resume_ats,
)
from openharness.jobhunt.storage import today_iso
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ""}
_MAX_RESUME_CHARS = 200_000
_MAX_OTHER_SKILLS = 12


def _read_resume_file(file_path: str, cwd: Path) -> tuple[str | None, str | None]:
    """Read a resume file; returns ``(text, error)``."""
    path = Path(file_path)
    if not path.is_absolute():
        path = cwd / path
    if not path.exists():
        return None, f"Resume file not found: {path}"
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return None, (
            f"Unsupported resume format '{path.suffix}'. Export the resume to "
            "Markdown or plain text (.md / .txt) first."
        )
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"Failed to read resume file: {exc}"
    return content[:_MAX_RESUME_CHARS], None


def _ats_payload(resume: ParsedResume, raw_text: str) -> dict[str, Any]:
    """Compute the ATS score, sync it onto the resume and return a payload."""
    ats = score_resume_ats(resume, raw_text)
    resume.ats_score = ats.total
    return {
        "total": ats.total,
        "components": [
            {
                "name": component.name,
                "score": component.score,
                "max_score": component.max_score,
                "detail": component.detail,
            }
            for component in ats.components
        ],
    }


# ---------------------------------------------------------------------------
# resume_parse
# ---------------------------------------------------------------------------


class ResumeParseToolInput(BaseModel):
    """Arguments for the resume_parse tool."""

    file_path: str | None = Field(
        default=None, description="Path to a Markdown / plain-text resume file"
    )
    text: str | None = Field(
        default=None, description="Raw resume text (alternative to file_path)"
    )
    store_to_rag: bool = Field(
        default=True,
        description="Ingest the parsed resume into the RAG 'resumes' collection",
    )


class ResumeParseTool(JobHuntToolBase):
    """Parse a resume into the structured model with an ATS score."""

    name = "resume_parse"
    description = (
        "Parse a Markdown / plain-text resume into the structured model "
        "(personal info, skills, experience, education, projects) and compute an "
        "itemised ATS friendliness score. Optionally ingests the resume into the "
        "RAG 'resumes' collection for semantic search. Writes to the local RAG "
        "store when store_to_rag is true."
    )
    input_model = ResumeParseToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        if isinstance(arguments, ResumeParseToolInput):
            return not arguments.store_to_rag
        return False

    async def execute(
        self, arguments: ResumeParseToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        if arguments.text and arguments.text.strip():
            raw_text = arguments.text
            source = "text"
        elif arguments.file_path:
            content, read_error = _read_resume_file(arguments.file_path, context.cwd)
            if read_error or content is None:
                return error_result(read_error or "Failed to read resume file")
            raw_text = content
            source = arguments.file_path
        else:
            return error_result("Provide either 'text' or 'file_path'")

        resume = parse_resume_text(raw_text)
        ats = _ats_payload(resume, raw_text)
        payload: dict[str, Any] = {
            "source": source,
            "resume": resume.to_dict(),
            "ats": ats,
        }

        storage: dict[str, Any] = {"stored": False}
        if arguments.store_to_rag:
            metadata: dict[str, Any] = {
                "kind": "resume",
                "source": "resume_parse",
                "name": resume.personal_info.get("name", ""),
                "years_of_experience": resume.years_of_experience,
                "top_skills": resume.technical_skills[:15],
                "parsed_at": today_iso(),
            }
            try:
                doc_id = self.ingest_document(
                    collection="resumes", text=raw_text, metadata=metadata
                )
                storage = {"stored": True, "collection": "resumes", "doc_id": doc_id}
            #RAG 不可用时解析结果仍然有效，不中断解析流程
            except Exception as exc:  # noqa: BLE001
                storage = {"stored": False, "error": str(exc)}
        payload["storage"] = storage
        return ToolResult(output=json_output(payload), metadata={"ats_score": ats["total"]})


# ---------------------------------------------------------------------------
# resume_generate
# ---------------------------------------------------------------------------


class ResumeGenerateToolInput(BaseModel):
    """Arguments for the resume_generate tool."""

    jd_text: str = Field(description="Target job description text")
    resume_text: str | None = Field(
        default=None,
        description="Source resume text; falls back to the stored user profile",
    )
    template: str = Field(
        default="classic", description="Layout template: classic / modern / minimal"
    )


def _section_header(template: str, title: str) -> str:
    if template == "minimal":
        return f"【{title}】"
    if template == "modern":
        return f"## {title}\n"
    return f"## {title}"


def _join_contact(info: dict[str, str]) -> str:
    parts = [
        str(info.get(key, "")).strip()
        for key in ("phone", "email", "location", "website")
    ]
    return " | ".join(part for part in parts if part)


def _rank_highlights(highlights: list[str], job: ParsedJD) -> list[str]:
    """Sort highlights by how many JD skills they mention (stable)."""
    job_skills = {skill.lower() for skill in job.all_skills}

    def relevance(text: str) -> int:
        lowered = text.lower()
        return sum(1 for skill in job_skills if skill in lowered)

    return sorted(highlights, key=relevance, reverse=True)


def _build_resume_markdown(
    parsed: ParsedResume | None,
    candidate: CandidateProfile,
    job: ParsedJD,
    template: str,
) -> str:
    lines: list[str] = []
    name = ""
    contact = ""
    if parsed is not None:
        name = parsed.personal_info.get("name", "")
        contact = _join_contact(parsed.personal_info)
    lines.append(f"# {name or '姓名'}")
    if contact:
        lines.append(contact)
    lines.append("")

    lines.append(_section_header(template, "求职意向"))
    intent = job.title or "目标岗位"
    if job.city:
        intent = f"{intent}｜{job.city}"
    lines.append(intent)
    lines.append("")

    lines.append(_section_header(template, "核心优势"))
    years_text = (
        f"{candidate.years_experience:g} 年相关经验"
        if candidate.years_experience is not None
        else "具备相关经验"
    )
    coverage = _skill_sets(candidate, job)
    overview = f"- {years_text}，目标岗位「{job.title or '目标岗位'}」核心技能覆盖 {coverage['coverage']:.0%}"
    lines.append(overview)
    if coverage["covered"]:
        lines.append(f"- 命中核心技能：{'、'.join(coverage['covered'])}")
    highlights = _rank_highlights(candidate.highlights, job)
    for highlight in highlights[:2]:
        lines.append(f"- {highlight}")
    lines.append("")

    lines.append(_section_header(template, "专业技能"))
    if coverage["covered"] or coverage["partial"]:
        lines.append(f"- 核心技能：{'、'.join(coverage['covered'] + coverage['partial'])}")
    if coverage["preferred_covered"]:
        lines.append(f"- 加分技能：{'、'.join(coverage['preferred_covered'])}")
    known = set(coverage["covered"]) | set(coverage["partial"]) | set(coverage["preferred_covered"])
    others = [skill for skill in candidate.skills if skill not in known][:_MAX_OTHER_SKILLS]
    if others:
        lines.append(f"- 其他技能：{'、'.join(others)}")
    lines.append("")

    if parsed is not None and parsed.experience:
        lines.append(_section_header(template, "工作经历"))
        for entry in parsed.experience:
            header = " | ".join(
                str(entry.get(key, "")) for key in ("company", "title") if entry.get(key)
            )
            period = str(entry.get("period", ""))
            lines.append(f"### {header}{f' | {period}' if period else ''}")
            for highlight in _rank_highlights(
                [str(item) for item in entry.get("highlights", [])], job
            ):
                lines.append(f"- {highlight}")
            lines.append("")

    if parsed is not None and parsed.education:
        lines.append(_section_header(template, "教育背景"))
        for entry in parsed.education:
            parts = [
                str(entry.get(key, ""))
                for key in ("school", "major", "degree", "period")
                if entry.get(key)
            ]
            lines.append(f"- {'  '.join(parts)}")
        lines.append("")

    if parsed is not None and parsed.projects:
        lines.append(_section_header(template, "项目经历"))
        for entry in parsed.projects:
            name_text = str(entry.get("name", ""))
            period = str(entry.get("period", ""))
            lines.append(f"### {name_text}{f'  {period}' if period else ''}")
            for highlight in _rank_highlights(
                [str(item) for item in entry.get("highlights", [])], job
            ):
                lines.append(f"- {highlight}")
            lines.append("")

    if parsed is None:
        lines.append(_section_header(template, "工作经历"))
        lines.append("- 待补充：未提供简历文本，请提供 resume_text 或先更新用户画像")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _skill_sets(candidate: CandidateProfile, job: ParsedJD) -> dict[str, Any]:
    """Compute covered / partial / missing skill sets with a coverage ratio."""
    report = analyze_skill_gap(candidate.skills, job)
    covered = list(report.covered) + list(report.partial)
    preferred = [skill for skill in job.preferred_skills if skill in candidate.skills]
    return {
        "covered": list(report.covered),
        "partial": list(report.partial),
        "missing": list(report.missing),
        "preferred_covered": preferred,
        "coverage": report.coverage,
        "_all_covered": covered,
    }


class ResumeGenerateTool(JobHuntToolBase):
    """Draft a JD-targeted resume from resume text or the stored profile."""

    name = "resume_generate"
    description = (
        "Generate a JD-targeted resume draft (Markdown) from a source resume or "
        "the stored user profile: skills are regrouped by JD priority, highlights "
        "are re-ordered by relevance and the coverage of the job's required / "
        "preferred skills is reported. Read-only."
    )
    input_model = ResumeGenerateToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ResumeGenerateToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not arguments.jd_text.strip():
            return error_result("'jd_text' must be non-empty")
        template = arguments.template.strip().lower() or "classic"
        if template not in {"classic", "modern", "minimal"}:
            return error_result(
                f"Unknown template '{arguments.template}': use classic / modern / minimal"
            )

        job = parse_jd_text(arguments.jd_text)
        store = self.resolve_store(context)
        profile = store.load_profile()
        parsed = parse_resume_text(arguments.resume_text) if arguments.resume_text else None
        candidate = self.candidate_from_texts(arguments.resume_text, profile)

        source_kind = "resume_text" if arguments.resume_text else "profile"
        markdown = _build_resume_markdown(parsed, candidate, job, template)
        coverage = _skill_sets(candidate, job)

        tips: list[str] = []
        if coverage["missing"]:
            tips.append(
                "岗位要求但简历未体现的技能："
                + "、".join(coverage["missing"])
                + "；如具备相关经验请补充到技能清单或经历描述中"
            )
        if parsed is None and not profile:
            tips.append("未提供简历文本且用户画像为空，建议先执行 profile_update 或提供 resume_text")
        if arguments.resume_text and not (parsed and parsed.experience):
            tips.append("未从来源简历中识别出工作经历，请检查简历中的“工作经历”分节与日期格式")

        payload: dict[str, Any] = {
            "resume_markdown": markdown,
            "template": template,
            "source": source_kind,
            "coverage": {
                "required_total": len(job.required_skills),
                "covered": coverage["covered"],
                "partial": coverage["partial"],
                "missing": coverage["missing"],
                "coverage": coverage["coverage"],
            },
            "tips": tips,
        }
        return ToolResult(output=json_output(payload))


# ---------------------------------------------------------------------------
# resume_optimize
# ---------------------------------------------------------------------------


class ResumeOptimizeToolInput(BaseModel):
    """Arguments for the resume_optimize tool."""

    resume_text: str = Field(description="Resume text to optimize")
    jd_text: str | None = Field(
        default=None, description="Optional target JD for keyword alignment"
    )


def _ats_checklist(ats_payload: dict[str, Any], resume: ParsedResume) -> list[dict[str, Any]]:
    checklist: list[dict[str, Any]] = []
    for component in ats_payload["components"]:
        if component["score"] >= component["max_score"]:
            continue
        name = component["name"]
        if name == "联系方式":
            missing = []
            if not resume.personal_info.get("email"):
                missing.append("邮箱")
            if not resume.personal_info.get("phone"):
                missing.append("手机号")
            action = "补充" + "、".join(missing or ["联系方式"])
        elif name == "技能清单":
            action = "技能项不足 10 个，补充更多与目标岗位相关的技术关键词"
        elif name == "工作经历":
            action = "为经历中的成果补充量化数据（规模、百分比、性能指标）"
        elif name == "教育背景":
            action = "补充学校与学位信息"
        elif name == "结构完整":
            action = "补齐标准分节：个人信息 / 自我评价 / 教育经历 / 工作经历 / 专业技能"
        else:
            action = "根据明细改进： " + component["detail"]
        checklist.append(
            {
                "component": name,
                "score": component["score"],
                "max_score": component["max_score"],
                "action": action,
            }
        )
    return checklist


class ResumeOptimizeTool(JobHuntToolBase):
    """ATS improvement checklist with optional JD keyword alignment."""

    name = "resume_optimize"
    description = (
        "Analyse a resume's ATS friendliness and return a prioritized improvement "
        "checklist. When a target JD is provided, also reports which required "
        "keywords are covered / missing so the resume can be aligned. Read-only."
    )
    input_model = ResumeOptimizeToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ResumeOptimizeToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        if not arguments.resume_text.strip():
            return error_result("'resume_text' must be non-empty")

        resume = parse_resume_text(arguments.resume_text)
        ats = _ats_payload(resume, arguments.resume_text)
        payload: dict[str, Any] = {
            "ats": ats,
            "checklist": _ats_checklist(ats, resume),
        }

        if arguments.jd_text and arguments.jd_text.strip():
            job = parse_jd_text(arguments.jd_text)
            candidate = CandidateProfile.from_resume(resume)
            report = analyze_skill_gap(candidate.skills, job)
            payload["jd_alignment"] = {
                "target": job.title or "目标岗位",
                "covered": list(report.covered),
                "partial": list(report.partial),
                "missing": list(report.missing),
                "missing_preferred": list(report.missing_preferred),
                "coverage": report.coverage,
                "priority_actions": list(report.priority_actions),
            }
        else:
            payload["jd_alignment"] = None
        return ToolResult(output=json_output(payload), metadata={"ats_score": ats["total"]})


# ---------------------------------------------------------------------------
# resume_compare
# ---------------------------------------------------------------------------


class ResumeCompareToolInput(BaseModel):
    """Arguments for the resume_compare tool."""

    resume_a: str = Field(description="First resume version (baseline)")
    resume_b: str = Field(description="Second resume version (candidate)")
    dimensions: list[str] = Field(
        default_factory=list,
        description="Subset of: ats / skills / experience / education (default: all)",
    )


_COMPARE_DIMENSIONS = ("ats", "skills", "experience", "education")


class ResumeCompareTool(JobHuntToolBase):
    """Compare two resume versions side by side."""

    name = "resume_compare"
    description = (
        "Compare two resume versions: ATS score breakdown, skill-set diff "
        "(common / only in A / only in B), experience and education summaries, "
        "with a short recommendation on which version is stronger. Read-only."
    )
    input_model = ResumeCompareToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ResumeCompareToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        if not arguments.resume_a.strip() or not arguments.resume_b.strip():
            return error_result("Both 'resume_a' and 'resume_b' must be non-empty")

        requested = [item.strip().lower() for item in arguments.dimensions if item.strip()]
        dimensions = requested or list(_COMPARE_DIMENSIONS)
        unknown = [item for item in dimensions if item not in _COMPARE_DIMENSIONS]
        if unknown:
            return error_result(
                f"Unknown dimensions {unknown}: use {', '.join(_COMPARE_DIMENSIONS)}"
            )

        resume_a = parse_resume_text(arguments.resume_a)
        resume_b = parse_resume_text(arguments.resume_b)
        ats_a = _ats_payload(resume_a, arguments.resume_a)
        ats_b = _ats_payload(resume_b, arguments.resume_b)

        payload: dict[str, Any] = {}
        if "ats" in dimensions:
            payload["ats"] = {
                "a": ats_a,
                "b": ats_b,
                "delta": round(ats_b["total"] - ats_a["total"], 1),
            }
        if "skills" in dimensions:
            skills_a = set(resume_a.technical_skills)
            skills_b = set(resume_b.technical_skills)
            payload["skills"] = {
                "common": sorted(skills_a & skills_b),
                "only_a": sorted(skills_a - skills_b),
                "only_b": sorted(skills_b - skills_a),
                "count": {"a": len(skills_a), "b": len(skills_b)},
            }
        if "experience" in dimensions:
            payload["experience"] = {
                "a": {
                    "entries": len(resume_a.experience),
                    "years": resume_a.years_of_experience,
                    "companies": [
                        str(entry.get("company", "")) for entry in resume_a.experience
                    ],
                },
                "b": {
                    "entries": len(resume_b.experience),
                    "years": resume_b.years_of_experience,
                    "companies": [
                        str(entry.get("company", "")) for entry in resume_b.experience
                    ],
                },
            }
        if "education" in dimensions:
            payload["education"] = {
                "a": resume_a.education,
                "b": resume_b.education,
            }

        notes: list[str] = []
        delta = ats_b["total"] - ats_a["total"]
        if abs(delta) >= 1:
            stronger = "B" if delta > 0 else "A"
            notes.append(
                f"ATS 友好度：版本 {stronger} 更高（A={ats_a['total']} vs B={ats_b['total']}）"
            )
        else:
            notes.append("两版本 ATS 友好度基本持平")
        only_a = set(resume_a.technical_skills) - set(resume_b.technical_skills)
        only_b = set(resume_b.technical_skills) - set(resume_a.technical_skills)
        if only_b:
            notes.append("版本 B 新增技能：" + "、".join(sorted(only_b)))
        if only_a:
            notes.append("版本 A 独有技能：" + "、".join(sorted(only_a)))
        payload["notes"] = notes
        return ToolResult(output=json_output(payload))
