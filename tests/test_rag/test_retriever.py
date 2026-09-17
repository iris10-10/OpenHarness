"""Tests for hybrid recall, reranking, context budgeting and formatters."""

from __future__ import annotations

import pytest

from openharness.rag.retriever import (
    HybridSearcher,
    RankedHit,
    Reranker,
    Retriever,
    format_retrieval_for_prompt,
    format_retrieval_for_tool,
    pack_context_hits,
)
from openharness.rag.vectorstore import VectorStore

RECALL_CASES = [
    ("Python 后端开发", "jobs", "j1"),
    ("市场运营活动策划", "jobs", "j2"),
    ("分布式系统架构师", "jobs", "j3"),
    ("字节跳动后端一面 GIL", "interview", "i1"),
    ("美团数据分析 SQL", "interview", "i2"),
]


# --------------------------------------------------------------------------- 召回


def test_hybrid_alpha_extremes_map_to_component_scores(seeded_store: VectorStore) -> None:
    vector_only = HybridSearcher(seeded_store, alpha=1.0).search("Python", collection="jobs")
    assert vector_only
    assert all(abs(hit.score - (hit.vector_score + 1.0) / 2.0) < 1e-9 for hit in vector_only)

    keyword_only = HybridSearcher(seeded_store, alpha=0.0).search("Python", collection="jobs")
    assert keyword_only
    assert all(abs(hit.score - hit.bm25_score) < 1e-9 for hit in keyword_only)


def test_hybrid_searcher_rejects_invalid_alpha(seeded_store: VectorStore) -> None:
    with pytest.raises(ValueError):
        HybridSearcher(seeded_store, alpha=1.5)


def test_hybrid_search_prioritizes_keyword_match(seeded_store: VectorStore) -> None:
    hits = HybridSearcher(seeded_store, alpha=0.5).search("Django 框架", collection="jobs")
    assert hits and hits[0].id == "j1"


def test_hybrid_search_applies_where_filter(seeded_store: VectorStore) -> None:
    hits = HybridSearcher(seeded_store).search("工程师", collection="jobs", where={"city": "北京"})
    assert hits and all(hit.metadata["city"] == "北京" for hit in hits)


def test_hybrid_searcher_refreshes_index_after_writes(seeded_store: VectorStore) -> None:
    searcher = HybridSearcher(seeded_store, alpha=0.5)
    before = searcher.search("Rust", collection="jobs")
    assert all("Rust" not in hit.text for hit in before)

    seeded_store.add_documents(
        "jobs",
        [{"id": "j9", "text": "Rust 系统工程师，负责底层开发", "metadata": {"city": "深圳"}}],
    )
    refreshed = searcher.search("Rust", collection="jobs")
    assert refreshed and refreshed[0].id == "j9"


def test_hybrid_search_empty_collection(store: VectorStore) -> None:
    assert HybridSearcher(store).search("anything", collection="knowledge") == []


def test_top5_recall_meets_target(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, final_top_n=5, vector_top_k=10)
    found = 0
    for query, collection, expected in RECALL_CASES:
        outcome = retriever.retrieve_sync(query, collection=collection)
        if expected in [hit.id for hit in outcome.hits]:
            found += 1
    assert found / len(RECALL_CASES) >= 0.8


# --------------------------------------------------------------------------- 重排


def test_reranker_boosts_title_matches() -> None:
    hits = [
        RankedHit(id="a", text="random", metadata={"title": "无关标题"}, score=0.50),
        RankedHit(id="b", text="random", metadata={"title": "Python 面试题"}, score=0.48),
    ]
    reranked = Reranker(strategy="heuristic").rerank_sync("Python", hits, top_n=2)
    assert reranked[0].id == "b"


def test_reranker_boosts_keyword_overlap() -> None:
    boosted = RankedHit(
        id="a", text="x", metadata={"keywords": "python, django, mysql, redis"}, score=0.40
    )
    plain = RankedHit(id="b", text="y", metadata={}, score=0.42)
    reranked = Reranker().rerank_sync("python django mysql", [boosted, plain], top_n=2)
    assert reranked[0].id == "a"


async def test_reranker_uses_injected_llm() -> None:
    calls: list[str] = []

    async def fake_llm(query: str, texts: list[str]) -> list[float]:
        del texts
        calls.append(query)
        return [0.0, 1.0]

    hits = [
        RankedHit(id="a", text="first", metadata={}, score=0.9),
        RankedHit(id="b", text="second", metadata={}, score=0.1),
    ]
    reranked = await Reranker(llm_reranker=fake_llm).rerank("query", hits, top_n=2)
    assert calls == ["query"]
    assert reranked[0].id == "b"


async def test_reranker_falls_back_when_llm_fails() -> None:
    async def broken_llm(query: str, texts: list[str]) -> list[float]:
        del query, texts
        raise RuntimeError("llm down")

    hits = [RankedHit(id="a", text="first", metadata={}, score=0.9)]
    reranked = await Reranker(llm_reranker=broken_llm).rerank("query", hits, top_n=1)
    assert [hit.id for hit in reranked] == ["a"]


