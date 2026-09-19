"""job-hunt CLI subcommand group."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from openharness.jobhunt.output import (
    console,
    render_applications,
    render_jobs,
    render_match_report,
    render_profile,
    render_questions,
    render_status,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

jobhunt_app = typer.Typer(
    name="job-hunt",
    help="Job hunting toolkit: search, match, resumes, interviews and application tracking.",
    invoke_without_command=True,
    no_args_is_help=True,
)
resume_app = typer.Typer(name="resume", help="Resume parsing, optimization and generation.")
applications_app = typer.Typer(name="applications", help="Application tracking board.")
profile_app = typer.Typer(name="profile", help="Local candidate profile.")
cron_app = typer.Typer(name="cron", help="Install or manually run job-hunt cron tasks.")

jobhunt_app.add_typer(resume_app)
jobhunt_app.add_typer(applications_app)
jobhunt_app.add_typer(profile_app)
jobhunt_app.add_typer(cron_app)


def _cwd() -> Path:
    return Path.cwd()


def _read_text_file(path: Path, *, label: str) -> str:
    if not path.exists():
        raise typer.BadParameter(f"{label} not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _json_payload(result: ToolResult) -> dict[str, Any]:
    try:
        data = json.loads(result.output)
    except json.JSONDecodeError:
        return {"message": result.output}
    return data if isinstance(data, dict) else {"data": data}


async def _execute_tool(tool: BaseTool, arguments: BaseModel) -> ToolResult:
    return await tool.execute(arguments, ToolExecutionContext(cwd=_cwd()))


def _run_tool(tool: BaseTool, arguments: BaseModel) -> ToolResult:
    return asyncio.run(_execute_tool(tool, arguments))


def _load_store():
    from openharness.config.settings import load_settings
    from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir

    settings = load_settings()
    directory = resolve_jobhunt_dir(configured=settings.job_hunt.data_directory)
    return JobHuntStore(directory), settings


def _print_tool_error(result: ToolResult) -> None:
    console.print(f"[red]{result.output}[/]")


@jobhunt_app.command("chat")
def chat(
    prompt: Annotated[
        str,
        typer.Option("--prompt", "-p", help="Optional first message for the job-hunt chat."),
    ] = "进入求职助手模式：请结合我的画像、岗位库和投递记录提供求职建议。",
) -> None:
    """Enter job-hunt chat mode."""
    from openharness.ui.app import run_repl

    asyncio.run(run_repl(prompt=prompt, cwd=str(_cwd())))


@jobhunt_app.command("web")
def web(
    host: Annotated[str, typer.Option("--host", help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", min=1, max=65535, help="Port to bind.")] = 8000,
    reload: Annotated[bool, typer.Option("--reload/--no-reload", help="Enable uvicorn reload.")] = False,
    static_dir: Annotated[
        Path | None,
        typer.Option("--static-dir", exists=True, file_okay=False, help="Built jobhunt-web/dist directory."),
    ] = None,
) -> None:
    """Start the FastAPI server for the job-hunt Web UI."""
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        console.print(
            "[red]Web dependencies are not installed.[/] "
            "Run `uv run --extra web oh job-hunt web` or install `openharness-ai[web]`."
        )
        raise typer.Exit(1) from exc

    from openharness.jobhunt.api.app import create_app

    app = create_app(static_dir=static_dir)
    console.print(f"Starting job-hunt Web UI on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=reload)


@jobhunt_app.command("search")
def search_jobs(
    query: Annotated[str, typer.Option("--query", "-q", help="Search query.")] = "",
    city: Annotated[str, typer.Option("--city", help="City filter.")] = "",
    salary_min: Annotated[int | None, typer.Option("--salary-min", help="Minimum monthly salary in K.")] = None,
    top: Annotated[int, typer.Option("--top", "-n", min=1, max=50, help="Number of jobs to show.")] = 10,
    collection: Annotated[str, typer.Option("--collection", "-c", help="RAG collection name.")] = "jobs",
) -> None:
    """Search imported job postings."""
    from openharness.config.settings import load_settings
    from openharness.jobhunt.parsing import parse_jd_text
    from openharness.rag import build_retriever_from_settings

    settings = load_settings()
    effective_query = query.strip() or " ".join(settings.job_hunt.target_positions) or "工程师"
    where: dict[str, Any] = {}
    if city:
        where["city"] = city
    if salary_min is not None:
        where["salary_max"] = {"$gte": salary_min * 1000}
    try:
        retriever = build_retriever_from_settings(settings)
        outcome = asyncio.run(
            retriever.retrieve(
                effective_query,
                collection=collection,
                where=where or None,
                top_n=top,
            )
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]岗位库暂不可用:[/] {exc}")
        console.print("先运行 `oh job-hunt import --dir ./data --collection jobs` 导入岗位数据。")
        return

    rows: list[dict[str, Any]] = []
    for rank, hit in enumerate(outcome.hits, start=1):
        metadata = hit.metadata
        job = parse_jd_text(
            hit.text,
            title_hint=str(metadata.get("title", "")),
            company_hint=str(metadata.get("company", "")),
            city_hint=str(metadata.get("city", "")),
            url=str(metadata.get("url", "")),
            posted_date=str(metadata.get("posted_date", "")),
        )
        rows.append(
            {
                "rank": rank,
                "company": job.company,
                "title": job.title,
                "salary": job.salary,
                "city": job.city,
                "score": hit.score,
                "posted_date": job.posted_date,
            }
        )
    render_jobs(rows, title=f"岗位搜索: {effective_query}")
    for note in outcome.notes:
        console.print(f"[yellow]提示:[/] {note}")


@jobhunt_app.command("match")
def match_resume(
    resume: Annotated[Path, typer.Option("--resume", "-r", exists=True, dir_okay=False, help="Resume text/markdown file.")],
    jd: Annotated[Path | None, typer.Option("--jd", help="Optional JD text file.")] = None,
    top: Annotated[int, typer.Option("--top", "-n", min=1, max=50)] = 5,
) -> None:
    """Match a resume against a JD or the imported jobs collection."""
    from openharness.tools.job_match_tool import JobMatchTool, JobMatchToolInput

    resume_text = _read_text_file(resume, label="resume")
    jd_texts = [_read_text_file(jd, label="JD")] if jd is not None else []
    result = _run_tool(
        JobMatchTool(),
        JobMatchToolInput(resume_text=resume_text, jd_texts=jd_texts, top_k=top),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    render_match_report(_json_payload(result))


@jobhunt_app.command("interview")
def interview(
    company: Annotated[str, typer.Option("--company", help="Target company.")] = "",
    position: Annotated[str, typer.Option("--position", help="Target position.")] = "",
    round: Annotated[str, typer.Option("--round", help="技术 / 行为 / HR / 高管.")] = "技术",
    jd: Annotated[Path | None, typer.Option("--jd", help="JD text file.")] = None,
    count: Annotated[int, typer.Option("--count", min=1, max=20)] = 8,
) -> None:
    """Generate interview questions and answer hints."""
    from openharness.tools.interview_tool import InterviewQuestionsTool, InterviewQuestionsToolInput

    if jd is not None:
        jd_text = _read_text_file(jd, label="JD")
    else:
        jd_text = f"公司：{company}\n岗位：{position}\n技能要求：Java Python Redis MySQL 系统设计"
    result = _run_tool(
        InterviewQuestionsTool(),
        InterviewQuestionsToolInput(
            jd_text=jd_text,
            company=company,
            round=round,
            count=count,
        ),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    render_questions(_json_payload(result))


@resume_app.command("parse")
def resume_parse(
    resume: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Resume text/markdown file.")],
    store_to_rag: Annotated[bool, typer.Option("--store-to-rag/--no-store-to-rag")] = False,
) -> None:
    """Parse a resume and compute an ATS score."""
    from openharness.tools.resume_tool import ResumeParseTool, ResumeParseToolInput

    result = _run_tool(
        ResumeParseTool(),
        ResumeParseToolInput(file_path=str(resume), store_to_rag=store_to_rag),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    payload = _json_payload(result)
    resume_payload = payload.get("resume") or {}
    ats = payload.get("ats") or {}
    console.print(f"[bold]ATS Score:[/] {ats.get('total', '-')}")
    console.print(json.dumps(resume_payload, ensure_ascii=False, indent=2))


@resume_app.command("optimize")
def resume_optimize(
    resume: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    jd: Annotated[Path | None, typer.Option("--jd", help="Optional JD file.")] = None,
) -> None:
    """Generate an ATS/JD alignment checklist for a resume."""
    from openharness.tools.resume_tool import ResumeOptimizeTool, ResumeOptimizeToolInput

    result = _run_tool(
        ResumeOptimizeTool(),
        ResumeOptimizeToolInput(
            resume_text=_read_text_file(resume, label="resume"),
            jd_text=_read_text_file(jd, label="JD") if jd else "",
        ),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    console.print_json(result.output)


@resume_app.command("generate")
def resume_generate(
    jd: Annotated[Path, typer.Option("--jd", exists=True, dir_okay=False, help="JD file.")],
    resume: Annotated[Path | None, typer.Option("--resume", help="Optional source resume file.")] = None,
    template: Annotated[str, typer.Option("--template")] = "classic",
) -> None:
    """Generate a JD-targeted resume draft."""
    from openharness.tools.resume_tool import ResumeGenerateTool, ResumeGenerateToolInput

    result = _run_tool(
        ResumeGenerateTool(),
        ResumeGenerateToolInput(
            jd_text=_read_text_file(jd, label="JD"),
            resume_text=_read_text_file(resume, label="resume") if resume else None,
            template=template,
        ),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    payload = _json_payload(result)
    console.print(payload.get("resume_markdown") or payload.get("generated_resume") or result.output)


@applications_app.command("list")
def applications_list(
    status: Annotated[str, typer.Option("--status", help="Filter by status.")] = "",
    company: Annotated[str, typer.Option("--company", help="Filter by company substring.")] = "",
) -> None:
    """List tracked applications."""
    from openharness.tools.application_tracker_tool import (
        ApplicationListTool,
        ApplicationListToolInput,
    )

    result = _run_tool(ApplicationListTool(), ApplicationListToolInput(status=status, company=company))
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    render_applications(_json_payload(result))


@applications_app.command("add")
def applications_add(
    company: Annotated[str, typer.Option("--company", help="Company name.")],
    position: Annotated[str, typer.Option("--position", help="Position title.")],
    channel: Annotated[str, typer.Option("--channel", help="Submission channel.")] = "其他",
    status: Annotated[str, typer.Option("--status", help="Initial status.")] = "已投递",
    notes: Annotated[str, typer.Option("--notes", help="Optional note.")] = "",
) -> None:
    """Add one application record."""
    from openharness.tools.application_tracker_tool import (
        ApplicationCreateTool,
        ApplicationCreateToolInput,
    )

    result = _run_tool(
        ApplicationCreateTool(),
        ApplicationCreateToolInput(
            company=company,
            position=position,
            channel=channel,
            status=status,
            notes=notes,
        ),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    payload = _json_payload(result)
    created = payload.get("created") or {}
    console.print(f"已添加投递: {created.get('id')} {created.get('company')} / {created.get('position')}")


@applications_app.command("update")
def applications_update(
    application_id: Annotated[str, typer.Argument(help="Application id.")],
    status: Annotated[str, typer.Option("--status", help="New status.")],
    note: Annotated[str, typer.Option("--note", help="Optional update note.")] = "",
) -> None:
    """Update one application's status."""
    from openharness.tools.application_tracker_tool import (
        ApplicationUpdateTool,
        ApplicationUpdateToolInput,
    )

    result = _run_tool(
        ApplicationUpdateTool(),
        ApplicationUpdateToolInput(application_id=application_id, status=status, note=note),
    )
    if result.is_error:
        _print_tool_error(result)
        raise typer.Exit(1)
    updated = (_json_payload(result).get("updated") or {})
    console.print(f"已更新投递: {updated.get('id')} -> {updated.get('status')}")


