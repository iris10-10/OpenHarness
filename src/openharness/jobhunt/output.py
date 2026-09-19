"""Rich output helpers for the job-hunt CLI."""

from __future__ import annotations

from collections import Counter
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from openharness.jobhunt.parsing import SalaryRange

console = Console()


def salary_text(salary: SalaryRange | dict[str, Any] | str | None) -> str:
    """Render a salary value in a compact human-friendly form."""
    if not salary:
        return "-"
    if isinstance(salary, str):
        return salary or "-"
    if isinstance(salary, SalaryRange):
        text = f"{salary.min_monthly / 1000:.0f}-{salary.max_monthly / 1000:.0f}K"
        if salary.months_per_year and salary.months_per_year != 12:
            text += f"*{salary.months_per_year}"
        return text
    raw = str(salary.get("raw") or "").strip()
    if raw:
        return raw
    low = salary.get("min_monthly")
    high = salary.get("max_monthly")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        text = f"{low / 1000:.0f}-{high / 1000:.0f}K"
        months = salary.get("months_per_year")
        if months and months != 12:
            text += f"*{months}"
        return text
    return "-"


def score_bar(score: float | None, *, width: int = 18) -> str:
    """Return an ASCII score bar suitable for tables."""
    value = max(0.0, min(100.0, float(score or 0)))
    filled = round(value / 100 * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {value:>5.1f}"


def render_jobs(jobs: list[dict[str, Any]], *, title: str = "岗位搜索结果") -> None:
    """Render a job list as a scan-friendly table."""
    table = Table(title=title, box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("#", justify="right", style="cyan", width=4)
    table.add_column("公司", overflow="fold")
    table.add_column("岗位", overflow="fold")
    table.add_column("薪资", no_wrap=True)
    table.add_column("城市", no_wrap=True)
    table.add_column("匹配/相关度", no_wrap=True)
    table.add_column("发布时间", no_wrap=True)
    for index, job in enumerate(jobs, start=1):
        score = job.get("score")
        score_value = "" if score is None else f"{float(score):.2f}"
        table.add_row(
            str(job.get("rank") or index),
            str(job.get("company") or "-"),
            str(job.get("title") or "-"),
            salary_text(job.get("salary")),
            str(job.get("city") or "-"),
            str(job.get("match") or job.get("recommendation") or score_value or "-"),
            str(job.get("posted_date") or "-"),
        )
    console.print(table)


def render_match_report(payload: dict[str, Any]) -> None:
    """Render a matching payload returned by the matching engine/tool."""
    matches = list(payload.get("matches") or [])
    summary = payload.get("candidate_summary") or {}
    console.print(
        Panel(
            "\n".join(
                [
                    f"技能数: {summary.get('skill_count', '-')}",
                    f"经验: {summary.get('years_of_experience', '-')}",
                    f"目标城市: {', '.join(summary.get('target_cities') or []) or '-'}",
                ]
            ),
            title="候选人摘要",
            border_style="cyan",
        )
    )
    table = Table(title="岗位匹配 Top-K", box=box.SIMPLE_HEAVY)
    table.add_column("#", justify="right", style="cyan", width=4)
    table.add_column("公司")
    table.add_column("岗位")
    table.add_column("得分", no_wrap=True)
    table.add_column("建议", no_wrap=True)
    table.add_column("缺口", overflow="fold")
    for item in matches:
        job = item.get("job") or {}
        missing = "、".join(item.get("missing_skills") or [])
        table.add_row(
            str(item.get("rank", "")),
            str(job.get("company") or "-"),
            str(job.get("title") or "-"),
            score_bar(item.get("score")),
            str(item.get("recommendation") or "-"),
            missing or "-",
        )
    console.print(table)
    if matches:
        best = matches[0]
        dimensions = best.get("dimensions") or []
        if dimensions:
            radar = Table(title="最佳岗位维度雷达", box=box.MINIMAL)
            radar.add_column("维度")
            radar.add_column("得分")
            radar.add_column("说明", overflow="fold")
            for dimension in dimensions:
                radar.add_row(
                    str(dimension.get("name", "")),
                    score_bar(dimension.get("score"), width=12),
                    str(dimension.get("detail", "")),
                )
            console.print(radar)
    for note in payload.get("notes") or []:
        console.print(f"[yellow]提示:[/] {note}")


def render_questions(payload: dict[str, Any]) -> None:
    """Render interview questions grouped by source/category."""
    console.print(
        Panel(
            f"轮次: {payload.get('round', '-')}\n难度: {payload.get('difficulty', '-')}\n"
            f"总题数: {payload.get('total', 0)}",
            title=f"面试准备 {payload.get('company') or ''}".strip(),
            border_style="magenta",
        )
    )
    rows: list[dict[str, Any]] = []
    for item in payload.get("real_questions") or []:
        rows.append({"source": "面经", "category": "真实", "question": item.get("excerpt", ""), "hint": item.get("doc_id", "")})
    for item in payload.get("generated_questions") or []:
        rows.append({"source": "题库", **item})
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("#", justify="right", width=4)
    table.add_column("来源", no_wrap=True)
    table.add_column("分类", no_wrap=True)
    table.add_column("题目", overflow="fold")
    table.add_column("答题要点", overflow="fold")
    for index, row in enumerate(rows, start=1):
        table.add_row(
            str(index),
            str(row.get("source") or "-"),
            str(row.get("category") or "-"),
            str(row.get("question") or "-"),
            str(row.get("hint") or "-"),
        )
    console.print(table)
    for note in payload.get("notes") or []:
        console.print(f"[yellow]提示:[/] {note}")


def render_applications(payload: dict[str, Any]) -> None:
    """Render tracked applications and status counts."""
    summary = payload.get("summary") or {}
    counts = summary.get("by_status") or {}
    console.print(render_pipeline(counts))
    table = Table(title="投递记录", box=box.SIMPLE_HEAVY)
    table.add_column("ID", no_wrap=True)
    table.add_column("公司")
    table.add_column("岗位")
    table.add_column("状态", no_wrap=True)
    table.add_column("投递日期", no_wrap=True)
    table.add_column("最近更新", no_wrap=True)
    for record in payload.get("applications") or []:
        table.add_row(
            str(record.get("id", "")),
            str(record.get("company", "")),
            str(record.get("position", "")),
            str(record.get("status", "")),
            str(record.get("applied_date", "")),
            str(record.get("last_update_date") or record.get("updated_at") or ""),
        )
    console.print(table)


def render_pipeline(counts: dict[str, int]) -> Panel:
    """Return a compact Kanban-like status pipeline panel."""
    statuses = ("已投递", "简历筛选中", "笔试", "一面", "二面", "HR面", "Offer", "已入职", "已拒绝")
    parts = [f"{status} {int(counts.get(status, 0))}" for status in statuses]
    return Panel("  ->  ".join(parts), title="投递看板", border_style="green")


def render_status(profile: dict[str, Any], applications: list[dict[str, Any]]) -> None:
    """Render the job-hunt dashboard."""
    counts = Counter(str(item.get("status", "")) for item in applications)
    active = sum(count for status, count in counts.items() if status not in {"已入职", "已拒绝"})
    basic = profile.get("basic") if isinstance(profile.get("basic"), dict) else {}
    skills = profile.get("skills") if isinstance(profile.get("skills"), dict) else {}
    technical = skills.get("technical") if isinstance(skills.get("technical"), list) else []
    console.print(
        Panel(
            "\n".join(
                [
                    f"当前方向: {basic.get('current_title') or '-'}",
                    f"目标城市: {', '.join(basic.get('target_cities') or []) or '-'}",
                    f"技能: {', '.join(str(item) for item in technical[:10]) or '-'}",
                    f"投递: {len(applications)} 条，其中进行中 {active} 条",
                ]
            ),
            title="求职状态",
            border_style="cyan",
        )
    )
    console.print(render_pipeline(dict(counts)))


def render_profile(profile: dict[str, Any]) -> None:
    """Render the stored candidate profile."""
    if not profile:
        console.print("[yellow]尚未建立用户画像。运行 `oh job-hunt setup` 或 `oh job-hunt profile update`。[/]")
        return
    basic = profile.get("basic") if isinstance(profile.get("basic"), dict) else {}
    preferences = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}
    skills = profile.get("skills") if isinstance(profile.get("skills"), dict) else {}
    console.print(
        Panel(
            "\n".join(
                [
                    f"当前职位: {basic.get('current_title') or '-'}",
                    f"经验年限: {basic.get('years_of_experience') or '-'}",
                    f"目标城市: {', '.join(basic.get('target_cities') or []) or '-'}",
                    f"期望薪资: {preferences.get('expected_salary_min') or '-'}-{preferences.get('expected_salary_max') or '-'}K",
                    f"技能: {', '.join(str(item) for item in skills.get('technical') or []) or '-'}",
                ]
            ),
            title="用户画像",
            border_style="blue",
        )
    )
