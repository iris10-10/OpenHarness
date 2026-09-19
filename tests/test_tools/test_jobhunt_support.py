"""Tests for the Phase 2 job-hunt support modules.

Covers storage (local JSON persistence), parsing (resume / JD / skills /
salary), scoring (ATS / matching / skill gap / interview answers), the
interview question bank and the algorithm / code-review helpers. Every
assertion is deterministic and offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openharness.jobhunt import algorithm
from openharness.jobhunt.code_review import detect_language, review_code
from openharness.jobhunt.parsing import (
    degree_level,
    experience_bounds,
    extract_skills,
    normalize_skill,
    parse_jd_text,
    parse_resume_text,
    parse_salary,
)
from openharness.jobhunt.questions import build_questions
from openharness.jobhunt.scoring import (
    CandidateProfile,
    analyze_skill_gap,
    classify_recommendation,
    evaluate_interview_answer,
    score_match,
    score_resume_ats,
)
from openharness.jobhunt.storage import (
    JOBHUNT_DIR_ENV,
    MAX_STORED_SESSIONS,
    JobHuntStore,
    deep_merge,
    new_record_id,
    resolve_jobhunt_dir,
    today_iso,
    utc_now_iso,
)

GIL_QUESTION = "什么是 GIL？它如何影响 Python 的多线程性能？"


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


def test_resolve_jobhunt_dir_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "override"
    env_dir = tmp_path / "env"
    configured = tmp_path / "configured"

    monkeypatch.setenv(JOBHUNT_DIR_ENV, str(env_dir))
    assert resolve_jobhunt_dir(override=override, configured=str(configured)) == override
    assert override.is_dir()

    assert resolve_jobhunt_dir(configured=str(configured)) == env_dir
    assert env_dir.is_dir()

    monkeypatch.delenv(JOBHUNT_DIR_ENV)
    assert resolve_jobhunt_dir(configured=str(configured)) == configured
    assert configured.is_dir()


def test_deep_merge_replaces_lists_without_mutating_inputs() -> None:
    base = {"basic": {"city": "杭州", "targets": ["杭州"]}, "flag": False}
    updates = {"basic": {"city": "上海", "targets": ["上海", "北京"]}, "extra": 1}

    merged = deep_merge(base, updates)

    assert merged == {
        "basic": {"city": "上海", "targets": ["上海", "北京"]},
        "flag": False,
        "extra": 1,
    }
    assert base == {"basic": {"city": "杭州", "targets": ["杭州"]}, "flag": False}


def test_new_record_id_shape_and_uniqueness() -> None:
    first = new_record_id("app")
    second = new_record_id("app")

    assert first.startswith("app-")
    assert len(first) == len("app-") + 8
    assert first != second


def test_time_helper_formats() -> None:
    assert utc_now_iso().endswith("Z")
    assert len(today_iso()) == 10


def test_store_profile_roundtrip_and_deep_merge(tmp_path: Path) -> None:
    store = JobHuntStore(tmp_path / "jobhunt")
    assert store.load_profile() == {}

    store.save_profile({"basic": {"current_city": "杭州"}})
    merged = store.merge_profile(
        {
            "basic": {"current_title": "后端工程师"},
            "skills": {"technical": [{"name": "Python"}]},
        }
    )

    assert merged["basic"] == {"current_city": "杭州", "current_title": "后端工程师"}
    assert store.load_profile() == merged


def test_store_applications_and_corrupt_file_fallback(tmp_path: Path) -> None:
    store = JobHuntStore(tmp_path / "jobhunt")
    assert store.load_applications() == []

    store.save_applications([{"id": "app-1", "status": "已投递"}])
    assert store.load_applications() == [{"id": "app-1", "status": "已投递"}]

    store.applications_path.write_text("{oops", encoding="utf-8")
    assert store.load_applications() == []


def test_store_sessions_capped_at_max(tmp_path: Path) -> None:
    store = JobHuntStore(tmp_path / "jobhunt")
    sessions = [{"id": f"session-{index}"} for index in range(MAX_STORED_SESSIONS + 5)]

    store.save_sessions(sessions)
    loaded = store.load_sessions()

    assert len(loaded) == MAX_STORED_SESSIONS
    assert loaded[0]["id"] == "session-0"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_parse_resume_text_structured_fields(resume_text: str) -> None:
    resume = parse_resume_text(resume_text)

    assert resume.personal_info["name"] == "张伟"
    assert resume.personal_info["phone"] == "13800138000"
    assert resume.personal_info["email"] == "zhangwei@example.com"
    assert resume.technical_skills == ["Python", "FastAPI", "MySQL", "Redis", "Docker", "Git"]
    assert resume.years_of_experience == 4.0
    assert resume.experience[0]["company"] == "某某科技有限公司"
    assert resume.experience[0]["period"] == "2020.03-2024.03"
    assert resume.education[0]["degree"] == "本科"


def test_parse_jd_text_structured_fields(jd_text: str) -> None:
    job = parse_jd_text(jd_text)

    assert job.title == "Python 后端开发工程师"
    assert job.company == "杭州星辰科技有限公司"
    assert job.city == "杭州"
    assert job.salary is not None
    assert (job.salary.min_monthly, job.salary.max_monthly, job.salary.months_per_year) == (
        25000,
        40000,
        14,
    )
    assert job.experience == "3-5年"
    assert job.education == "本科及以上"
    assert job.required_skills == ["Python", "FastAPI", "MySQL", "Redis"]
    assert job.preferred_skills == ["Docker", "Kubernetes"]
    assert job.category == "后端"
    assert job.completeness == 1.0


def test_skill_extraction_aliases() -> None:
    assert extract_skills("熟悉 K8s、golang 与 Spring Boot，了解 k8s") == [
        "Kubernetes",
        "Go",
        "Spring",
    ]
    assert normalize_skill("k8s") == "Kubernetes"
    assert normalize_skill("golang") == "Go"


def test_degree_levels() -> None:
    assert degree_level("高中") == 0
    assert degree_level("大专") == 1
    assert degree_level("本科") == 2
    assert degree_level("博士") == 4


def test_parse_salary_variants() -> None:
    k_range = parse_salary("25-40K·14薪")
    assert k_range is not None
    assert (k_range.min_monthly, k_range.max_monthly, k_range.months_per_year) == (
        25000,
        40000,
        14,
    )

    annual = parse_salary("25-40万/年")
    assert annual is not None
    assert (annual.min_monthly, annual.max_monthly) == (20833, 33333)

    assert parse_salary("面议") is None


def test_experience_bounds_normalized_form() -> None:
    assert experience_bounds("3-5年") == (3.0, 5.0)


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------


def test_ats_rubric_breakdown(resume_text: str) -> None:
    ats = score_resume_ats(parse_resume_text(resume_text), resume_text)
    components = {component.name: component.score for component in ats.components}

    assert ats.total == 65.0
    assert components == {
        "联系方式": 15.0,
        "技能清单": 12.0,
        "工作经历": 11.0,
        "教育背景": 10.0,
        "结构完整": 12.0,
        "内容质量": 5.0,
    }


def test_match_engine_with_merged_profile(
    jd_text: str, resume_text: str, profile_dict: dict[str, Any]
) -> None:
    candidate = CandidateProfile.from_resume(parse_resume_text(resume_text))
    candidate = candidate.merged_with_profile(profile_dict)

    match = score_match(candidate, parse_jd_text(jd_text))

    assert match.total == 92.7
    assert match.recommendation == "保底"
    assert match.skill_coverage == 1.0
    assert match.covered_skills == ("Python", "FastAPI", "MySQL", "Redis")
    assert match.missing_skills == ()
    assert match.freshness_applied is False


def test_match_engine_resume_only(jd_text: str, resume_text: str) -> None:
    match = score_match(
        CandidateProfile.from_resume(parse_resume_text(resume_text)),
        parse_jd_text(jd_text),
    )

    assert match.total == 87.8
    assert match.recommendation == "保底"


def test_classify_recommendation_tiers() -> None:
    common = {"match_threshold": 65.0, "safety_threshold": 80.0}

    safety = classify_recommendation(
        85.0, coverage=0.95, experience_ok=True, education_ok=True, **common
    )
    matching = classify_recommendation(
        85.0, coverage=0.7, experience_ok=True, education_ok=True, **common
    )
    stretch = classify_recommendation(
        50.0, coverage=0.7, experience_ok=True, education_ok=True, **common
    )

    assert (safety, matching, stretch) == ("保底", "匹配", "冲刺")


def test_skill_gap_full_coverage(
    jd_text: str, resume_text: str, profile_dict: dict[str, Any]
) -> None:
    candidate = CandidateProfile.from_resume(parse_resume_text(resume_text))
    candidate = candidate.merged_with_profile(profile_dict)

    report = analyze_skill_gap(candidate.skills, parse_jd_text(jd_text))

    assert report.coverage == 1.0
    assert report.covered == ("Python", "FastAPI", "MySQL", "Redis")
    assert report.missing == ()
    assert report.missing_preferred == ()
    assert report.priority_actions == ()


def test_evaluate_interview_answer_weak_vs_strong() -> None:
    weak = evaluate_interview_answer(GIL_QUESTION, "不知道")

    assert weak.score == 2.0
    assert "Python" in weak.missing_keywords

    strong_answer = (
        "首先，GIL 是 CPython 的全局解释器锁：同一时刻只有一个线程能执行 Python 字节码，"
        "所以多线程无法利用多核做并行计算，CPU 密集型任务的性能提升很有限。"
        "然后，我的做法是把 CPU 密集模块改用多进程，接口耗时从 2s 降低到 200ms，吞吐提升大约 3 倍。"
        "最后做个总结：IO 密集型保留多线程仍然有效，因为等待 IO 时 GIL 会释放；"
        "如果必须多核并行，就换成多进程或 C 扩展。"
    )
    strong = evaluate_interview_answer(GIL_QUESTION, strong_answer)

    assert strong.score == 94.0
    assert strong.score > weak.score


# ---------------------------------------------------------------------------
# 面试题库
# ---------------------------------------------------------------------------


def test_build_questions_technical_round() -> None:
    questions = build_questions(["Python", "FastAPI"], round_kind="技术", limit=8)

    assert len(questions) == 8
    assert {question.category for question in questions} == {"八股文", "算法", "系统设计", "项目"}
    assert any("GIL" in question.question for question in questions)


def test_build_questions_hr_round() -> None:
    questions = build_questions(["Python", "FastAPI"], round_kind="HR", limit=8)

    assert len(questions) == 8
    assert {question.category for question in questions} == {"HR", "行为"}


def test_build_questions_hard_excludes_basics() -> None:
    questions = build_questions(["Python"], round_kind="技术", difficulty="困难", limit=8)

    assert questions
    assert all(question.difficulty != "基础" for question in questions)


# ---------------------------------------------------------------------------
# 算法题库与代码审查
# ---------------------------------------------------------------------------


def test_detect_topics_ranks_by_keyword_hits() -> None:
    topics = algorithm.detect_topics("给定一个数组，求和最大的连续子数组，考虑动态规划")

    assert topics == ["array", "sliding_window", "dp"]


def test_analyze_problem_payload_uses_topic_playbook() -> None:
    analysis = algorithm.analyze_problem("给定一个包含重复项的数组，判断是否存在两个数的和为目标值")
    payload = analysis.to_dict()

    assert payload["primary_topic"] == "array"
    assert payload["primary_label"] == "数组与哈希"
    related = [item["problem"] for item in payload["related_problems"]]
    assert "两数之和" in related


def test_select_problems_topic_first_then_varied_filler() -> None:
    picked = algorithm.select_problems(topics=["dp"], limit=6)

    assert [problem.problem for problem in picked] == [
        "爬楼梯",
        "打家劫舍",
        "零钱兑换",
        "最长递增子序列",
        "最长回文子串",
        "单词拆分",
    ]
    assert all(problem.topic == "dp" for problem in picked)

    easy = algorithm.select_problems(topics=["dp"], difficulty="简单", limit=4)

    assert [problem.problem for problem in easy] == [
        "爬楼梯",
        "两数之和",
        "合并两个有序数组",
        "反转链表",
    ]
    assert all(problem.difficulty == "简单" for problem in easy)


def test_review_code_flags_nested_loops() -> None:
    code = "def f(arr):\n    for i in arr:\n        for j in arr:\n            print(i, j)\n"

    findings, summary = review_code(code)

    assert summary["language"] == "python"
    assert summary["max_loop_depth"] == 2
    assert "O(n²)" in summary["complexity_estimate"]
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.severity, finding.category, finding.line) == ("警告", "复杂度", 3)


def test_review_code_flags_mutable_default_argument() -> None:
    findings, _ = review_code("def f(x=[]):\n    return x\n")

    assert len(findings) == 1
    assert (findings[0].severity, findings[0].category) == ("隐患", "正确性")


def test_detect_language_identifies_python_and_javascript() -> None:
    assert detect_language("def f():\n    return 1\n") == "python"
    assert detect_language("function f() { return 1; }") == "javascript"
