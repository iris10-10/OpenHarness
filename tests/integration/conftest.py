"""Shared offline fixtures for Phase 7 integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.config.schema import RagEmbeddingSettings, RagSettings
from openharness.config.settings import Settings, save_settings
from openharness.rag import reset_rag_stack_cache
from openharness.rag.embedding import EmbeddingService
from openharness.rag.retriever import Retriever
from openharness.rag.vectorstore import VectorStore


@pytest.fixture
def isolated_jobhunt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | Settings]:
    """Provide a deterministic, fully local job-hunt environment."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    jobhunt_dir = data_dir / "jobhunt"
    rag_dir = data_dir / "rag"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPENHARNESS_JOBHUNT_DIR", str(jobhunt_dir))
    monkeypatch.setenv("OPENHARNESS_RAG_DATA_DIR", str(rag_dir))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENHARNESS_EMBEDDING_API_KEY", raising=False)

    settings = Settings().model_copy(
        update={
            "rag": RagSettings(
                enabled=True,
                persist_directory=str(rag_dir),
                embedding=RagEmbeddingSettings(provider="hash", cache_enabled=False),
            ),
            "job_hunt": Settings().job_hunt.model_copy(
                update={"target_cities": ["杭州"], "target_positions": ["Python 后端工程师"]}
            ),
        }
    )
    save_settings(settings)
    reset_rag_stack_cache()
    yield {
        "config_dir": config_dir,
        "data_dir": data_dir,
        "jobhunt_dir": jobhunt_dir,
        "rag_dir": rag_dir,
        "settings": settings,
    }
    reset_rag_stack_cache()


@pytest.fixture
def offline_retriever(isolated_jobhunt: dict[str, Path | Settings]) -> Retriever:
    """Build the real retriever with the dependency-free vector backend."""

    settings = isolated_jobhunt["settings"]
    assert isinstance(settings, Settings)
    embedding = EmbeddingService(provider="hash", cache_enabled=False)
    store = VectorStore(
        isolated_jobhunt["rag_dir"],
        backend="simple",
        embedding_service=embedding,
    )
    return Retriever(
        store,
        alpha=settings.rag.retrieval.hybrid_alpha,
        candidate_pool=settings.rag.retrieval.candidate_pool,
        vector_top_k=settings.rag.retrieval.vector_top_k,
        final_top_n=settings.rag.retrieval.final_top_n,
        context_budget_ratio=settings.rag.retrieval.context_budget_ratio,
        context_window_tokens=settings.context_window_tokens,
        max_context_tokens=settings.rag.retrieval.max_context_tokens,
        default_collection=settings.rag.default_collection,
    )