async def test_reranker_ignores_mismatched_llm_scores() -> None:
    async def weird_llm(query: str, texts: list[str]) -> list[float]:
        del query, texts
        return [1.0]  # wrong length

    hits = [
        RankedHit(id="a", text="first", metadata={}, score=0.9),
        RankedHit(id="b", text="second", metadata={}, score=0.1),
    ]
    reranked = await Reranker(llm_reranker=weird_llm).rerank("query", hits, top_n=2)
    assert [hit.id for hit in reranked] == ["a", "b"]  # heuristic order preserved


def test_reranker_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError):
        Reranker(strategy="magic")


# --------------------------------------------------------------------------- 预算


def test_pack_context_hits_fits_budget() -> None:
    hits = [
        RankedHit(id="a", text="hello world", metadata={}, score=0.9),
        RankedHit(id="b", text="second entry", metadata={}, score=0.5),
    ]
    packed, used = pack_context_hits(hits, budget_tokens=100)
    assert [hit.id for hit in packed] == ["a", "b"]
    assert used == 4


def test_pack_context_hits_truncates_overflow() -> None:
    long_text = " ".join(f"w{index:03d}" for index in range(100))  # ~100 tokens
    hits = [RankedHit(id="a", text=long_text, metadata={}, score=0.9)]
    packed, used = pack_context_hits(hits, budget_tokens=50)
    assert len(packed) == 1
    assert used <= 50
    assert len(packed[0].text) < len(long_text)


def test_pack_context_hits_drops_fragments_below_minimum() -> None:
    hits = [RankedHit(id="a", text=" ".join(f"w{index}" for index in range(40)), metadata={}, score=0.9)]
    assert pack_context_hits(hits, budget_tokens=10) == ([], 0)


def test_pack_context_hits_zero_budget() -> None:
    hits = [RankedHit(id="a", text="hello", metadata={}, score=0.9)]
    assert pack_context_hits(hits, budget_tokens=0) == ([], 0)


# --------------------------------------------------------------------------- 检索器


def test_retriever_properties_and_budget(seeded_store: VectorStore) -> None:
    retriever = Retriever(
        seeded_store,
        context_budget_ratio=0.25,
        context_window_tokens=16000,
        max_context_tokens=4000,
        default_collection="jobs",
    )
    assert retriever.store is seeded_store
    assert retriever.default_collection == "jobs"
    assert retriever.context_budget() == 4000

    tight = Retriever(seeded_store, context_budget_ratio=0.1, context_window_tokens=16000)
    assert tight.context_budget() == 1600
    capped = Retriever(
        seeded_store, context_budget_ratio=1.0, context_window_tokens=16000, max_context_tokens=500
    )
    assert capped.context_budget() == 500
    disabled = Retriever(seeded_store, context_budget_ratio=0.0)
    assert disabled.context_budget() == 0


def test_retriever_sync_search(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="jobs", final_top_n=3)
    outcome = retriever.retrieve_sync("Python 后端开发")
    assert outcome.hits
    assert outcome.collections == ["jobs"]
    assert outcome.used_tokens <= outcome.budget_tokens
    assert outcome.budget_tokens == 4000


async def test_retriever_async_matches_sync_ranking(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="jobs")
    sync_outcome = retriever.retrieve_sync("Python 后端开发")
    async_outcome = await retriever.retrieve("Python 后端开发")
    assert [hit.id for hit in async_outcome.hits] == [hit.id for hit in sync_outcome.hits]


def test_retriever_reports_empty_collections(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="resumes")
    outcome = retriever.retrieve_sync("anything")
    assert outcome.hits == []
    assert any("no documents indexed yet" in note for note in outcome.notes)


def test_retriever_searches_multiple_collections(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store)
    outcome = retriever.retrieve_sync("Python", collections=["jobs", "interview"])
    assert outcome.collections == ["jobs", "interview"]


def test_retriever_respects_top_n_and_budget_override(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="jobs", final_top_n=5)
    single = retriever.retrieve_sync("工程师", top_n=1)
    assert len(single.hits) == 1

    tiny = retriever.retrieve_sync("工程师", budget_tokens=5)
    assert tiny.budget_tokens == 5
    assert tiny.hits == [] and tiny.used_tokens == 0


def test_retriever_where_filter(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="jobs")
    outcome = retriever.retrieve_sync("工程师", where={"city": "上海"})
    assert outcome.hits
    assert all(hit.metadata["city"] == "上海" for hit in outcome.hits)


# --------------------------------------------------------------------------- 格式化


def test_format_retrieval_for_tool(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="jobs")
    outcome = retriever.retrieve_sync("Python 后端")
    text = format_retrieval_for_tool(outcome)
    assert text.startswith("Found ")
    assert "j1" in text

    empty = retriever.retrieve_sync("zzz", collection="resumes")
    assert format_retrieval_for_tool(empty).startswith("No matches found")
    assert format_retrieval_for_tool(outcome, max_chars=20).endswith("… (truncated)")


def test_format_retrieval_for_prompt(seeded_store: VectorStore) -> None:
    retriever = Retriever(seeded_store, default_collection="jobs")
    outcome = retriever.retrieve_sync("Python 后端")
    text = format_retrieval_for_prompt(outcome)
    assert text.startswith("Retrieved ")
    assert "local knowledge base" in text
    assert format_retrieval_for_prompt(outcome, max_chars=20).endswith("… (truncated)")

    empty = retriever.retrieve_sync("zzz", collection="resumes")
    assert format_retrieval_for_prompt(empty) == ""
