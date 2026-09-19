"""Salary research tools: salary_query / salary_compare / salary_negotiate (Phase 2).

All numbers are aggregated from job postings the user has ingested into the
RAG ``jobs`` collection (via ``jd_parse``): salary metadata is read from each
posting (or re-parsed from its text as a fallback), converted to monthly
CNY and summarised deterministically. No fabricated market data — when the
local store has no samples the tools say so and point at the ingestion path.

- ``salary_query``: range/percentiles for a position (+city/years) with samples
- ``salary_compare``: cross-city or cross-company comparison of one position
- ``salary_negotiate``: negotiation scripts grounded in the sampled market
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import experience_bounds, parse_salary
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_MAX_RETRIEVE_K = 50
_COMPARE_MAX_VALUES = 8
_DIMENSIONS = ("city", "company")

_INGEST_HINT = "请先用 jd_parse（store_to_rag=true）将目标岗位 JD 入库，再重试"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _salary_text(record: dict[str, Any]) -> str:
    """Render a sample's monthly range as ``25-40K·14薪``."""
    text = f"{record['salary_min'] / 1000:.0f}-{record['salary_max'] / 1000:.0f}K"
    months = record.get("months_per_year", 12)
    if months and months != 12:
        text += f"·{months}薪"
    return text


def _sample_from_hit(hit: Any) -> dict[str, Any] | None:
    """Extract a salary sample from a RAG job hit (metadata first, text fallback)."""
    metadata = hit.metadata
    salary_min = _as_int(metadata.get("salary_min"))
    salary_max = _as_int(metadata.get("salary_max"))
    months = _as_int(metadata.get("months_per_year")) or 12
    if salary_min is None or salary_max is None:
        parsed = parse_salary(hit.text)
        if parsed is None:
            return None
        salary_min, salary_max = parsed.min_monthly, parsed.max_monthly
        months = parsed.months_per_year
    if salary_max < salary_min:
        salary_min, salary_max = salary_max, salary_min
    return {
        "doc_id": hit.id,
        "company": str(metadata.get("company", "")),
        "title": str(metadata.get("title", "")),
        "city": str(metadata.get("city", "")),
        "experience": str(metadata.get("experience", "")),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "months_per_year": months,
    }


def _monthly(record: dict[str, Any]) -> float:
    return (record["salary_min"] + record["salary_max"]) / 2.0


def _quantile(ordered: list[float], frac: float) -> float:
    """Linear-interpolation quantile (numpy-style), deterministic."""
    if len(ordered) == 1:
        return float(ordered[0])
    position = frac * (len(ordered) - 1)
    lower = math.floor(position)
    upper = min(len(ordered) - 1, math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _stats(values: list[float]) -> dict[str, Any]:
    """Interpolated quantile summary (deterministic, numpy-style default)."""
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": round(ordered[0]),
        "p25": round(_quantile(ordered, 0.25)),
        "median": round(_quantile(ordered, 0.5)),
        "p75": round(_quantile(ordered, 0.75)),
        "max": round(ordered[-1]),
        "mean": round(sum(ordered) / len(ordered)),
    }


def _city_matches(sample_city: str, city: str) -> bool:
    if not city or not sample_city:
        return True
    return city in sample_city or sample_city in city


def _years_compatible(experience: str, years: float | None) -> bool:
    """Soft experience filter: drop postings whose requirement is clearly off."""
    if years is None:
        return True
    bounds = experience_bounds(experience)
    if bounds is None:
        return True
    low, high = bounds
    return years >= low - 2 and years <= high + 2