@jobhunt_app.command("import")
def import_knowledge(
    directory: Annotated[Path, typer.Option("--dir", "-d", exists=True, file_okay=False)],
    collection: Annotated[str, typer.Option("--collection", "-c")] = "knowledge",
    tags: Annotated[
        str,
        typer.Option("--tags", help="Comma-separated tags to attach to imported chunks."),
    ] = "",
    category: Annotated[str, typer.Option("--category")] = "",
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive")] = True,
) -> None:
    """Import local PDF/Markdown/JSON/TXT/HTML knowledge into RAG."""
    from openharness.config.settings import load_settings
    from openharness.rag.embedding import EmbeddingService
    from openharness.rag.sources.local_importer import LocalKnowledgeImporter
    from openharness.rag.vectorstore import VectorStore

    settings = load_settings()
    embedding = EmbeddingService(
        provider=settings.rag.embedding.provider,
        openai_model=settings.rag.embedding.openai_model,
        openai_api_key=settings.rag.embedding.api_key,
        openai_base_url=settings.rag.embedding.base_url,
        local_model=settings.rag.embedding.local_model,
        cache_enabled=settings.rag.embedding.cache_enabled,
        cache_ttl_days=settings.rag.embedding.cache_ttl_days,
    )
    store = VectorStore(
        settings.rag.persist_directory or None,
        embedding_service=embedding,
        collections=(collection,),
    )
    importer = LocalKnowledgeImporter(
        store,
        max_chunks_per_document=settings.rag.max_chunks_per_document,
    )
    report = importer.import_directory(
        directory,
        collection=collection,
        recursive=recursive,
        tags=[tag.strip() for tag in tags.split(",") if tag.strip()],
        category=category,
    )
    console.print(
        "imported: "
        f"processed={report.files_processed}, skipped={report.files_skipped}, "
        f"failed={report.files_failed}, chunks={report.chunks_created}, "
        f"deleted={report.chunks_deleted}"
    )
    if report.errors:
        for error in report.errors:
            console.print(f"[red]error:[/] {error}")


