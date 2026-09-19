"""Tests for the algorithm-practice tools (recommend / analyze / review)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.tools.algorithm_practice_tool import (
    AlgorithmAnalyzeTool,
    AlgorithmAnalyzeToolInput,
    AlgorithmRecommendTool,
    AlgorithmRecommendToolInput,
    AlgorithmReviewTool,
    AlgorithmReviewToolInput,
)
from openharness.tools.base import ToolResult

DOUBLE_LOOP_CODE = "def f(arr):\n    for i in arr:\n        for j in arr:\n            print(i, j)\n"
CLEAN_CODE = "def add(a, b):\n    return a + b\n"
KNOWN_PROBLEM = "给定一个包含重复项的数组，判断是否存在两个数的和为目标值"


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# algorithm_recommend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_algorithm_recommend_topic_filter(settings, ctx, make_retriever) -> None:
    result = await AlgorithmRecommendTool(
        retriever=make_retriever(), settings=settings
    ).execute(AlgorithmRecommendToolInput(topic="动态规划", count=6), ctx)

    assert result.is_error is False
    payload = _payload(result)
    assert payload["filters"] == {"topics": ["动态规划"], "difficulty": "混合"}
    problems = payload["recommended_problems"]
    assert [problem["problem"] for problem in problems] == [
        "爬楼梯",
        "打家劫舍",
        "零钱兑换",
        "最长递增子序列",
        "最长回文子串",
        "单词拆分",
    ]
    assert all(problem["topic"] == "dp" for problem in problems)
    assert all(problem["topic_label"] == "动态规划" for problem in problems)
    assert problems[0]["hint"]
    assert payload["real_problems"] == []
    assert payload["total"] == 6
    assert payload["notes"][0].startswith("面经库暂无算法真题")
    assert result.metadata == {"real_count": 0, "recommended_count": 6}


@pytest.mark.asyncio
async def test_algorithm_recommend_company_hits_and_jd_fallback(
    jd_text, settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(
        hits={
            "interview": [
                make_hit(
                    "真题：手撕 LRU 缓存",
                    doc_id="alg-1",
                    collection="interview",
                    company="杭州星辰科技有限公司",
                    score=0.88,
                )
            ]
        }
    )
    result = await AlgorithmRecommendTool(retriever=retriever, settings=settings).execute(
        AlgorithmRecommendToolInput(company="杭州星辰科技有限公司"), ctx
    )

    payload = _payload(result)
    assert payload["real_problems"] == [
        {
            "doc_id": "alg-1",
            "company": "杭州星辰科技有限公司",
            "excerpt": "真题：手撕 LRU 缓存",
            "score": 0.88,
        }
    ]
    assert payload["total"] == 9
    assert "notes" not in payload
    assert retriever.calls[0]["collections"] == ["interview"]
    assert retriever.calls[0]["where"] == {"company": "杭州星辰科技有限公司"}
    assert result.metadata == {"real_count": 1, "recommended_count": 8}

    # 该 JD 不含算法关键词：主题为空，回退通用题单
    jd_result = await AlgorithmRecommendTool(
        retriever=make_retriever(), settings=settings
    ).execute(AlgorithmRecommendToolInput(jd_text=jd_text), ctx)
    payload = _payload(jd_result)
    assert payload["filters"]["topics"] == []
    assert len(payload["recommended_problems"]) == 8
    assert payload["recommended_problems"][0]["problem"] == "两数之和"
    assert payload["recommended_problems"][0]["leetcode"] == 1


@pytest.mark.asyncio
async def test_algorithm_recommend_offline_and_validation(
    settings, ctx, make_retriever, offline_retriever
) -> None:
    offline = await AlgorithmRecommendTool(
        retriever=offline_retriever, settings=settings
    ).execute(AlgorithmRecommendToolInput(), ctx)

    payload = _payload(offline)
    assert payload["notes"][0].startswith("面经库检索不可用：rag offline (test)")
    assert len(payload["recommended_problems"]) == 8
    assert offline.metadata == {"real_count": 0, "recommended_count": 8}

    tool = AlgorithmRecommendTool(retriever=make_retriever(), settings=settings)
    bad_difficulty = await tool.execute(
        AlgorithmRecommendToolInput(difficulty="地狱"), ctx
    )
    assert bad_difficulty.is_error is True
    assert "未知难度 '地狱'" in bad_difficulty.output

    bad_topic = await tool.execute(
        AlgorithmRecommendToolInput(topic="量子计算"), ctx
    )
    assert bad_topic.is_error is True
    assert "未知刷题主题 '量子计算'" in bad_topic.output


# ---------------------------------------------------------------------------
# algorithm_analyze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_algorithm_analyze_known_forced_and_generic(settings, ctx) -> None:
    tool = AlgorithmAnalyzeTool(settings=settings)

    known = await tool.execute(AlgorithmAnalyzeToolInput(problem=KNOWN_PROBLEM), ctx)
    payload = _payload(known)
    assert payload["primary_topic"] == "array"
    assert payload["primary_label"] == "数组与哈希"
    assert [item["problem"] for item in payload["related_problems"]] == [
        "两数之和",
        "移动零",
        "三数之和",
        "合并区间",
    ]
    assert len(payload["pitfalls"]) == 3
    assert payload["notes"] == ["主题识别基于关键词命中；如与题目不符，可用 topic 参数纠正"]
    assert known.metadata == {"primary_topic": "array", "related_count": 4}

    forced = await tool.execute(
        AlgorithmAnalyzeToolInput(problem="请实现一个功能", topic="链表"), ctx
    )
    payload = _payload(forced)
    assert payload["primary_topic"] == "linked_list"
    assert payload["primary_label"] == "链表"
    assert [item["problem"] for item in payload["related_problems"]] == [
        "反转链表",
        "环形链表",
        "合并两个有序链表",
        "相交链表",
    ]
    assert "notes" not in payload

    generic = await tool.execute(AlgorithmAnalyzeToolInput(problem="请帮我看看这道题"), ctx)
    payload = _payload(generic)
    # to_dict() 将空主题映射为 "generic"（dataclass 属性本身为空串）
    assert payload["primary_topic"] == "generic"
    assert payload["primary_label"] == "通用"
    assert payload["related_problems"] == []
    assert payload["notes"][0].startswith("未从题干识别出主题")
    assert generic.metadata == {"primary_topic": "generic", "related_count": 0}


@pytest.mark.asyncio
async def test_algorithm_analyze_validation(settings, ctx) -> None:
    tool = AlgorithmAnalyzeTool(settings=settings)

    empty = await tool.execute(AlgorithmAnalyzeToolInput(problem="  "), ctx)
    assert empty.is_error is True
    assert "'problem' 为必填" in empty.output

    bad_topic = await tool.execute(
        AlgorithmAnalyzeToolInput(problem="两数之和", topic="量子计算"), ctx
    )
    assert bad_topic.is_error is True
    assert "未知刷题主题 '量子计算'" in bad_topic.output


# ---------------------------------------------------------------------------
# algorithm_review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_algorithm_review_flags_double_loop(settings, ctx) -> None:
    result = await AlgorithmReviewTool(settings=settings).execute(
        AlgorithmReviewToolInput(code=DOUBLE_LOOP_CODE), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["language"] == "python"
    assert payload["line_count"] == 4
    assert payload["max_loop_depth"] == 2
    assert "O(n²)" in payload["complexity_estimate"]
    assert payload["findings"] == [
        {
            "severity": "警告",
            "category": "复杂度",
            "message": "检测到 2 层循环嵌套，最坏时间复杂度可能达到 O(n²) 量级（双层循环）",
            "suggestion": "尝试用哈希表/双指针/前缀和/单调栈去掉内层循环；数据有序时可考虑二分。",
            "line": 3,
        }
    ]
    assert len(payload["edge_case_checklist"]) == 6
    assert payload["edge_case_checklist"][0] == "空输入 / None：函数是否直接崩溃"
    assert payload["notes"] == [
        "以下为静态启发式检测结果（未执行代码）：仅提示常见的复杂度与实现风险，请结合具体语境判断"
    ]
    assert result.metadata == {"language": "python", "finding_count": 1}


@pytest.mark.asyncio
async def test_algorithm_review_clean_code_language_alias_and_problem_context(
    settings, ctx,
) -> None:
    tool = AlgorithmReviewTool(settings=settings)

    clean = await tool.execute(
        AlgorithmReviewToolInput(code=CLEAN_CODE, language="py"), ctx
    )
    payload = _payload(clean)
    assert payload["language"] == "python"
    assert payload["max_loop_depth"] == 0
    assert payload["findings"] == []
    assert len(payload["notes"]) == 2
    assert "启发式检查未发现常见问题" in payload["notes"][1]
    assert clean.metadata == {"language": "python", "finding_count": 0}

    with_problem = await tool.execute(
        AlgorithmReviewToolInput(
            code=DOUBLE_LOOP_CODE, problem="给定一个数组，求和最大的连续子数组，考虑动态规划"
        ),
        ctx,
    )
    payload = _payload(with_problem)
    assert payload["problem_context"]["primary_label"] == "数组与哈希"
    assert payload["problem_context"]["pitfalls"]
    assert payload["problem_context"]["target_complexity"]


@pytest.mark.asyncio
async def test_algorithm_review_validation(settings, ctx) -> None:
    tool = AlgorithmReviewTool(settings=settings)

    empty = await tool.execute(AlgorithmReviewToolInput(code="  "), ctx)
    assert empty.is_error is True
    assert "'code' 为必填" in empty.output

    bad_language = await tool.execute(
        AlgorithmReviewToolInput(code=CLEAN_CODE, language="rust"), ctx
    )
    assert bad_language.is_error is True
    assert "不支持的语言 'rust'" in bad_language.output

    oversized = await tool.execute(AlgorithmReviewToolInput(code="x" * 20_001), ctx)
    assert oversized.is_error is True
    assert "代码过长" in oversized.output


def test_algorithm_tools_read_only_flags() -> None:
    assert AlgorithmRecommendTool().is_read_only(AlgorithmRecommendToolInput()) is True
    assert (
        AlgorithmAnalyzeTool().is_read_only(AlgorithmAnalyzeToolInput(problem="两数之和"))
        is True
    )
    assert (
        AlgorithmReviewTool().is_read_only(AlgorithmReviewToolInput(code="pass")) is True
    )
