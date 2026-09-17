"""Tests for the rag package surface: lazy exports and settings-driven factories."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from openharness.config.settings import Settings
from openharness.rag import (
    Retriever,
    build_ingestor_from_settings,
    build_retriever_from_settings,
    reset_rag_stack_cache,
)


@pytest.fixture(autouse=True)
def _clear_stack_cache() -> None:
    reset_rag_stack_cache()
    yield
    reset_rag_stack_cache()


def test_package_import_is_lightweight() -> None:
    code = (
        "import sys; import openharness.rag; "
        "assert 'chromadb' not in sys.modules, 'chromadb imported eagerly'; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_lazy_exports_resolve() -> None:
    from openharness import rag

    assert rag.VectorStore.__name__ == "VectorStore"
    assert rag.Retriever.__name__ == "Retriever"
    assert rag.DocumentIngestor.__name__ == "DocumentIngestor"
    assert rag.SearchHit.__name__ == "SearchHit"
    assert "VectorStore" in dir(rag)
    with pytest.raises(AttributeError):
        _ = rag.DoesNotExist  # type: ignore[attr-defined]


def test_build_retriever_from_settings(tmp_path: Path) -> None:
    settings = Settings()
    settings.rag.persist_directory = str(tmp_path / "chromadb")
    settings.rag.embedding.provider = "hash"
    settings.rag.embedding.cache_enabled = False

    retriever = build_retriever_from_settings(settings)
    assert retriever.default_collection == settings.rag.default_collection
    window = settings.context_window_tokens or Retriever.DEFAULT_CONTEXT_WINDOW
    assert retriever.context_budget() == min(
        int(window * settings.rag.retrieval.context_budget_ratio),
        settings.rag.retrieval.max_context_tokens,
    )
    assert retriever.store.persist_directory == Path(tmp_path / "chromadb")
    # the same settings fingerprint reuses the cached stack
    assert build_retriever_from_settings(settings) is retriever


def test_build_ingestor_from_settings(tmp_path: Path) -> None:
    settings = Settings()
    settings.rag.persist_directory = str(tmp_path / "chromadb")
    settings.rag.embedding.provider = "hash"
    settings.rag.embedding.cache_enabled = False
    settings.rag.chunking.chunk_tokens = 128
    settings.rag.chunking.overlap_tokens = 16

    retriever = build_retriever_from_settings(settings)
    ingestor = build_ingestor_from_settings(settings, retriever.store)
    report = ingestor.ingest_text(
        settings.rag.default_collection, "# 标题\n\n" + "内容句子。" * 20, metadata={"source": "t.md"}
    )
    assert report.files_processed == 1
    assert report.chunks_created >= 1
    assert retriever.store.count(settings.rag.default_collection) == report.chunks_created