@jobhunt_app.command("status")
def status() -> None:
    """Show the local job-hunt dashboard."""
    store, _settings = _load_store()
    render_status(store.load_profile(), store.load_applications())


@jobhunt_app.command("setup")
def setup(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Use supplied options/defaults without prompting.")] = False,
    rag_enabled: Annotated[bool | None, typer.Option("--rag-enabled/--rag-disabled")] = None,
    embedding_provider: Annotated[str | None, typer.Option("--embedding-provider")] = None,
    cities: Annotated[str | None, typer.Option("--cities", help="Comma-separated target cities.")] = None,
    positions: Annotated[str | None, typer.Option("--positions", help="Comma-separated target positions.")] = None,
    salary_min: Annotated[int | None, typer.Option("--salary-min", help="Expected minimum monthly salary in K.")] = None,
    salary_max: Annotated[int | None, typer.Option("--salary-max", help="Expected maximum monthly salary in K.")] = None,
    years: Annotated[float | None, typer.Option("--years", help="Years of experience.")] = None,
    company_types: Annotated[str | None, typer.Option("--company-types", help="Comma-separated company types.")] = None,
    skills: Annotated[str | None, typer.Option("--skills", help="Comma-separated core skills.")] = None,
    current_title: Annotated[str | None, typer.Option("--current-title")] = None,
    resume: Annotated[Path | None, typer.Option("--resume", exists=True, dir_okay=False)] = None,
    scraping_enabled: Annotated[bool | None, typer.Option("--scraping-enabled/--scraping-disabled")] = None,
) -> None:
    """Run the initialization wizard for job-hunt preferences."""
    from openharness.jobhunt.setup import run_setup

    result = run_setup(
        yes=yes,
        rag_enabled=rag_enabled,
        embedding_provider=embedding_provider,
        cities=cities,
        positions=positions,
        salary_min=salary_min,
        salary_max=salary_max,
        years=years,
        company_types=company_types,
        skills=skills,
        current_title=current_title,
        resume=resume,
        scraping_enabled=scraping_enabled,
    )
    console.print("[green]Job-hunt setup complete.[/]")
    console.print(f"- settings: {result['settings_path']}")
    console.print(f"- data: {result['jobhunt_dir']}")
    console.print(f"- RAG: {result['rag_enabled']} ({result['embedding_provider']})")
    console.print(f"- cities: {', '.join(result['target_cities']) or '-'}")
    console.print(f"- positions: {', '.join(result['target_positions']) or '-'}")
    console.print("Next: `oh job-hunt import --dir ./data --collection jobs` then `oh job-hunt search --query 后端`.")


