"""Tests for the vector store backends, CRUD and metadata filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.rag.embedding import EmbeddingService, HashEmbeddingProvider
from openharness.rag.vectorstore import (
    DEFAULT_COLLECTIONS,
    CollectionManager,
    EmbeddingMismatchError,
    VectorStore,
    sanitize_metadata,
)

try:
    import chromadb  # noqa: F401

    HAS_CHROMA = True
except ImportError:  # pragma: no cover - only when the rag extra is missing
    HAS_CHROMA = False


def test_default_collections_definition() -> None:
    assert DEFAULT_COLLECTIONS == ("jobs", "resumes", "interview", "companies", "knowledge")


def test_sanitize_metadata_drops_none_and_jsonifies_sequences() -> None:
    cleaned = sanitize_metadata(
        {"city": "北京", "score": 3, "active": True, "note": None, "tags": ["a", "b"]}
    )
    assert cleaned["city"] == "北京"
    assert cleaned["score"] == 3
    assert cleaned["active"] is True
    assert "note" not in cleaned
    assert cleaned["tags"] == '["a", "b"]'


def test_sanitize_metadata_empty_input() -> None:
    assert sanitize_metadata(None) == {}
    assert sanitize_metadata({}) == {}


def test_add_search_and_get_documents(store: VectorStore) -> None:
    ids = store.add_documents(
        "jobs",
        [
            {"id": "j1", "text": "Python 后端工程师，负责服务端开发", "metadata": {"city": "北京"}},
            {"text": "市场运营专员，负责活动策划", "metadata": {"city": "上海"}},
        ],
    )
    assert len(ids) == 2
    assert ids[0] == "j1"
    assert ids[1]  # derived from the content hash

    hits = store.search("jobs", "Python 后端开发", top_k=2)
    assert hits and hits[0].id == "j1"
    assert hits[0].metadata["city"] == "北京"

    docs = store.get_documents("jobs")
    assert {doc.id for doc in docs} == set(ids)
    assert store.count("jobs") == 2


def test_empty_texts_are_skipped(store: VectorStore) -> None:
    ids = store.add_documents("jobs", [{"id": "x", "text": "   "}, {"id": "y", "text": "ok"}])
    assert ids == ["y"]


def test_batch_with_duplicate_ids_keeps_last_write(store: VectorStore) -> None:
    store.add_documents(
        "jobs",
        [{"id": "dup", "text": "first"}, {"id": "dup", "text": "second"}],
    )
    docs = store.get_documents("jobs")
    assert len(docs) == 1
    assert docs[0].text == "second"


def test_search_applies_where_filters(store: VectorStore) -> None:
    store.add_documents(
        "jobs",
        [
            {
                "id": "j1",
                "text": "Python 后端工程师",
                "metadata": {"city": "北京", "salary_max": 40000},
            },
            {
                "id": "j2",
                "text": "市场运营专员",
                "metadata": {"city": "上海", "salary_max": 15000},
            },
        ],
    )
    beijing = store.search("jobs", "工程师", where={"city": "北京"})
    assert [hit.id for hit in beijing] == ["j1"]
    rich = store.search("jobs", "专员", where={"salary_max": {"$gte": 30000}})
    assert [hit.id for hit in rich] == ["j1"]


def test_get_documents_operator_filters(store: VectorStore) -> None:
    store.add_documents(
        "jobs",
        [
            {"id": "a", "text": "a", "metadata": {"salary_max": 10000, "city": "北京"}},
            {"id": "b", "text": "b", "metadata": {"salary_max": 30000, "city": "上海"}},
            {"id": "c", "text": "c", "metadata": {"salary_max": 50000, "city": "北京"}},
        ],
    )
    high = store.get_documents("jobs", where={"salary_max": {"$gte": 30000}})
    assert {doc.id for doc in high} == {"b", "c"}
    mid = store.get_documents("jobs", where={"salary_max": {"$gt": 10000, "$lt": 50000}})
    assert {doc.id for doc in mid} == {"b"}
    cities = store.get_documents("jobs", where={"city": {"$in": ["北京"]}})
    assert {doc.id for doc in cities} == {"a", "c"}
    limited = store.get_documents("jobs", where={"city": "北京"}, limit=1)
    assert len(limited) == 1


def test_delete_documents_by_ids_and_where(store: VectorStore) -> None:
    store.add_documents(
        "jobs",
        [
            {"id": "j1", "text": "Python", "metadata": {"city": "北京"}},
            {"id": "j2", "text": "Java", "metadata": {"city": "上海"}},
            {"id": "j3", "text": "Go", "metadata": {"city": "北京"}},
        ],
    )
    assert store.delete_documents("jobs", where={"city": "北京"}) == 2
    assert store.count("jobs") == 1
    assert store.delete_documents("jobs", ids=["j2"]) == 1
    assert store.count("jobs") == 0
    with pytest.raises(ValueError):
        store.delete_documents("jobs")


def test_search_empty_collection_and_blank_query(store: VectorStore) -> None:
    assert store.search("knowledge", "anything") == []
    assert store.search("jobs", "   ") == []


def test_store_persists_across_instances(tmp_path: Path, hash_service: EmbeddingService) -> None:
    first = VectorStore(tmp_path / "persist", backend="simple", embedding_service=hash_service)
    first.add_documents("jobs", [{"id": "j1", "text": "Python 后端", "metadata": {"city": "北京"}}])

    second = VectorStore(tmp_path / "persist", backend="simple", embedding_service=hash_service)
    assert second.count("jobs") == 1
    docs = second.get_documents("jobs")
    assert docs[0].id == "j1"
    assert docs[0].metadata["city"] == "北京"
    hits = second.search("jobs", "Python")
    assert hits and hits[0].id == "j1" and hits[0].score > 0.3


def test_revision_increments_on_writes(store: VectorStore) -> None:
    assert store.revision("jobs") == 0
    store.add_documents("jobs", [{"id": "j1", "text": "Python"}])
    assert store.revision("jobs") == 1
    store.search("jobs", "Python")
    assert store.revision("jobs") == 1
    store.delete_documents("jobs", ids=["j1"])
    assert store.revision("jobs") == 2


def test_embedding_mismatch_is_reported(tmp_path: Path) -> None:
    store_384 = VectorStore(
        tmp_path / "mismatch",
        backend="simple",
        embedding_service=EmbeddingService(
            providers=[HashEmbeddingProvider(dimension=384)], cache_enabled=False
        ),
    )
    store_384.add_documents("jobs", [{"id": "j1", "text": "Python"}])

    store_256 = VectorStore(
        tmp_path / "mismatch",
        backend="simple",
        embedding_service=EmbeddingService(
            providers=[HashEmbeddingProvider(dimension=256)], cache_enabled=False
        ),
    )
    with pytest.raises(EmbeddingMismatchError) as excinfo:
        store_256.search("jobs", "Python")
    assert excinfo.value.stored == "hash:blake2b-384"
    assert excinfo.value.current == "hash:blake2b-256"
    with pytest.raises(EmbeddingMismatchError):
        store_256.add_documents("jobs", [{"id": "j2", "text": "Java"}])


def test_collection_manager_reports_and_creates_missing(
    tmp_path: Path, hash_service: EmbeddingService
) -> None:
    store = VectorStore(
        tmp_path / "manager",
        backend="simple",
        embedding_service=hash_service,
        auto_create_collections=False,
        collections=(),
    )
    manager = CollectionManager(store, ["jobs", "knowledge"])
    assert manager.expected_collections == ["jobs", "knowledge"]
    assert manager.missing() == ["jobs", "knowledge"]
    assert set(manager.ensure_all()) == {"jobs", "knowledge"}
    assert manager.missing() == []
    assert manager.ensure_all() == []


def test_drop_collection(store: VectorStore) -> None:
    store.add_documents("scratch", [{"id": "x", "text": "hi"}])
    assert "scratch" in store.list_collections()
    assert store.drop_collection("scratch") is True
    assert store.drop_collection("scratch") is False
    assert "scratch" not in store.list_collections()


def test_auto_backend_selection(tmp_path: Path, hash_service: EmbeddingService) -> None:
    store = VectorStore(tmp_path / "auto", backend="auto", embedding_service=hash_service)
    expected = "chroma" if HAS_CHROMA else "simple"
    assert store.backend_name == expected


def test_unknown_backend_is_rejected(tmp_path: Path, hash_service: EmbeddingService) -> None:
    with pytest.raises(ValueError):
        VectorStore(tmp_path / "bad", backend="nope", embedding_service=hash_service)


@pytest.mark.skipif(not HAS_CHROMA, reason="chromadb is not installed")
def test_chroma_backend_roundtrip(tmp_path: Path, hash_service: EmbeddingService) -> None:
    store = VectorStore(tmp_path / "chroma", backend="chroma", embedding_service=hash_service)
    store.add_documents(
        "jobs",
        [
            {
                "id": "j1",
                "text": "Python 后端工程师，负责服务端开发",
                "metadata": {"city": "北京", "salary_max": 40000},
            },
            {
                "id": "j2",
                "text": "市场运营专员，负责活动策划",
                "metadata": {"city": "上海", "salary_max": 15000},
            },
        ],
    )
    assert store.count("jobs") == 2
    hits = store.search("jobs", "Python 后端开发", top_k=2)
    assert hits and hits[0].id == "j1"

    filtered = store.search("jobs", "专员", where={"city": "上海"})
    assert [hit.id for hit in filtered] == ["j2"]
    rich = store.search("jobs", "工程师", where={"salary_max": {"$gte": 30000}})
    assert [hit.id for hit in rich] == ["j1"]

    assert store.delete_documents("jobs", ids=["j2"]) == 1
    assert store.count("jobs") == 1
    assert store.drop_collection("jobs") is True


@pytest.mark.skipif(not HAS_CHROMA, reason="chromadb is not installed")
def test_chroma_backend_persists_across_instances(
    tmp_path: Path, hash_service: EmbeddingService
) -> None:
    first = VectorStore(tmp_path / "chroma_persist", backend="chroma", embedding_service=hash_service)
    first.add_documents("knowledge", [{"id": "k1", "text": "持久化验证内容"}])

    second = VectorStore(
        tmp_path / "chroma_persist", backend="chroma", embedding_service=hash_service
    )
    assert second.count("knowledge") == 1
    assert second.get_documents("knowledge")[0].id == "k1"
