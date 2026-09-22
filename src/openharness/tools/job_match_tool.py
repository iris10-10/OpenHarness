"""Job-matching tools: job_match / candidate_match (Phase 2, read-only).

- ``job_match`` scores a candidate (resume text and/or the stored user
  profile) against job candidates — supplied inline via ``jd_texts`` or
  retrieved from the RAG ``jobs`` collection — and returns a ranked Top-K
  list with per-dimension scores, skill gaps and 冲刺 / 匹配 / 保底 advice.
- ``candidate_match`` is the reverse direction: one job posting scored
  against the resumes stored in the RAG ``resumes`` collection, optionally
  plus the local user profile.

Rankings are produced by the deterministic engine in
:mod:`openharness.jobhunt.scoring` (same weights as ``job_hunt.matching``
settings), so identical inputs always produce identical ordering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import (
    ParsedJD,
    SalaryRange,
    parse_jd_text,
    parse_resume_text,
)
from openharness.jobhunt.scoring import (
    CandidateProfile,
    MatchResult,
    MatchWeights,
    score_match,
)
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

if TYPE_CHECKING:
    from openharness.config.schema import JobHuntMatchingSettings
    from openharness.rag.retriever import RankedHit

_MAX_RETRIEVE_K = 50


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _format_salary(salary: SalaryRange | None) -> str:
    """Render a parsed salary range as ``25-40K·14薪``."""
    if salary is None:
        return ""
    text = f"{salary.min_monthly / 1000:.0f}-{salary.max_monthly / 1000:.0f}K"
    if salary.months_per_year and salary.months_per_year != 12:
        text += f"·{salary.months_per_year}薪"
    return text


def _filters_to_where(filters: dict[str, Any]) -> dict[str, Any] | None:
    """Translate tool filters into a RAG ``where`` mapping (best effort)."""
    where: dict[str, Any] = {}
    city = str(filters.get("city", "")).strip()
    if city:
        where["city"] = city
    category = str(filters.get("category", "")).strip()
    if category:
        where["category"] = category
    salary_min = _as_int(filters.get("salary_min"))
    if salary_min is not None:
        where["salary_max"] = {"$gte": salary_min}
    salary_max = _as_int(filters.get("salary_max"))
    if salary_max is not None:
        where["salary_min"] = {"$lte": salary_max}
    return where or None


def _job_passes_filters(job: ParsedJD, filters: dict[str, Any]) -> bool:
    """Apply filters locally; missing data never disqualifies a posting."""
    city = str(filters.get("city", "")).strip()
    if city and job.city and city not in job.city and job.city not in city:
        return False
    category = str(filters.get("category", "")).strip()
    if category and job.category and category not in job.category:
        return False
    salary_min = _as_int(filters.get("salary_min"))
    if salary_min is not None and job.salary is not None and job.salary.max_monthly < salary_min:
        return False
    salary_max = _as_int(filters.get("salary_max"))
    if salary_max is not None and job.salary is not None and job.salary.min_monthly > salary_max:
        return False
    keywords = [keyword.lower() for keyword in _as_str_list(filters.get("keywords"))]
    if keywords:
        haystack = f"{job.title} {job.raw_text}".lower()
        if not any(keyword in haystack for keyword in keywords):
            return False
    return True


def _job_from_hit(hit: RankedHit) -> ParsedJD:
    """Re-parse a stored job posting from a RAG hit, using metadata hints."""
    metadata = hit.metadata
    return parse_jd_text(
        hit.text,
        title_hint=str(metadata.get("title", "")),
        company_hint=str(metadata.get("company", "")),
        city_hint=str(metadata.get("city", "")),
        url=str(metadata.get("url", "")),
        posted_date=str(metadata.get("posted_date", "")),
    )


def _job_payload(job: ParsedJD, *, doc_id: str = "", source: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": job.title,
        "company": job.company,
        "city": job.city,
        "salary": _format_salary(job.salary),
        "experience": job.experience,
        "education": job.education,
        "category": job.category or "",
        "posted_date": job.posted_date,
        "url": job.url,
    }
    if doc_id:
        payload["doc_id"] = doc_id
    if source:
        payload["source"] = source
    return payload


def _match_weights(matching: JobHuntMatchingSettings) -> MatchWeights:
    """Build matching weights from the ``job_hunt.matching`` config section."""
    return MatchWeights(
        skills=matching.weight_skills,
        experience=matching.weight_experience,
        education=matching.weight_education,
        projects=matching.weight_projects,
        soft_skills=matching.weight_soft_skills,
        other=matching.weight_other,
    )


def _score(
    job: ParsedJD, candidate: CandidateProfile, matching: JobHuntMatchingSettings
) -> MatchResult:
    """Score one candidate-job pair with the configured weights and thresholds."""
    return score_match(
        candidate,
        job,
        weights=_match_weights(matching),
        freshness_days=matching.freshness_days,
        freshness_boost=matching.freshness_boost,
        match_threshold=matching.match_threshold,
        safety_threshold=matching.safety_threshold,
    )


def _dimensions_payload(result: MatchResult) -> list[dict[str, Any]]:
    return [
        {
            "name": dimension.name,
            "score": dimension.score,
            "weight": dimension.weight,
            "detail": dimension.detail,
        }
        for dimension in result.dimensions
    ]


def _candidate_summary(candidate: CandidateProfile) -> dict[str, Any]:
    return {
        "top_skills": candidate.skills[:10],
        "skill_count": len(candidate.skills),
        "years_of_experience": candidate.years_experience,
        "current_title": candidate.current_title,
        "target_cities": candidate.target_cities,
        "expected_salary_min": candidate.expected_salary_min,
        "expected_salary_max": candidate.expected_salary_max,
    }


def _candidate_query(candidate: CandidateProfile) -> str:
    """Build a retrieval query from the candidate's title and skills."""
    parts: list[str] = []
    if candidate.current_title:
        parts.append(candidate.current_title)
    parts.extend(candidate.skills[:8])
    return " ".join(parts) or "工程师"