@profile_app.command("show")
def profile_show() -> None:
    """Show the stored user profile."""
    store, _settings = _load_store()
    render_profile(store.load_profile())


@profile_app.command("update")
def profile_update(
    current_title: Annotated[str, typer.Option("--current-title")] = "",
    cities: Annotated[str, typer.Option("--cities", help="Comma-separated target cities.")] = "",
    skills: Annotated[str, typer.Option("--skills", help="Comma-separated skills.")] = "",
    salary_min: Annotated[int | None, typer.Option("--salary-min")] = None,
    salary_max: Annotated[int | None, typer.Option("--salary-max")] = None,
    years: Annotated[float | None, typer.Option("--years")] = None,
) -> None:
    """Update local profile fields."""
    from openharness.jobhunt.setup import _split_csv

    store, _settings = _load_store()
    updates: dict[str, Any] = {"basic": {}, "preferences": {}, "skills": {}}
    if current_title:
        updates["basic"]["current_title"] = current_title
    if cities:
        updates["basic"]["target_cities"] = _split_csv(cities)
    if years is not None:
        updates["basic"]["years_of_experience"] = years
    if salary_min is not None:
        updates["preferences"]["expected_salary_min"] = salary_min
    if salary_max is not None:
        updates["preferences"]["expected_salary_max"] = salary_max
    if skills:
        updates["skills"]["technical"] = _split_csv(skills)
    updates = {key: value for key, value in updates.items() if value}
    profile = store.merge_profile(updates)
    render_profile(profile)


@cron_app.command("install")
def cron_install() -> None:
    """Install default job-hunt cron jobs."""
    from openharness.services.cron import install_jobhunt_cron_jobs

    names = install_jobhunt_cron_jobs()
    console.print("Installed job-hunt cron jobs: " + ", ".join(names))


@cron_app.command("run")
def cron_run(
    task: Annotated[
        str,
        typer.Argument(help="daily-jobs / follow-ups / interview-digest / data-sync"),
    ],
) -> None:
    """Manually run one job-hunt cron task."""
    from openharness.services.cron import run_jobhunt_cron_task

    try:
        payload = run_jobhunt_cron_task(task)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    console.print_json(json.dumps(payload, ensure_ascii=False))
