"""Algorithm-practice tools: algorithm_recommend / algorithm_analyze / algorithm_review.

- ``algorithm_recommend`` recommends practice problems for a target company:
  real problems from the RAG ``interview`` collection first; the classic
  problem bank in :mod:`openharness.jobhunt.algorithm` fills the rest,
  filtered by topic and difficulty.
- ``algorithm_analyze`` classifies a problem statement and returns the
  topic's framework (解题步骤 / 常见陷阱 / 复杂度目标 / 面试追问) plus related
  classic problems.
- ``algorithm_review`` runs static heuristics over a submitted solution
  (:mod:`openharness.jobhunt.code_review`); the code is never executed and
  findings are explicitly labelled as heuristics.

The bank and the heuristics live in ``openharness.jobhunt``; this module is
the thin tool layer.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.algorithm import (
    ALGORITHM_DIFFICULTIES,
    TOPIC_LABELS,
    analyze_problem,
    detect_topics,
    resolve_topic,
    select_problems,
)
from openharness.jobhunt.code_review import EDGE_CASE_CHECKLIST, review_code
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_DIFFICULTY_OPTIONS: tuple[str, ...] = (*ALGORITHM_DIFFICULTIES, "混合")

_EXCERPT_LIMIT = 320
_MAX_CODE_CHARS = 20_000

_LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "c++": "cpp",
    "cplusplus": "cpp",
    "golang": "go",
}
_SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "python",
    "java",
    "javascript",
    "cpp",
    "go",
)

_TOPIC_OPTIONS_TEXT = " / ".join(
    f"{label}（{key}）" for key, label in TOPIC_LABELS.items()
)


def _excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _difficulty_error(difficulty: str) -> str | None:
    if difficulty not in _DIFFICULTY_OPTIONS:
        return f"未知难度 '{difficulty}'：可选 {' / '.join(_DIFFICULTY_OPTIONS)}"
    return None


def _topic_error(topic: str) -> str | None:
    if topic.strip() and not resolve_topic(topic):
        return f"未知刷题主题 '{topic}'：可选 {_TOPIC_OPTIONS_TEXT}"
    return None


# ---------------------------------------------------------------------------
# algorithm_recommend
# ---------------------------------------------------------------------------


class AlgorithmRecommendToolInput(BaseModel):
    """Arguments for the algorithm_recommend tool."""

    company: str = Field(
        default="",
        description="Target company; its RAG interview experiences are prioritised",
    )
    jd_text: str = Field(
        default="", description="Optional JD text: used to infer practice topics"
    )
    topic: str = Field(
        default="",
        description="Topic filter, e.g. 动态规划 / 链表（也接受 dp / linked_list）",
    )
    difficulty: str = Field(
        default="混合", description="Difficulty: 简单 / 中等 / 困难 / 混合"
    )
    count: int = Field(default=8, ge=1, le=20, description="Maximum problems to recommend")


class AlgorithmRecommendTool(JobHuntToolBase):
    """Recommend practice problems for a company / topic / difficulty."""

    name = "algorithm_recommend"
    description = (
        "Recommend algorithm practice problems: real problems from the RAG "
        "'interview' collection first (target company prioritised), then a "
        "curated classic problem bank filtered by topic (动态规划/链表/…, key or "
        "Chinese label) and difficulty. Chinese labels for topic output. "
        "Read-only."
    )
    input_model = AlgorithmRecommendToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: AlgorithmRecommendToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        difficulty_error = _difficulty_error(arguments.difficulty)
        if difficulty_error:
            return error_result(difficulty_error)
        topic_error = _topic_error(arguments.topic)
        if topic_error:
            return error_result(topic_error)

        company = arguments.company.strip()
        explicit = resolve_topic(arguments.topic)
        topics: list[str] = []
        if explicit:
            topics = [explicit]
        elif arguments.jd_text.strip():
            topics = detect_topics(arguments.jd_text)

        recommended = select_problems(
            topics=topics, difficulty=arguments.difficulty, limit=arguments.count
        )

        real: list[dict[str, Any]] = []
        notes: list[str] = []
        query = " ".join(
            part for part in (company, "算法 手撕 真题 高频题") if part
        ).strip() or "算法 手撕 真题"
        where = {"company": company} if company else None
        try:
            retriever = self.resolve_retriever()
            outcome = await retriever.retrieve(
                query, collection="interview", where=where, top_n=5
            )
        #面经库不可用时仍返回内置题单，不中断
        except Exception as exc:  # noqa: BLE001
            notes.append(f"面经库检索不可用：{exc}（可先导入面经文档后重试）")
        else:
            for hit in outcome.hits:
                real.append(
                    {
                        "doc_id": hit.id,
                        "company": str(hit.metadata.get("company", "")),
                        "excerpt": _excerpt(hit.text),
                        "score": round(float(hit.score), 4),
                    }
                )
            if not outcome.hits:
                if outcome.notes:
                    notes.extend(str(note) for note in outcome.notes)
                scope = f"'{company}' 的" if company else ""
                notes.append(
                    f"面经库暂无{scope}算法真题：以下推荐为内置经典题单"
                    "（按主题/难度筛选）；导入面经后可得公司专属高频题"
                )

        payload: dict[str, Any] = {
            "company": company,
            "filters": {
                "topics": [TOPIC_LABELS[key] for key in topics],
                "difficulty": arguments.difficulty,
            },
            "real_problems": real,
            "recommended_problems": [problem.to_dict() for problem in recommended],
            "total": len(real) + len(recommended),
        }
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"real_count": len(real), "recommended_count": len(recommended)},
        )


# ---------------------------------------------------------------------------
# algorithm_analyze
# ---------------------------------------------------------------------------


class AlgorithmAnalyzeToolInput(BaseModel):
    """Arguments for the algorithm_analyze tool."""

    problem: str = Field(description="Problem statement or title to analyze")
    topic: str = Field(
        default="",
        description="Force a topic (key or Chinese label); empty = keyword auto-detect",
    )
    related_limit: int = Field(
        default=4, ge=0, le=10, description="Maximum related classic problems"
    )


class AlgorithmAnalyzeTool(JobHuntToolBase):
    """Explain a problem: topic, framework, pitfalls, related classics."""

    name = "algorithm_analyze"
    description = (
        "Analyze an algorithm problem statement: detect its topic and return "
        "the solving framework (解题步骤 / 常见陷阱 / 复杂度目标 / 面试追问) plus "
        "related classic problems with approach hints. Topic can be forced via "
        "the topic parameter. Read-only."
    )
    input_model = AlgorithmAnalyzeToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: AlgorithmAnalyzeToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        if not arguments.problem.strip():
            return error_result("'problem' 为必填")
        topic_error = _topic_error(arguments.topic)
        if topic_error:
            return error_result(topic_error)

        analysis = analyze_problem(
            arguments.problem,
            topic=arguments.topic,
            related_limit=arguments.related_limit,
        )
        payload = analysis.to_dict()
        notes: list[str] = []
        if not analysis.matched_topics:
            notes.append(
                "未从题干识别出主题：已返回通用解题框架；"
                f"可用 topic 参数显式指定（{_TOPIC_OPTIONS_TEXT}）"
            )
        elif not arguments.topic.strip():
            notes.append("主题识别基于关键词命中；如与题目不符，可用 topic 参数纠正")
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={
                "primary_topic": analysis.primary_topic or "generic",
                "related_count": len(analysis.related),
            },
        )


# ---------------------------------------------------------------------------
# algorithm_review
# ---------------------------------------------------------------------------


class AlgorithmReviewToolInput(BaseModel):
    """Arguments for the algorithm_review tool."""

    code: str = Field(description="Solution code to review (heuristics; never executed)")
    language: str = Field(
        default="",
        description="python / java / javascript / cpp / go; empty = auto-detect",
    )
    problem: str = Field(
        default="", description="Optional problem statement for topic-specific pitfalls"
    )


class AlgorithmReviewTool(JobHuntToolBase):
    """Static heuristic review of a submitted solution."""

    name = "algorithm_review"
    description = (
        "Review a solution with deterministic static heuristics (never "
        "executes the code): loop-nesting complexity risks, sorting inside "
        "loops, list membership in loops, string += in loops, recursion "
        "without memoization, mutable default arguments, Java string == / JS "
        "loose equality, C++ std::endl. Returns findings with fixes plus an "
        "edge-case checklist. Read-only."
    )
    input_model = AlgorithmReviewToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: AlgorithmReviewToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        code = arguments.code
        if not code.strip():
            return error_result("'code' 为必填")
        if len(code) > _MAX_CODE_CHARS:
            return error_result(
                f"代码过长（{len(code)} 字符 > {_MAX_CODE_CHARS}）：请提交单个题解片段"
            )
        language = arguments.language.strip().lower()
        language = _LANGUAGE_ALIASES.get(language, language)
        if language and language not in _SUPPORTED_LANGUAGES:
            return error_result(
                f"不支持的语言 '{arguments.language}'：可选 "
                f"{' / '.join((*_SUPPORTED_LANGUAGES, '（留空自动检测）'))}"
            )

        findings, summary = review_code(code, language=language)
        notes = [
            (
                "以下为静态启发式检测结果（未执行代码）：仅提示常见的复杂度与实现风险，"
                "请结合具体语境判断"
            )
        ]
        if not findings:
            notes.append("启发式检查未发现常见问题；仍建议按边界用例清单逐项自测")

        payload: dict[str, Any] = {
            "language": summary["language"],
            "line_count": summary["line_count"],
            "max_loop_depth": summary["max_loop_depth"],
            "complexity_estimate": summary["complexity_estimate"],
            "findings": [finding.to_dict() for finding in findings],
            "edge_case_checklist": list(EDGE_CASE_CHECKLIST),
        }
        if arguments.problem.strip():
            analysis = analyze_problem(arguments.problem, related_limit=2)
            payload["problem_context"] = {
                "primary_label": analysis.primary_label,
                "pitfalls": list(analysis.pitfalls),
                "target_complexity": analysis.target_complexity,
            }
        payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={
                "language": summary["language"],
                "finding_count": len(findings),
            },
        )