def _samples_payload(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(records, key=_monthly, reverse=True)[:limit]
    return [
        {
            "company": record["company"],
            "title": record["title"],
            "city": record["city"],
            "salary": _salary_text(record),
            "monthly_mid": round(_monthly(record)),
            "doc_id": record["doc_id"],
        }
        for record in ordered
    ]


class _SalaryToolBase(JobHuntToolBase):
    """Shared retrieval + sampling plumbing for the salary tools."""

    async def _collect_samples(
        self,
        *,
        query: str,
        where: dict[str, Any] | None,
        top_n: int = _MAX_RETRIEVE_K,
    ) -> tuple[list[dict[str, Any]], list[str], str]:
        """Retrieve jobs and extract salary samples; returns (samples, notes, error)."""
        try:
            retriever = self.resolve_retriever()
            outcome = await retriever.retrieve(
                query, collection="jobs", where=where, top_n=top_n
            )
        #岗位库不可用时给出可执行的替代路径
        except Exception as exc:  # noqa: BLE001
            return [], [], f"岗位库检索失败：{exc}。{_INGEST_HINT}"
        notes = [str(note) for note in outcome.notes]
        samples = [
            sample
            for hit in outcome.hits
            if (sample := _sample_from_hit(hit)) is not None
        ]
        skipped = len(outcome.hits) - len(samples)
        if skipped:
            notes.append(f"{skipped} 条岗位未识别出薪资，已跳过")
        return samples, notes, ""


# ---------------------------------------------------------------------------
# salary_query
# ---------------------------------------------------------------------------


class SalaryQueryToolInput(BaseModel):
    """Arguments for the salary_query tool."""

    position: str = Field(description="Position / role, e.g. Python 后端工程师")
    city: str = Field(default="", description="City filter, e.g. 北京 / 上海")
    years: float | None = Field(
        default=None, ge=0, le=40, description="Years of experience for a like-for-like comparison"
    )
    sample_limit: int = Field(
        default=10, ge=1, le=30, description="Maximum individual samples to list"
    )


class SalaryQueryTool(_SalaryToolBase):
    """Query salary ranges for a position from the local jobs store."""

    name = "salary_query"
    description = (
        "Aggregate salary ranges for a position (optionally filtered by city and "
        "years) from job postings in the local RAG 'jobs' collection: monthly "
        "min/p25/median/p75/max plus annualised estimates, with per-posting "
        "samples. Read-only; ingest postings via jd_parse first."
    )
    input_model = SalaryQueryToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: SalaryQueryToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        position = arguments.position.strip()
        if not position:
            return error_result("'position' 为必填")
        city = arguments.city.strip()
        query = " ".join(part for part in (position, city) if part)
        where = {"city": city} if city else None
        samples, notes, error = await self._collect_samples(query=query, where=where)
        if error:
            return error_result(error)

        city_filtered = [s for s in samples if _city_matches(s["city"], city)]
        kept = [s for s in city_filtered if _years_compatible(s["experience"], arguments.years)]
        dropped = len(city_filtered) - len(kept)
        if dropped:
            notes.append(f"按 {arguments.years} 年经验过滤掉 {dropped} 条要求差异过大的岗位")

        if not kept:
            hint = (
                f"岗位库中暂无 '{position}' 的薪资样本：{_INGEST_HINT}"
                if not samples
                else "当前筛选条件下没有样本：可放宽 city / years 条件"
            )
            return ToolResult(
                output=json_output(
                    {
                        "position": position,
                        "city": city,
                        "years": arguments.years,
                        "sample_count": 0,
                        "notes": [*notes, hint],
                    }
                )
            )

        monthly_values = [_monthly(record) for record in kept]
        annual_values = [value * record["months_per_year"] for value, record in zip(monthly_values, kept)]
        payload: dict[str, Any] = {
            "position": position,
            "city": city,
            "years": arguments.years,
            "sample_count": len(kept),
            "monthly": _stats(monthly_values),
            "annual": _stats(annual_values),
            "samples": _samples_payload(kept, arguments.sample_limit),
        }
        if notes:
            payload["notes"] = notes
        if len(kept) < 5:
            payload["hints"] = [
                (
                    f"当前样本仅 {len(kept)} 条，结论仅供参考；"
                    f"可批量入库目标城市/岗位的 JD 后重试（样本越多越可靠）"
                )
            ]
        return ToolResult(
            output=json_output(payload),
            metadata={"sample_count": len(kept), "median_monthly": _stats(monthly_values)["median"]},
        )


# ---------------------------------------------------------------------------
# salary_compare
# ---------------------------------------------------------------------------


class SalaryCompareToolInput(BaseModel):
    """Arguments for the salary_compare tool."""

    position: str = Field(description="Position / role to compare")
    dimension: str = Field(default="city", description="Compare by: city / company")
    values: list[str] = Field(description="Cities or companies to compare (2-8 entries)")
    years: float | None = Field(
        default=None, ge=0, le=40, description="Years of experience for a like-for-like comparison"
    )


class SalaryCompareTool(_SalaryToolBase):
    """Compare one position's salary across cities or companies."""

    name = "salary_compare"
    description = (
        "Compare one position's salary across multiple cities or companies using "
        "job postings in the local RAG 'jobs' collection: per-group sample counts, "
        "monthly percentiles and the gap versus the overall median. Read-only; "
        "needs postings ingested via jd_parse."
    )
    input_model = SalaryCompareToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: SalaryCompareToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        position = arguments.position.strip()
        if not position:
            return error_result("'position' 为必填")
        dimension = arguments.dimension.strip()
        if dimension not in _DIMENSIONS:
            return error_result(f"未知对比维度 '{dimension}'：可选 {' / '.join(_DIMENSIONS)}")
        values = [value.strip() for value in arguments.values if value and value.strip()]
        values = list(dict.fromkeys(values))
        if len(values) < 2:
            return error_result("'values' 至少需要 2 个对比对象（城市或公司）")
        if len(values) > _COMPARE_MAX_VALUES:
            return error_result(f"'values' 最多 {_COMPARE_MAX_VALUES} 个对比对象")

        groups: list[dict[str, Any]] = []
        errors: list[str] = []
        all_values: list[float] = []
        for value in values:
            where = {"city": value} if dimension == "city" else {"company": value}
            samples, notes, error = await self._collect_samples(
                query=f"{position} {value}", where=where
            )
            if error:
                errors.append(f"{value}：{error}")
                groups.append({"value": value, "sample_count": 0, "monthly": None})
                continue
            matched = [s for s in samples if _matches_dimension(s, dimension, value)]
            kept = [s for s in matched if _years_compatible(s["experience"], arguments.years)]
            if not kept:
                groups.append(
                    {
                        "value": value,
                        "sample_count": 0,
                        "monthly": None,
                        "notes": [*notes, f"没有 '{position}' 在 {value} 的可用样本"],
                    }
                )
                continue
            monthly_values = [_monthly(record) for record in kept]
            all_values.extend(monthly_values)
            group: dict[str, Any] = {
                "value": value,
                "sample_count": len(kept),
                "monthly": _stats(monthly_values),
            }
            if notes:
                group["notes"] = notes
            groups.append(group)

        if not all_values:
            if errors and len(errors) == len(values):
                return error_result(errors[0])
            return ToolResult(
                output=json_output(
                    {
                        "position": position,
                        "dimension": dimension,
                        "groups": groups,
                        "notes": [
                            f"没有可用薪资样本：{_INGEST_HINT}",
                            *(f"检索错误：{item}" for item in errors),
                        ],
                    }
                )
            )

        overall = _stats(all_values)
        for group in groups:
            monthly = group.get("monthly")
            if monthly:
                group["vs_overall_pct"] = round(
                    (monthly["median"] - overall["median"]) / overall["median"] * 100, 1
                )
        groups.sort(key=lambda group: (group.get("monthly") or {}).get("median", -1), reverse=True)

        payload: dict[str, Any] = {
            "position": position,
            "dimension": dimension,
            "years": arguments.years,
            "overall": overall,
            "groups": groups,
            "notes": [f"样本来自本地岗位库（共 {overall['count']} 条），{_INGEST_HINT}"],
        }
        if errors:
            payload["notes"].extend(f"部分检索错误：{item}" for item in errors)
        return ToolResult(
            output=json_output(payload),
            metadata={"group_count": len(groups), "sample_count": overall["count"]},
        )


def _matches_dimension(record: dict[str, Any], dimension: str, value: str) -> bool:
    """Local confirmation of the RAG metadata filter (missing data never drops)."""
    if dimension == "city":
        return _city_matches(record["city"], value)
    company = record["company"]
    return not company or value in company or company in value


# ---------------------------------------------------------------------------
# salary_negotiate
# ---------------------------------------------------------------------------


class SalaryNegotiateToolInput(BaseModel):
    """Arguments for the salary_negotiate tool."""

    position: str = Field(description="Position / role being negotiated")
    target_monthly: int = Field(
        ge=1, le=1_000_000, description="Target monthly salary in CNY (before annualised extras)"
    )
    city: str = Field(default="", description="City of the offer (optional)")
    years: float | None = Field(default=None, ge=0, le=40, description="Your years of experience")
    current_offer_monthly: int | None = Field(
        default=None, ge=0, description="Monthly offer already on the table (optional)"
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Differentiating strengths / achievements to justify the ask",
    )


class SalaryNegotiateTool(_SalaryToolBase):
    """Generate negotiation scripts grounded in sampled market data."""

    name = "salary_negotiate"
    description = (
        "Build a salary-negotiation playbook for one position: market position of "
        "the target salary (sampled from the local RAG 'jobs' collection when "
        "available), an anchor suggestion, ready-to-use scripts (opening / value "
        "justification / lowball response / closing), questions to ask about the "
        "total package and cautions. Read-only."
    )
    input_model = SalaryNegotiateToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: SalaryNegotiateToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        position = arguments.position.strip()
        if not position:
            return error_result("'position' 为必填")
        target = arguments.target_monthly
        city = arguments.city.strip()
        strengths = [item.strip() for item in arguments.strengths if item and item.strip()]

        notes: list[str] = []
        market: dict[str, Any] | None = None
        samples, sample_notes, error = await self._collect_samples(
            query=" ".join(part for part in (position, city) if part),
            where={"city": city} if city else None,
        )
        notes.extend(sample_notes)
        if error:
            notes.append(error)
        else:
            kept = [
                s
                for s in samples
                if _city_matches(s["city"], city)
                and _years_compatible(s["experience"], arguments.years)
            ]
            if kept:
                monthly_stats = _stats([_monthly(record) for record in kept])
                market = {
                    "sample_count": len(kept),
                    "monthly": monthly_stats,
                    "city": city,
                }
            else:
                notes.append(f"岗位库中暂无 '{position}' 的可用样本，市场分析省略")

        analysis: dict[str, Any] = {"anchor_monthly": target}
        if market is not None:
            stats = market["monthly"]
            median = stats["median"]
            analysis["target_vs_market_median_pct"] = round((target - median) / median * 100, 1)
            if target <= stats["p25"]:
                position_label = "低于市场 P25：目标偏保守，可适当上调锚点"
            elif target >= stats["p75"]:
                position_label = "高于市场 P75：重点论证差异化价值与总包结构"
            else:
                position_label = "处于市场 P25-P75 区间：按锚点策略正常推进"
            analysis["market_position"] = position_label
            analysis["anchor_monthly"] = max(target, stats["p75"])
            if market["sample_count"] < 5:
                notes.append(f"市场样本仅 {market['sample_count']} 条，分位数据仅供参考")

        strengths_text = "、".join(strengths) if strengths else "核心项目成果与可量化贡献"
        scripts: list[str] = []
        if market is not None:
            stats = market["monthly"]
            scripts.append(
                f"锚定开场：我了解到 {city or '该城市'}{position} 的市场月薪中位约 "
                f"{stats['median']} 元、P75 约 {stats['p75']} 元。结合我的经验与面试表现，"
                f"我的期望是 {analysis['anchor_monthly']} 元/月，也想先了解贵司该岗位的预算区间。"
            )
        else:
            scripts.append(
                f"锚定开场：先给出期望 {target} 元/月（依据市场行情与当前总包），"
                "再询问岗位预算区间，把对话锚定在区间上沿而非底价。"
            )
        experience_text = f"{arguments.years:g} 年经验" if arguments.years else "相关经验"
        scripts.append(
            f"价值论证：用 {experience_text} 与亮点（{strengths_text}）说明你能带来的"
            "可量化价值（如性能提升、成本下降、营收贡献），把薪资诉求转成投资回报讨论。"
        )
        if arguments.current_offer_monthly is not None:
            gap = target - arguments.current_offer_monthly
            scripts.append(
                f"低球应对：目前报价 {arguments.current_offer_monthly} 元/月，与期望相差 "
                f"{gap} 元/月。先确认总包细节（年终奖、股票、补贴、签字费），"
                "再表达对岗位的兴趣并给出可接受的调整空间，避免当场接受或拒绝。"
            )
        scripts.append(
            "收尾确认：请把完整总包（base/年终/股票/补贴）写进 offer letter，"
            "并确认调薪与晋升周期、试用期薪资比例，再给出明确答复时间。"
        )

        payload: dict[str, Any] = {
            "position": position,
            "city": city,
            "target_monthly": target,
            "current_offer_monthly": arguments.current_offer_monthly,
            "market": market,
            "analysis": analysis,
            "scripts": scripts,
            "questions_to_ask": [
                "总包构成：base、年终奖（发放条件）、股票（授予与兑现节奏）、签字费",
                "调薪与晋升周期：一年几次评估、需要达到什么条件",
                "社保公积金缴纳基数与试用期薪资比例",
                "绩效奖金系数与团队近年达成情况",
            ],
            "cautions": [
                "不要在拿到书面 offer 前透露绝对底线（最低可接受数字）",
                "对方报出低于预期的数字时先追问总包细节，不要当场表态",
                "有竞争 offer 时如实说明但不编造具体数字，用时间线推动决策",
            ],
        }
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"has_market": market is not None, "anchor_monthly": analysis["anchor_monthly"]},
        )
