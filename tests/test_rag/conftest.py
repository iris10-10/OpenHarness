"""Shared fixtures for RAG tests: all state stays inside tmp_path."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from openharness.config.settings import Settings
from openharness.rag.embedding import EmbeddingService
from openharness.rag.vectorstore import VectorStore

JOB_DOCUMENTS: list[dict[str, object]] = [
    {
        "id": "j1",
        "text": "Python 后端工程师，负责服务端开发，熟悉 Django 框架与 MySQL 调优",
        "metadata": {
            "title": "Python 后端工程师",
            "city": "北京",
            "salary_min": 25000,
            "salary_max": 40000,
        },
    },
    {
        "id": "j2",
        "text": "市场运营专员，负责品牌活动策划与用户增长",
        "metadata": {
            "title": "市场运营专员",
            "city": "上海",
            "salary_min": 12000,
            "salary_max": 18000,
        },
    },
    {
        "id": "j3",
        "text": "资深 Python 架构师，负责分布式系统与微服务治理",
        "metadata": {
            "title": "资深架构师",
            "city": "北京",
            "salary_min": 45000,
            "salary_max": 60000,
        },
    },
]

INTERVIEW_DOCUMENTS: list[dict[str, object]] = [
    {
        "id": "i1",
        "text": "字节跳动后端一面：自我介绍后问了 Python GIL、协程与 MySQL 索引优化",
        "metadata": {
            "title": "字节跳动后端一面",
            "company": "字节跳动",
            "position": "后端工程师",
        },
    },
    {
        "id": "i2",
        "text": "美团数据分析二面：SQL 窗口函数与 A/B 测试设计",
        "metadata": {"title": "美团数据分析二面", "company": "美团", "position": "数据分析师"},
    },
]


@pytest.fixture(autouse=True)
def _isolate_rag_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect user-level dirs and credentials so tests never touch the profile."""
    home = tmp_path / "openharness_home"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(home))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(home / "data"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENHARNESS_EMBEDDING_API_KEY", raising=False)
    yield


@pytest.fixture
def hash_service(tmp_path: Path) -> EmbeddingService:
    """Deterministic offline embedding service (no network, no cache files)."""
    return EmbeddingService(
        provider="hash",
        cache_enabled=False,
        cache_dir=tmp_path / "embedding_cache",
    )


@pytest.fixture
def settings() -> Settings:
    """Default settings (RAG disabled)."""
    return Settings()


@pytest.fixture
def store(tmp_path: Path, hash_service: EmbeddingService) -> VectorStore:
    """Simple-backend vector store isolated in tmp_path."""
    return VectorStore(tmp_path / "store", backend="simple", embedding_service=hash_service)


@pytest.fixture
def seeded_store(store: VectorStore) -> VectorStore:
    """Store preloaded with job postings and interview notes."""
    store.add_documents("jobs", JOB_DOCUMENTS)
    store.add_documents("interview", INTERVIEW_DOCUMENTS)
    return store
