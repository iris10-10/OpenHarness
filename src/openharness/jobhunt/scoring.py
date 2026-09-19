"""Deterministic scoring engines for the job-hunt tools (Phase 2).

All engines in this module are pure functions over the parsed structures from
:mod:`openharness.jobhunt.parsing`:

- :func:`score_resume_ats` — ATS friendliness rubric (0-100, itemised).
- :func:`score_match` — weighted candidate-job matching (skills 35% /
  experience 20% / education 10% / projects 15% / soft skills 10% /
  other 10%) with a freshness boost for recently posted jobs.
- :func:`analyze_skill_gap` — covered / partial / missing skill breakdown.
- :func:`evaluate_interview_answer` — heuristic answer quality feedback.

The heuristics are intentionally documented and prompt-independent: the LLM
turns these structured scores into prose, so the numbers stay reproducible
and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from openharness.jobhunt.parsing import (
    SOFT_SKILLS,
    ParsedJD,
    ParsedResume,
    SalaryRange,
    are_skills_related,
    count_quantified_lines,
    count_quantified_tokens,
    degree_level,
    experience_bounds,
    extract_skills,
    normalize_skill,
    split_sections,
)

__all__ = [
    "AnswerEvaluation",
    "AtsScore",
    "CandidateProfile",
    "MatchResult",
    "MatchWeights",
    "SkillGapReport",
    "analyze_skill_gap",
    "evaluate_interview_answer",
    "score_match",
    "score_resume_ats",
]

_SOFT_SKILL_SET = set(SOFT_SKILLS)

# ---------------------------------------------------------------------------
# ATS 评分
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"(待补充|待完善|xxx|XXX|示例文本|填写)")

_ATS_SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("personal", "个人信息"),
    ("summary", "自我评价"),
    ("education", "教育经历"),
    ("experience", "工作经历"),
    ("skills", "专业技能"),
)


@dataclass(frozen=True)
class AtsComponent:
    """One itemised ATS rubric component."""

    name: str
    score: float
    max_score: float
    detail: str


@dataclass(frozen=True)
class AtsScore:
    """ATS friendliness score with an itemised breakdown (total 0-100)."""

    total: float
    components: tuple[AtsComponent, ...]


def score_resume_ats(resume: ParsedResume, raw_text: str) -> AtsScore:
    """Score resume ATS friendliness on a documented 100-point rubric.

    Rubric: contact info 15 (email 8 + phone 7); skill list 20;
    work experience 20 (presence 8 + quantified highlights 12);
    education 10; section structure 20 (5 sections x 4);
    content quality 15 (length 5 + bullet highlights 5 + no placeholders 5).
    """
    components: list[AtsComponent] = []

    contact = resume.personal_info
    email_score = 8.0 if contact.get("email") else 0.0
    phone_score = 7.0 if contact.get("phone") else 0.0
    components.append(
        AtsComponent(
            "联系方式",
            email_score + phone_score,
            15.0,
            f"email={'有' if contact.get('email') else '缺失'}, "
            f"phone={'有' if contact.get('phone') else '缺失'}",
        )
    )

    skill_count = len(resume.technical_skills)
    components.append(
        AtsComponent(
            "技能清单",
            float(min(skill_count, 10) * 2),
            20.0,
            f"识别到 {skill_count} 项专业技能",
        )
    )

    highlights: list[str] = []
    for entry in resume.experience:
        highlights.extend(str(item) for item in entry.get("highlights", []))
    quantified = count_quantified_lines(highlights)
    experience_score = (8.0 if resume.experience else 0.0) + float(min(quantified, 4) * 3)
    components.append(
        AtsComponent(
            "工作经历",
            experience_score,
            20.0,
            f"经历 {len(resume.experience)} 段，量化成果 {quantified} 条",
        )
    )

    education_score = 0.0
    if resume.education:
        best = resume.education[0]
        if best.get("school") and best.get("degree"):
            education_score = 10.0
        else:
            education_score = 5.0
    components.append(
        AtsComponent(
            "教育背景",
            education_score,
            10.0,
            f"教育经历 {len(resume.education)} 段",
        )
    )

    sections = split_sections(raw_text)
    present = sum(
        1 for key, _ in _ATS_SECTION_LABELS if sections.get(key)
    )
    components.append(
        AtsComponent(
            "结构完整",
            float(present * 4),
            20.0,
            f"命中 {present}/5 个标准分节",
        )
    )

    quality = 0.0
    quality_notes: list[str] = []
    text_length = len(raw_text.strip())
    if 300 <= text_length <= 5000:
        quality += 5.0
        quality_notes.append("篇幅适中")
    else:
        quality_notes.append(f"篇幅 {text_length} 字符（建议 300-5000）")
    if len(highlights) >= 3:
        quality += 5.0
        quality_notes.append("要点充分")
    else:
        quality_notes.append(f"要点仅 {len(highlights)} 条")
    if _PLACEHOLDER_RE.search(raw_text):
        quality_notes.append("含占位符/示例文本")
    else:
        quality += 5.0
    components.append(AtsComponent("内容质量", quality, 15.0, "；".join(quality_notes)))

    total = round(sum(component.score for component in components), 1)
    return AtsScore(total=total, components=tuple(components))


# ---------------------------------------------------------------------------
# 候选人画像
# ---------------------------------------------------------------------------


@dataclass
class CandidateProfile:
    """Normalized candidate view used by the matching engine."""

    skills: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    years_experience: float | None = None
    education_level: int | None = None
    education_text: str = ""
    projects_text: str = ""
    highlights: list[str] = field(default_factory=list)
    target_cities: list[str] = field(default_factory=list)
    expected_salary_min: int | None = None
    expected_salary_max: int | None = None
    current_title: str = ""

    @classmethod
    def from_resume(cls, resume: ParsedResume) -> CandidateProfile:
        """Build a candidate profile from parsed resume data."""
        project_chunks: list[str] = []
        for entry in resume.projects:
            project_chunks.append(str(entry.get("name", "")))
            project_chunks.extend(str(item) for item in entry.get("highlights", []))
        highlights: list[str] = []
        for entry in resume.experience:
            highlights.extend(str(item) for item in entry.get("highlights", []))
        education_text = ""
        education_level = None
        if resume.education:
            first = resume.education[0]
            education_text = " ".join(
                str(first.get(key, "")) for key in ("school", "degree", "major")
            ).strip()
            education_level = degree_level(str(first.get("degree", "")) or None)
        current_title = ""
        if resume.experience:
            current_title = str(resume.experience[0].get("title", ""))
        return cls(
            skills=_normalize_skill_list(resume.technical_skills),
            soft_skills=list(resume.soft_skills),
            languages=list(resume.languages),
            years_experience=resume.years_of_experience,
            education_level=education_level,
            education_text=education_text,
            projects_text="\n".join(project_chunks),
            highlights=highlights + [str(item) for item in resume.awards],
            current_title=current_title,
        )

    @classmethod
    def from_profile_dict(cls, profile: dict[str, Any]) -> CandidateProfile:
        """Build a candidate profile from the stored user-profile mapping."""
        basic = _as_dict(profile.get("basic"))
        skills_block = _as_dict(profile.get("skills"))
        education = _as_dict(profile.get("education"))
        preferences = _as_dict(profile.get("preferences"))

        technical: list[str] = []
        for item in _as_list(skills_block.get("technical")):
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
            else:
                name = str(item).strip()
            if name:
                technical.append(name)
        target_cities = [str(city) for city in _as_list(basic.get("target_cities")) if str(city).strip()]
        current_city = str(basic.get("current_city", "")).strip()
        if current_city and current_city not in target_cities:
            target_cities.insert(0, current_city)

        years_raw = basic.get("years_of_experience")
        years = float(years_raw) if isinstance(years_raw, (int, float)) else None

        degree_text = str(education.get("degree", "")).strip()
        return cls(
            skills=_normalize_skill_list(technical),
            soft_skills=[],
            languages=[],
            years_experience=years,
            education_level=degree_level(degree_text) if degree_text else None,
            education_text=_join_education(education),
            target_cities=target_cities,
            expected_salary_min=_as_int(preferences.get("expected_salary_min")),
            expected_salary_max=_as_int(preferences.get("expected_salary_max")),
            current_title=str(basic.get("current_title", "")).strip(),
        )

    def merged_with_profile(self, profile: dict[str, Any]) -> CandidateProfile:
        """Overlay a stored user profile on this resume-derived profile."""
        other = CandidateProfile.from_profile_dict(profile)
        skills = list(self.skills)
        for skill in other.skills:
            if skill not in skills:
                skills.append(skill)
        soft = list(self.soft_skills)
        for skill in other.soft_skills:
            if skill not in soft:
                soft.append(skill)
        return CandidateProfile(
            skills=skills,
            soft_skills=soft,
            languages=list(self.languages),
            years_experience=(
                other.years_experience if other.years_experience is not None else self.years_experience
            ),
            education_level=(
                other.education_level if other.education_level is not None else self.education_level
            ),
            education_text=other.education_text or self.education_text,
            projects_text=self.projects_text,
            highlights=list(self.highlights),
            target_cities=other.target_cities or list(self.target_cities),
            expected_salary_min=other.expected_salary_min or self.expected_salary_min,
            expected_salary_max=other.expected_salary_max or self.expected_salary_max,
            current_title=other.current_title or self.current_title,
        )


def _normalize_skill_list(skills: list[str]) -> list[str]:
    normalized: list[str] = []
    for skill in skills:
        canonical = normalize_skill(skill)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _join_education(education: dict[str, Any]) -> str:
    parts = [str(education.get(key, "")).strip() for key in ("school", "degree", "major")]
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# 匹配引擎
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchWeights:
    """Matching dimension weights (auto-normalized on use)."""

    skills: float = 0.35
    experience: float = 0.20
    education: float = 0.10
    projects: float = 0.15
    soft_skills: float = 0.10
    other: float = 0.10

    def normalized(self) -> MatchWeights:
        total = self.skills + self.experience + self.education + self.projects + self.soft_skills + self.other
        if total <= 0:
            return MatchWeights()
        return MatchWeights(
            skills=self.skills / total,
            experience=self.experience / total,
            education=self.education / total,
            projects=self.projects / total,
            soft_skills=self.soft_skills / total,
            other=self.other / total,
        )


@dataclass(frozen=True)
class DimensionScore:
    """A single matching dimension result."""

    name: str
    score: float
    weight: float
    detail: str


@dataclass(frozen=True)
class MatchResult:
    """Weighted matching result between a candidate and a job."""

    total: float
    dimensions: tuple[DimensionScore, ...]
    covered_skills: tuple[str, ...]
    partial_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    recommendation: str
    freshness_applied: bool
    skill_coverage: float


def _skill_coverage(
    candidate_skills: list[str], required: list[str]
) -> tuple[float, list[str], list[str], list[str]]:
    """Return weighted coverage plus covered / partial / missing lists."""
    if not required:
        return 1.0, [], [], []
    candidate_set = set(candidate_skills)
    covered: list[str] = []
    partial: list[str] = []
    missing: list[str] = []
    score = 0.0
    for skill in required:
        if skill in candidate_set:
            covered.append(skill)
            score += 1.0
        elif any(are_skills_related(skill, owned) for owned in candidate_set):
            partial.append(skill)
            score += 0.6
        else:
            missing.append(skill)
    return score / len(required), covered, partial, missing


def _technical_job_skills(job: ParsedJD) -> tuple[list[str], list[str]]:
    required = [skill for skill in job.required_skills if skill not in _SOFT_SKILL_SET]
    preferred = [skill for skill in job.preferred_skills if skill not in _SOFT_SKILL_SET]
    return required, preferred


def _score_skills_dimension(
    candidate: CandidateProfile, job: ParsedJD
) -> tuple[float, str, list[str], list[str], list[str]]:
    required, preferred = _technical_job_skills(job)
    if not required and not preferred:
        #JD 未提取到技能：退化为全文技能重叠，仍无数据则给中性分
        fallback = [skill for skill in extract_skills(job.raw_text) if skill not in _SOFT_SKILL_SET]
        if not fallback:
            return 60.0, "JD 未提及明确技能要求，按中性分处理", [], [], []
        required = fallback
    coverage, covered, partial, missing = _skill_coverage(candidate.skills, required)
    score = coverage
    detail = f"必备技能覆盖 {coverage:.0%}（{len(covered)} 项命中 / {len(partial)} 项相近 / {len(missing)} 项缺失）"
    if preferred:
        preferred_coverage, _, _, _ = _skill_coverage(candidate.skills, preferred)
        score = 0.8 * coverage + 0.2 * preferred_coverage
        detail += f"；加分技能覆盖 {preferred_coverage:.0%}"
    return round(score * 100, 1), detail, covered, partial, missing


def _score_experience_dimension(candidate: CandidateProfile, job: ParsedJD) -> tuple[float, str]:
    bounds = experience_bounds(job.experience)
    if bounds is None or candidate.years_experience is None:
        return 70.0, "经验要求或候选人年限缺失，按中性分处理"
    min_years, max_years = bounds
    years = candidate.years_experience
    if years < min_years:
        score = max(0.0, 100.0 - (min_years - years) * 25.0)
        detail = f"要求 {job.experience}，候选 {years} 年，低于门槛"
    elif years > max_years:
        score = max(60.0, 100.0 - (years - max_years) * 5.0)
        detail = f"要求 {job.experience}，候选 {years} 年，资历略超"
    else:
        score = 100.0
        detail = f"要求 {job.experience}，候选 {years} 年，符合区间"
    return round(score, 1), detail


def _score_education_dimension(candidate: CandidateProfile, job: ParsedJD) -> tuple[float, str]:
    required = degree_level(job.education)
    if required is None or required == 0:
        return 100.0, f"学历要求：{job.education or '未要求'}"
    if candidate.education_level is None:
        return 60.0, f"要求 {job.education}，候选人学历未识别，按中性分处理"
    diff = candidate.education_level - required
    if diff >= 0:
        score = 100.0
        detail = f"要求 {job.education}，候选人学历达标"
    else:
        score = max(0.0, 100.0 + diff * 50.0)
        detail = f"要求 {job.education}，候选人学历低 {abs(diff)} 档"
    return score, detail


def _score_projects_dimension(candidate: CandidateProfile, job: ParsedJD) -> tuple[float, str]:
    text = "\n".join([candidate.projects_text] + candidate.highlights).strip()
    if not text:
        return 40.0, "简历缺少项目/经历描述，无法评估相关性"
    required, _ = _technical_job_skills(job)
    top_job_skills = required[:10]
    project_skills = set(extract_skills(text))
    if not top_job_skills:
        return 70.0, "JD 未提取到技能关键词，按中性分处理"
    hits = 0.0
    for skill in top_job_skills:
        if skill in project_skills:
            hits += 1.0
        elif any(are_skills_related(skill, owned) for owned in project_skills):
            hits += 0.5
    coverage = hits / len(top_job_skills)
    quantified = min(count_quantified_lines(text.splitlines()), 4) / 4.0
    score = 100.0 * (0.75 * coverage + 0.25 * quantified)
    detail = f"项目/经历覆盖岗位技能 {coverage:.0%}，量化成果占比 {quantified:.0%}"
    return round(score, 1), detail


def _score_soft_dimension(candidate: CandidateProfile, job: ParsedJD) -> tuple[float, str]:
    job_soft = [skill for skill in job.all_skills if skill in _SOFT_SKILL_SET]
    if not job_soft:
        return 70.0, "JD 未提及软技能要求，按中性分处理"
    owned = set(candidate.soft_skills) | set(candidate.languages)
    sold = [skill for skill in job_soft if skill in owned]
    score = len(sold) / len(job_soft) * 100.0
    detail = f"软技能命中 {len(sold)}/{len(job_soft)}"
    return round(score, 1), detail


def _score_other_dimension(candidate: CandidateProfile, job: ParsedJD) -> tuple[float, str]:
    score = 0.0
    notes: list[str] = []
    if not job.city or job.city in {"远程"}:
        score += 60.0
        notes.append("城市不限")
    elif not candidate.target_cities:
        score += 30.0
        notes.append("候选人未设置目标城市")
    elif job.city in candidate.target_cities:
        score += 60.0
        notes.append(f"城市匹配（{job.city}）")
    else:
        notes.append(f"城市不匹配（岗位 {job.city}，期望 {'/'.join(candidate.target_cities)}）")

    salary_note = _salary_expectation_note(candidate, job.salary)
    score += salary_note[0]
    notes.append(salary_note[1])
    return round(score, 1), "；".join(notes)


def _salary_expectation_note(
    candidate: CandidateProfile, salary: SalaryRange | None
) -> tuple[float, str]:
    exp_min = candidate.expected_salary_min
    exp_max = candidate.expected_salary_max
    if exp_min is None and exp_max is None:
        return 20.0, "未设置薪资期望"
    if salary is None:
        return 20.0, "岗位未标注薪资"
    low = exp_min if exp_min is not None else 0
    high = exp_max if exp_max is not None else 10**9
    if salary.max_monthly >= low and salary.min_monthly <= high:
        return 40.0, "薪资区间与期望有重叠"
    if salary.max_monthly >= low * 0.8:
        return 20.0, "薪资与期望接近但未完全重叠"
    return 0.0, "薪资低于期望区间"


def _freshness_applied(job: ParsedJD, freshness_days: int) -> bool:
    if not job.posted_date:
        return False
    try:
        posted = date.fromisoformat(job.posted_date.strip()[:10])
    except ValueError:
        return False
    age = (datetime.now().astimezone().date() - posted).days
    return 0 <= age <= freshness_days


def score_match(
    candidate: CandidateProfile,
    job: ParsedJD,
    *,
    weights: MatchWeights | None = None,
    freshness_days: int = 30,
    freshness_boost: float = 0.10,
    match_threshold: float = 65.0,
    safety_threshold: float = 80.0,
) -> MatchResult:
    """Score a candidate against a job on the documented weighted rubric."""
    normalized = (weights or MatchWeights()).normalized()

    skills_score, skills_detail, covered, partial, missing = _score_skills_dimension(candidate, job)
    experience_score, experience_detail = _score_experience_dimension(candidate, job)
    education_score, education_detail = _score_education_dimension(candidate, job)
    projects_score, projects_detail = _score_projects_dimension(candidate, job)
    soft_score, soft_detail = _score_soft_dimension(candidate, job)
    other_score, other_detail = _score_other_dimension(candidate, job)

    dimensions = (
        DimensionScore("技能匹配", skills_score, normalized.skills, skills_detail),
        DimensionScore("经验匹配", experience_score, normalized.experience, experience_detail),
        DimensionScore("学历匹配", education_score, normalized.education, education_detail),
        DimensionScore("项目匹配", projects_score, normalized.projects, projects_detail),
        DimensionScore("软技能匹配", soft_score, normalized.soft_skills, soft_detail),
        DimensionScore("其他因素", other_score, normalized.other, other_detail),
    )
    total = sum(dimension.score * dimension.weight for dimension in dimensions)

    boosted = _freshness_applied(job, freshness_days)
    if boosted:
        total = min(100.0, total * (1.0 + freshness_boost))
    total = round(total, 1)

    required_skills, _ = _technical_job_skills(job)
    if required_skills:
        coverage, _, _, _ = _skill_coverage(candidate.skills, required_skills)
    else:
        coverage = 1.0
    recommendation = classify_recommendation(
        total,
        coverage=coverage,
        experience_ok=_meets_experience(candidate, job),
        education_ok=_meets_education(candidate, job),
        match_threshold=match_threshold,
        safety_threshold=safety_threshold,
    )
    return MatchResult(
        total=total,
        dimensions=dimensions,
        covered_skills=tuple(covered),
        partial_skills=tuple(partial),
        missing_skills=tuple(missing),
        recommendation=recommendation,
        freshness_applied=boosted,
        skill_coverage=round(coverage, 3),
    )


def _meets_experience(candidate: CandidateProfile, job: ParsedJD) -> bool:
    bounds = experience_bounds(job.experience)
    if bounds is None or candidate.years_experience is None:
        return True
    return candidate.years_experience >= bounds[0]


def _meets_education(candidate: CandidateProfile, job: ParsedJD) -> bool:
    required = degree_level(job.education)
    if required is None or required == 0 or candidate.education_level is None:
        return True
    return candidate.education_level >= required


def classify_recommendation(
    total: float,
    *,
    coverage: float,
    experience_ok: bool,
    education_ok: bool,
    match_threshold: float,
    safety_threshold: float,
) -> str:
    """Map a match score to 冲刺 / 匹配 / 保底 triage advice.

    ``保底`` requires a high score *and* all hard requirements met with a
    comfortable skill-coverage margin (>= 90%).
    """
    if total >= safety_threshold and coverage >= 0.9 and experience_ok and education_ok:
        return "保底"
    if total >= match_threshold:
        return "匹配"
    return "冲刺"


# ---------------------------------------------------------------------------
# 技能差距分析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillGapReport:
    """Skill-gap breakdown between a candidate and a job."""

    covered: tuple[str, ...]
    partial: tuple[str, ...]
    missing: tuple[str, ...]
    missing_preferred: tuple[str, ...]
    coverage: float
    priority_actions: tuple[str, ...]


def analyze_skill_gap(candidate_skills: list[str], job: ParsedJD) -> SkillGapReport:
    """Compare candidate skills against required / preferred job skills."""
    required, preferred = _technical_job_skills(job)
    required_coverage, covered, partial, missing = _skill_coverage(candidate_skills, required)
    _, _, _, missing_preferred = _skill_coverage(candidate_skills, preferred)

    actions: list[str] = []
    for skill in list(missing)[:5]:
        related = _closest_related(skill, candidate_skills)
        if related:
            actions.append(f"高优先级补齐「{skill}」：已有相近技能「{related}」，建议通过小项目快速迁移")
        else:
            actions.append(f"高优先级补齐「{skill}」：系统学习 + 在简历中补充相关项目或关键词")
    for skill in list(missing_preferred)[:3]:
        actions.append(f"加分项「{skill}」：可在面试中表达学习计划，或在简历中标注入门水平")

    return SkillGapReport(
        covered=tuple(covered),
        partial=tuple(partial),
        missing=tuple(missing),
        missing_preferred=tuple(missing_preferred),
        coverage=round(required_coverage, 3),
        priority_actions=tuple(actions),
    )


def _closest_related(target: str, owned: list[str]) -> str | None:
    for skill in owned:
        if are_skills_related(target, skill):
            return skill
    return None


# ---------------------------------------------------------------------------
# 面试回答评估
# ---------------------------------------------------------------------------

_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}")
_STRUCTURE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("背景", "情境", "任务", "目标", "需求", "问题"),
    ("做法", "行动", "措施", "方法", "通过", "首先", "其次", "然后", "接着", "负责"),
    ("结果", "最终", "因此", "于是", "成效", "收益", "提升", "降低", "达成", "增长"),
    ("总结", "复盘", "反思", "收获", "改进"),
)
_TRANSITION_WORDS: tuple[str, ...] = ("首先", "其次", "然后", "最后", "因为", "所以", "同时", "另外")


@dataclass(frozen=True)
class AnswerDimension:
    """One interview-answer evaluation dimension."""

    name: str
    score: float
    max_score: float
    advice: str


@dataclass(frozen=True)
class AnswerEvaluation:
    """Heuristic evaluation of an interview answer."""

    score: float
    dimensions: tuple[AnswerDimension, ...]
    missing_keywords: tuple[str, ...]
    suggestions: tuple[str, ...]


def _question_keywords(question: str) -> list[str]:
    keywords = list(extract_skills(question))
    for match in _ASCII_WORD_RE.finditer(question.lower()):
        token = match.group(0)
        if token not in keywords and len(token) >= 2:
            keywords.append(token)
    return keywords[:12]


def evaluate_interview_answer(
    question: str,
    answer: str,
    *,
    round_kind: str = "技术",
) -> AnswerEvaluation:
    """Evaluate an interview answer with a documented heuristic rubric.

    Rubric: content coverage 40 (question keywords), structure 30
    (background/action/result/reflection markers), quantified detail 20,
    expression 10 (length band + transition words). The result is guidance,
    not a verdict — the LLM should present it as constructive feedback.
    """
    stripped = answer.strip()
    dimensions: list[AnswerDimension] = []
    suggestions: list[str] = []

    keywords = _question_keywords(question)
    missing: list[str] = []
    if keywords:
        hit = [keyword for keyword in keywords if keyword.lower() in stripped.lower()]
        missing = [keyword for keyword in keywords if keyword not in hit]
        coverage_score = len(hit) / len(keywords) * 40.0
        advice = (
            "回答覆盖了问题的核心关键词"
            if coverage_score >= 32
            else "建议围绕问题关键词展开，先给出结论再补充细节"
        )
    else:
        coverage_score = min(32.0, len(stripped) / 120.0 * 32.0)
        advice = "问题未识别出明确关键词，按回答展开度评分"
    dimensions.append(AnswerDimension("内容覆盖", round(coverage_score, 1), 40.0, advice))

    lower = stripped.lower()
    groups_present = sum(
        1 for group in _STRUCTURE_GROUPS if any(marker in lower for marker in group)
    )
    structure_score = groups_present * 7.5
    if round_kind == "行为":
        structure_advice = "行为面试建议按 STAR（背景-任务-行动-结果）完整展开"
    else:
        structure_advice = "建议按“背景/问题 → 做法 → 结果”结构作答，突出取舍与权衡"
    if structure_score < 22.5:
        suggestions.append(structure_advice)
    dimensions.append(
        AnswerDimension("回答结构", round(structure_score, 1), 30.0, structure_advice)
    )

    quantified = count_quantified_tokens(stripped)
    detail_score = min(20.0, quantified * 7.0)
    detail_advice = (
        "有量化细节，继续保持"
        if detail_score >= 14
        else "补充量化结果（如提升 30%、耗时从 2s 降到 200ms）更有说服力"
    )
    if detail_score < 14:
        suggestions.append(detail_advice)
    dimensions.append(AnswerDimension("细节与量化", round(detail_score, 1), 20.0, detail_advice))

    length = len(stripped)
    if length < 30:
        length_score = 2.0
    elif length < 60:
        length_score = 5.0
    elif length <= 1000:
        length_score = 6.0
    else:
        length_score = 4.0
    transition_score = 4.0 if any(word in lower for word in _TRANSITION_WORDS) else 0.0
    expression_score = length_score + transition_score
    expression_advice = (
        "表达长度适中"
        if 60 <= length <= 1000
        else "回答过短，建议展开到 1-3 分钟口述量；过长则先给结论"
    )
    dimensions.append(
        AnswerDimension("表达与条理", round(expression_score, 1), 10.0, expression_advice)
    )

    if missing:
        suggestions.append("可补充关键词：" + "、".join(missing[:6]))
    if coverage_score < 32:
        suggestions.append("先给结论（30 秒内），再分层展开（原理 → 实现 → 权衡 → 案例）")

    total = round(sum(dimension.score for dimension in dimensions), 1)
    return AnswerEvaluation(
        score=total,
        dimensions=tuple(dimensions),
        missing_keywords=tuple(missing),
        suggestions=tuple(dict.fromkeys(suggestions)),
    )
