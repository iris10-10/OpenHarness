"""Tests for the salary research tools (salary_query / compare / negotiate)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from openharness.tools.base import ToolResult
from openharness.tools.salary_tool import (
    SalaryCompareTool,
    SalaryCompareToolInput,
    SalaryNegotiateTool,
    SalaryNegotiateToolInput,
    SalaryQueryTool,
    SalaryQueryToolInput,
)

JOB_SENIOR = {
    "salary_min": 25000,
    "salary_max": 40000,
    "months_per_year": 14,
    "city": "杭州",
    "company": "杭州星辰科技有限公司",
    "title": "Python 后端开发工程师",
    "experience": "3-5年",
}
JOB_JUNIOR = {
    "salary_min": 20000,
    "salary_max": 30000,
    "months_per_year": 12,
    "city": "杭州",
    "company": "杭州初创科技有限公司",
    "title": "Python 后端开发工程师",
    "experience": "1-3年",
}
JOB_SHANGHAI = {
    "salary_min": 30000,
    "salary_max": 50000,
    "months_per_year": 12,
    "city": "上海",
    "company": "上海云帆科技有限公司",
    "title": "Python 后端开发工程师",
    "experience": "5-10年",
}


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


def _jobs_retriever(make_retriever, make_hit):
    return make_retriever(
        hits={
            "jobs": [
                make_hit("", doc_id="job-1", collection="jobs", **JOB_SENIOR),
                make_hit("", doc_id="job-2", collection="jobs", **JOB_JUNIOR),
                make_hit("", doc_id="job-3", collection="jobs", **JOB_SHANGHAI),
            ]
        }
    )


# ---------------------------------------------------------------------------
# salary_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_salary_query_aggregates_and_lists_samples(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = _jobs_retriever(make_retriever, make_hit)

    result = await SalaryQueryTool(retriever=retriever, settings=settings).execute(
        SalaryQueryToolInput(position="Python 后端开发工程师"), ctx
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["sample_count"] == 3
    assert payload["monthly"] == {
        "count": 3,
        "min": 25000,
        "p25": 28750,
        "median": 32500,
        "p75": 36250,
        "max": 40000,
        "mean": 32500,
    }
    assert payload["annual"] == {
        "count": 3,
        "min": 300000,
        "p25": 377500,
        "median": 455000,
        "p75": 467500,
        "max": 480000,
        "mean": 411667,
    }
    assert [
        (sample["doc_id"], sample["salary"], sample["monthly_mid"])
        for sample in payload["samples"]
    ] == [
        ("job-3", "30-50K", 40000),
        ("job-1", "25-40K·14薪", 32500),
        ("job-2", "20-30K", 25000),
    ]
    assert len(payload["hints"]) == 1
    assert result.metadata == {"sample_count": 3, "median_monthly": 32500}


@pytest.mark.asyncio
async def test_salary_query_city_and_years_filters(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = _jobs_retriever(make_retriever, make_hit)

    by_city = await SalaryQueryTool(retriever=retriever, settings=settings).execute(
        SalaryQueryToolInput(position="Python 后端开发工程师", city="杭州"), ctx
    )
    payload = _payload(by_city)
    assert payload["sample_count"] == 2
    assert payload["monthly"]["median"] == 28750
    assert [sample["doc_id"] for sample in payload["samples"]] == ["job-1", "job-2"]

    # 8 年经验与 1-3 / 3-5 年的岗位差异过大，只保留 5-10 年
    by_years = await SalaryQueryTool(retriever=retriever, settings=settings).execute(
        SalaryQueryToolInput(position="Python 后端开发工程师", years=8), ctx
    )
    payload = _payload(by_years)
    assert payload["sample_count"] == 1
    assert payload["monthly"]["median"] == 40000
    assert "按 8.0 年经验过滤掉 2 条要求差异过大的岗位" in payload["notes"]

    # 全部被经验过滤：给出放宽条件提示而非报错
    all_dropped = await SalaryQueryTool(retriever=retriever, settings=settings).execute(
        SalaryQueryToolInput(position="Python 后端开发工程师", years=40), ctx
    )
    payload = _payload(all_dropped)
    assert payload["sample_count"] == 0
    assert "按 40.0 年经验过滤掉 3 条要求差异过大的岗位" in payload["notes"]
    assert "当前筛选条件下没有样本：可放宽 city / years 条件" in payload["notes"]


@pytest.mark.asyncio
async def test_salary_query_empty_and_offline(settings, ctx, make_retriever, offline_retriever) -> None:
    empty = await SalaryQueryTool(
        retriever=make_retriever(), settings=settings
    ).execute(SalaryQueryToolInput(position="Python 后端开发工程师"), ctx)

    payload = _payload(empty)
    assert payload["sample_count"] == 0
    assert payload["notes"][-1] == (
        "岗位库中暂无 'Python 后端开发工程师' 的薪资样本："
        "请先用 jd_parse（store_to_rag=true）将目标岗位 JD 入库，再重试"
    )
    assert empty.metadata == {}

    offline = await SalaryQueryTool(
        retriever=offline_retriever, settings=settings
    ).execute(SalaryQueryToolInput(position="Python 后端开发工程师"), ctx)
    assert offline.is_error is True
    assert "岗位库检索失败" in offline.output
    assert "jd_parse" in offline.output


@pytest.mark.asyncio
async def test_salary_query_text_fallback_and_skipped_note(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = make_retriever(
        hits={
            "jobs": [
                make_hit(
                    "岗位名称：Python 后端开发工程师\n薪资范围：25-40K·14薪",
                    doc_id="job-a",
                    collection="jobs",
                    city="杭州",
                    company="杭州星辰科技有限公司",
                ),
                make_hit(
                    "急招后端工程师，待遇面议",
                    doc_id="job-b",
                    collection="jobs",
                ),
            ]
        }
    )

    result = await SalaryQueryTool(retriever=retriever, settings=settings).execute(
        SalaryQueryToolInput(position="Python 后端开发工程师"), ctx
    )

    payload = _payload(result)
    assert payload["sample_count"] == 1
    sample = payload["samples"][0]
    assert sample["doc_id"] == "job-a"
    assert sample["salary"] == "25-40K·14薪"
    assert sample["monthly_mid"] == 32500
    assert "1 条岗位未识别出薪资，已跳过" in payload["notes"]


# ---------------------------------------------------------------------------
# salary_compare
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_salary_compare_groups_and_gaps(settings, ctx, make_hit, make_retriever) -> None:
    retriever = _jobs_retriever(make_retriever, make_hit)

    result = await SalaryCompareTool(retriever=retriever, settings=settings).execute(
        SalaryCompareToolInput(
            position="Python 后端开发工程师", dimension="city", values=["杭州", "上海"]
        ),
        ctx,
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["overall"]["median"] == 32500
    groups = {group["value"]: group for group in payload["groups"]}
    assert groups["杭州"]["sample_count"] == 2
    assert groups["杭州"]["monthly"]["median"] == 28750
    assert groups["杭州"]["vs_overall_pct"] == -11.5
    assert groups["上海"]["sample_count"] == 1
    assert groups["上海"]["monthly"]["median"] == 40000
    assert groups["上海"]["vs_overall_pct"] == 23.1
    # 中位数高的分组排在前面
    assert [group["value"] for group in payload["groups"]] == ["上海", "杭州"]
    assert payload["notes"][0].startswith("样本来自本地岗位库（共 3 条）")
    assert result.metadata == {"group_count": 2, "sample_count": 3}


@pytest.mark.asyncio
async def test_salary_compare_validation_empty_and_offline(
    settings, ctx, make_retriever, offline_retriever
) -> None:
    tool = SalaryCompareTool(settings=settings)

    empty_position = await tool.execute(
        SalaryCompareToolInput(position="  ", values=["杭州", "上海"]), ctx
    )
    assert empty_position.is_error is True
    assert "'position' 为必填" in empty_position.output

    bad_dimension = await tool.execute(
        SalaryCompareToolInput(position="后端", dimension="行业", values=["杭州", "上海"]), ctx
    )
    assert bad_dimension.is_error is True
    assert "未知对比维度 '行业'" in bad_dimension.output

    single = await tool.execute(
        SalaryCompareToolInput(position="后端", values=["杭州"]), ctx
    )
    assert single.is_error is True
    assert "至少需要 2 个对比对象" in single.output

    too_many = await tool.execute(
        SalaryCompareToolInput(position="后端", values=[f"城市{i}" for i in range(9)]), ctx
    )
    assert too_many.is_error is True
    assert "最多 8 个对比对象" in too_many.output

    no_samples = await SalaryCompareTool(retriever=make_retriever(), settings=settings).execute(
        SalaryCompareToolInput(position="Python 后端开发工程师", values=["杭州", "上海"]), ctx
    )
    payload = _payload(no_samples)
    assert [group["sample_count"] for group in payload["groups"]] == [0, 0]
    assert "没有可用薪资样本" in payload["notes"][0]

    offline = await SalaryCompareTool(
        retriever=offline_retriever, settings=settings
    ).execute(
        SalaryCompareToolInput(position="Python 后端开发工程师", values=["杭州", "上海"]), ctx
    )
    assert offline.is_error is True
    assert "岗位库检索失败" in offline.output


# ---------------------------------------------------------------------------
# salary_negotiate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_salary_negotiate_with_market(settings, ctx, make_hit, make_retriever) -> None:
    retriever = _jobs_retriever(make_retriever, make_hit)

    result = await SalaryNegotiateTool(retriever=retriever, settings=settings).execute(
        SalaryNegotiateToolInput(
            position="Python 后端开发工程师",
            target_monthly=30000,
            city="杭州",
            current_offer_monthly=26000,
            strengths=["高并发订单系统经验"],
        ),
        ctx,
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["market"]["sample_count"] == 2
    assert payload["market"]["monthly"]["median"] == 28750
    assert payload["analysis"]["target_vs_market_median_pct"] == 4.3
    assert payload["analysis"]["market_position"] == "处于市场 P25-P75 区间：按锚点策略正常推进"
    assert payload["analysis"]["anchor_monthly"] == 30625

    scripts = payload["scripts"]
    assert len(scripts) == 4
    assert "锚定开场" in scripts[0]
    assert "28750" in scripts[0]
    assert "30625" in scripts[0]
    assert "价值论证" in scripts[1]
    assert "高并发订单系统经验" in scripts[1]
    assert "低球应对" in scripts[2]
    assert "相差 4000 元/月" in scripts[2]
    assert "收尾确认" in scripts[3]
    assert len(payload["questions_to_ask"]) == 4
    assert len(payload["cautions"]) == 3
    assert "市场样本仅 2 条，分位数据仅供参考" in payload["notes"]
    assert result.metadata == {"has_market": True, "anchor_monthly": 30625}


@pytest.mark.asyncio
async def test_salary_negotiate_market_position_labels(
    settings, ctx, make_hit, make_retriever
) -> None:
    retriever = _jobs_retriever(make_retriever, make_hit)
    tool = SalaryNegotiateTool(retriever=retriever, settings=settings)

    low = await tool.execute(
        SalaryNegotiateToolInput(
            position="Python 后端开发工程师", target_monthly=25000, city="杭州"
        ),
        ctx,
    )
    payload = _payload(low)
    assert payload["analysis"]["market_position"] == "低于市场 P25：目标偏保守，可适当上调锚点"
    assert payload["analysis"]["anchor_monthly"] == 30625

    high = await tool.execute(
        SalaryNegotiateToolInput(
            position="Python 后端开发工程师", target_monthly=32000, city="杭州"
        ),
        ctx,
    )
    payload = _payload(high)
    assert payload["analysis"]["market_position"] == "高于市场 P75：重点论证差异化价值与总包结构"
    assert payload["analysis"]["anchor_monthly"] == 32000


@pytest.mark.asyncio
async def test_salary_negotiate_without_market_offline_and_validation(
    settings, ctx, make_retriever, offline_retriever
) -> None:
    no_samples = await SalaryNegotiateTool(
        retriever=make_retriever(), settings=settings
    ).execute(
        SalaryNegotiateToolInput(position="Python 后端开发工程师", target_monthly=30000), ctx
    )
    payload = _payload(no_samples)
    assert payload["market"] is None
    assert payload["analysis"] == {"anchor_monthly": 30000}
    assert "岗位库中暂无 'Python 后端开发工程师' 的可用样本，市场分析省略" in payload["notes"]
    assert "先给出期望 30000 元/月" in payload["scripts"][0]
    assert len(payload["scripts"]) == 3
    assert no_samples.metadata == {"has_market": False, "anchor_monthly": 30000}

    offline = await SalaryNegotiateTool(
        retriever=offline_retriever, settings=settings
    ).execute(
        SalaryNegotiateToolInput(position="Python 后端开发工程师", target_monthly=30000), ctx
    )
    payload = _payload(offline)
    assert payload["market"] is None
    assert "岗位库检索失败" in payload["notes"][0]

    empty_position = await SalaryNegotiateTool(settings=settings).execute(
        SalaryNegotiateToolInput(position="  ", target_monthly=30000), ctx
    )
    assert empty_position.is_error is True
    assert "'position' 为必填" in empty_position.output

    with pytest.raises(ValidationError):
        SalaryNegotiateToolInput(position="后端", target_monthly=0)


def test_salary_tools_read_only_flags() -> None:
    assert (
        SalaryQueryTool().is_read_only(SalaryQueryToolInput(position="后端")) is True
    )
    assert (
        SalaryCompareTool().is_read_only(
            SalaryCompareToolInput(position="后端", values=["杭州", "上海"])
        )
        is True
    )
    assert (
        SalaryNegotiateTool().is_read_only(
            SalaryNegotiateToolInput(position="后端", target_monthly=20000)
        )
        is True
    )