# ---------------------------------------------------------------------------
# job_match
# ---------------------------------------------------------------------------


class JobMatchToolInput(BaseModel):
    """Arguments for the job_match tool."""

    resume_text: str | None = Field(
        default=None,
        description="Resume text; falls back to the stored user profile when omitted",
    )
    jd_texts: list[str] = Field(
        default_factory=list,
        description=(
            "Inline job descriptions to match against; when non-empty the RAG "
            "'jobs' collection is not queried"
        ),
    )
    query: str = Field(
        default="",
        description="Extra retrieval query for the RAG mode (defaults to title + skills)",
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional filters: city / category / salary_min (CNY monthly, a posting "
            "must be able to reach it) / salary_max / keywords (substring list)"
        ),
    )
    top_k: int = Field(
        default=0, ge=0, le=50, description="Maximum matches to return (0 = configured default)"
    )


class JobMatchTool(JobHuntToolBase):
    """Rank jobs against the candidate and return Top-K matches."""

    name = "job_match"
    description = (
        "Match the candidate (resume text and/or the stored user profile) against "
        "jobs — inline jd_texts or postings retrieved from the RAG 'jobs' collection "
        "— and return a ranked Top-K list with per-dimension scores, covered / "
        "partial / missing skills and sprint / match / safety triage advice. "
        "Deterministic and read-only."
    )
    input_model = JobMatchToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: JobMatchToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        profile = self.resolve_profile(context)
        has_resume = bool(arguments.resume_text and arguments.resume_text.strip())
        if not has_resume and not profile:
            return error_result(
                "找不到候选人信息：请提供 resume_text，或先调用 profile_update 建立用户画像"
            )
        candidate = self.candidate_from_texts(arguments.resume_text, profile)
        if (
            not candidate.skills
            and candidate.years_experience is None
            and not candidate.current_title
        ):
            return error_result(
                "候选人信息为空：resume_text 未解析出技能与经历，且画像中没有相关数据"
            )

        matching = self.job_hunt_settings().matching
        top_k = arguments.top_k or matching.top_k
        notes: list[str] = []
        entries: list[tuple[ParsedJD, str]] = []

        inline_jobs = [text for text in arguments.jd_texts if text and text.strip()]
        if inline_jobs:
            source = "inline"
            entries = [
                (parse_jd_text(text), f"inline-{index}")
                for index, text in enumerate(inline_jobs, start=1)
            ]
        else:
            source = "rag"
            query = arguments.query.strip() or _candidate_query(candidate)
            retrieve_k = min(_MAX_RETRIEVE_K, max(top_k * 3, 10))
            try:
                retriever = self.resolve_retriever()
                outcome = await retriever.retrieve(
                    query,
                    collection="jobs",
                    where=_filters_to_where(arguments.filters),
                    top_n=retrieve_k,
                )
            #RAG 检索失败时给出可执行的替代路径，避免静默空结果
            except Exception as exc:  # noqa: BLE001
                return error_result(
                    f"岗位检索失败：{exc}。可改用 jd_texts 直接传入岗位文本，"
                    "或先运行 jd_parse 将 JD 入库后重试"
                )
            entries = [(_job_from_hit(hit), hit.id) for hit in outcome.hits]
            notes.extend(str(note) for note in outcome.notes)

        filtered = [
            (job, doc_id)
            for job, doc_id in entries
            if _job_passes_filters(job, arguments.filters)
        ]
        removed = len(entries) - len(filtered)
        if removed:
            notes.append(f"按 filters 过滤掉 {removed} 个岗位")

        if not filtered:
            hint = (
                "没有可评估的岗位：请提供 jd_texts，或先运行 jd_parse 将 JD 入库"
                if not entries
                else "所有岗位都被 filters 过滤：请放宽 city / salary / keywords 条件"
            )
            return ToolResult(
                output=json_output(
                    {
                        "candidate_summary": _candidate_summary(candidate),
                        "source": source,
                        "filters": dict(arguments.filters),
                        "evaluated": 0,
                        "matches": [],
                        "notes": [*notes, hint],
                    }
                )
            )

        scored = [
            (_score(job, candidate, matching), job, doc_id)
            for job, doc_id in filtered
        ]
        scored.sort(key=lambda item: item[0].total, reverse=True)

        matches: list[dict[str, Any]] = []
        for rank, (result, job, doc_id) in enumerate(scored[:top_k], start=1):
            entry: dict[str, Any] = {
                "rank": rank,
                "score": result.total,
                "recommendation": result.recommendation,
                "freshness_applied": result.freshness_applied,
                "skill_coverage": result.skill_coverage,
                "dimensions": _dimensions_payload(result),
                "covered_skills": list(result.covered_skills),
                "partial_skills": list(result.partial_skills),
                "missing_skills": list(result.missing_skills),
                "job": _job_payload(job, doc_id=doc_id, source=source),
            }
            matches.append(entry)

        payload: dict[str, Any] = {
            "candidate_summary": _candidate_summary(candidate),
            "source": source,
            "filters": dict(arguments.filters),
            "evaluated": len(filtered),
            "matches": matches,
        }
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"match_count": len(matches), "top_score": matches[0]["score"]},
        )


