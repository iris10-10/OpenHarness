"""job-hunt CLI subcommand entry."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

jobhunt_app = typer.Typer(
    name="job-hunt",
    help="Job hunting toolkit: resumes, job matching, interview prep and tracking.",
    invoke_without_command=True,
)


@jobhunt_app.callback(invoke_without_command=True)
def jobhunt_main(ctx: typer.Context) -> None:
    """Job-hunt command group."""

    if ctx.invoked_subcommand is None:
        print("job-hunt: use 'oh job-hunt import --dir ./data --collection knowledge'.")


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
    print(
        "imported: "
        f"processed={report.files_processed}, skipped={report.files_skipped}, "
        f"failed={report.files_failed}, chunks={report.chunks_created}, "
        f"deleted={report.chunks_deleted}"
    )
    if report.errors:
        for error in report.errors:
            print(f"error: {error}")
