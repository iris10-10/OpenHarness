"""Integration tests for local ingestion, persistence and hybrid retrieval."""

from __future__ import annotations

from pathlib import Path

from openharness.rag.sources.local_importer import LocalKnowledgeImporter

SAMPLE_DIR = Path(__file__).parents[2] / "examples" / "sample_data"


async def test_local_import_retrieve_and_incremental_refresh(offline_retriever, tmp_path: Path) -> None:
    """Import knowledge, retrieve it, skip unchanged input, then refresh it."""

    source = SAMPLE_DIR / "interviews" / "python-backend.md"
    importer = LocalKnowledgeImporter(
        offline_retriever.store,
        state_path=offline_retriever.store.persist_directory / "ingestion-state.json",
    )

    first = importer.import_file(
        source,
        collection="interview",
        tags=["python", "backend"],
        category="面经",
    )
    assert first.files_processed == 1
    assert first.chunks_created >= 1

    outcome = await offline_retriever.retrieve(
        "Python GIL MySQL 索引",
        collection="interview",
        top_n=3,
    )
    assert outcome.hits
    assert any("GIL" in hit.text or "MySQL" in hit.text for hit in outcome.hits)
    assert outcome.used_tokens <= outcome.budget_tokens
    assert outcome.hits[0].metadata["category"] == "面经"

    unchanged = importer.import_file(
        source,
        collection="interview",
        tags=["python", "backend"],
        category="面经",
    )
    assert unchanged.chunks_created >= 1

    changed_source = tmp_path / "changed-interview.md"
    changed_source.write_text(
        "# Python 后端二面\n\n重点考察 FastAPI 异步架构、Redis 一致性与故障恢复。",
        encoding="utf-8",
    )
    changed = importer.import_file(changed_source, collection="interview")
    assert changed.files_processed == 1
    refreshed = await offline_retriever.retrieve(
        "FastAPI Redis 一致性",
        collection="interview",
        top_n=3,
    )
    assert any("一致性" in hit.text for hit in refreshed.hits)