# ---------------------------------------------------------------------------
# candidate_match
# ---------------------------------------------------------------------------


class CandidateMatchToolInput(BaseModel):
    """Arguments for the candidate_match tool."""

    job_text: str = Field(description="Job description text to match candidates against")
    top_k: int = Field(
        default=0, ge=0, le=50, description="Maximum candidates to return (0 = configured default)"
    )
    include_profile: bool = Field(
        default=True,
        description="Include the locally stored user profile as a candidate",
    )


class CandidateMatchTool(JobHuntToolBase):
    """Rank stored resumes against one job (reverse matching)."""

    name = "candidate_match"
    description = (
        "Score one job posting against the candidate pool: resumes stored in the "
        "RAG 'resumes' collection plus, optionally, the local user profile. "
        "Returns candidates ranked by the weighted matching engine with skill "
        "coverage details. Read-only."
    )
    input_model = CandidateMatchToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: CandidateMatchToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not arguments.job_text.strip():
            return error_result("'job_text' must be non-empty")
        job = parse_jd_text(arguments.job_text)
        matching = self.job_hunt_settings().matching
        top_k = arguments.top_k or matching.top_k
        notes: list[str] = []

        pool: list[tuple[str, str, CandidateProfile, dict[str, Any]]] = []
        retrieve_k = min(_MAX_RETRIEVE_K, max(top_k * 2, 10))
        query = " ".join([job.title, *job.required_skills[:8]]).strip() or "简历"
        try:
            retriever = self.resolve_retriever()
            outcome = await retriever.retrieve(query, collection="resumes", top_n=retrieve_k)
        #RAG 不可用时仍可评估本地画像，记录提示而不中断
        except Exception as exc:  # noqa: BLE001
            notes.append(f"RAG resumes 检索不可用：{exc}")
        else:
            for hit in outcome.hits:
                parsed = parse_resume_text(hit.text)
                candidate = CandidateProfile.from_resume(parsed)
                label = str(hit.metadata.get("name", "")).strip() or hit.id
                pool.append(
                    (
                        label,
                        "rag",
                        candidate,
                        {
                            "doc_id": hit.id,
                            "top_skills": parsed.technical_skills[:8],
                            "years_of_experience": parsed.years_of_experience,
                        },
                    )
                )
            if not outcome.hits and outcome.notes:
                notes.extend(str(note) for note in outcome.notes)

        if arguments.include_profile:
            profile = self.resolve_profile(context)
            if profile:
                candidate = CandidateProfile.from_profile_dict(profile)
                if candidate.skills or candidate.years_experience is not None:
                    pool.append(
                        (
                            "本地画像",
                            "profile",
                            candidate,
                            {
                                "top_skills": candidate.skills[:8],
                                "years_of_experience": candidate.years_experience,
                            },
                        )
                    )

        if not pool:
            return ToolResult(
                output=json_output(
                    {
                        "job": _job_payload(job),
                        "candidates": [],
                        "pool_size": 0,
                        "notes": [
                            *notes,
                            (
                                "候选池为空：先用 resume_parse（store_to_rag=true）入库简历，"
                                "或 profile_update 建立画像"
                            ),
                        ],
                    }
                )
            )

        scored = [
            (_score(job, candidate, matching), label, source, summary)
            for label, source, candidate, summary in pool
        ]
        scored.sort(key=lambda item: item[0].total, reverse=True)

        candidates: list[dict[str, Any]] = []
        for rank, (result, label, source, summary) in enumerate(scored[:top_k], start=1):
            candidates.append(
                {
                    "rank": rank,
                    "candidate": label,
                    "source": source,
                    "resume_summary": summary,
                    "score": result.total,
                    "recommendation": result.recommendation,
                    "skill_coverage": result.skill_coverage,
                    "covered_skills": list(result.covered_skills),
                    "partial_skills": list(result.partial_skills),
                    "missing_skills": list(result.missing_skills),
                    "dimensions": _dimensions_payload(result),
                }
            )

        payload: dict[str, Any] = {
            "job": _job_payload(job),
            "pool_size": len(pool),
            "candidates": candidates,
        }
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"candidate_count": len(candidates)},
        )
