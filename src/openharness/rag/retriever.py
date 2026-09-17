"""Two-stage retrieval: hybrid recall, reranking and context budgeting.

Stage one (:class:`HybridSearcher`) combines vector similarity with BM25
keyword scores::

    score = alpha * vector_score + (1 - alpha) * bm25_score

Stage two (:class:`Reranker`) applies metadata boosts and optionally an
injected async LLM scorer. :func:`pack_context_hits` then fills the context
budget highest-score-first, truncating the overflow.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import math
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from openharness.rag.utils import estimate_tokens, stable_json_dumps, tokenize_text
from openharness.rag.vectorstore import StoredDocument, VectorStore

LLMRerankerCallable = Callable[[str, list[str]], Awaitable[list[float]]]

#截断块的最小可用 token 数（预算过小则直接丢弃，避免正文碎片）
_MIN_TRUNCATED_TOKENS = 32


class _SimpleBM25:
    """Minimal BM25Okapi implementation used when rank-bm25 is unavailable."""

    def __init__(
        self,
        corpus_tokens: Sequence[Sequence[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._corpus = [list(tokens) for tokens in corpus_tokens]
        self._k1 = k1
        self._b = b
        self._doc_freqs: list[dict[str, int]] = []
        document_frequency: dict[str, int] = {}
        for tokens in self._corpus:
            freqs: dict[str, int] = {}
            for token in tokens:
                freqs[token] = freqs.get(token, 0) + 1
            self._doc_freqs.append(freqs)
            for token in freqs:
                document_frequency[token] = document_frequency.get(token, 0) + 1
        corpus_size = len(self._corpus)
        self._idf: dict[str, float] = {
            token: math.log(1 + (corpus_size - freq + 0.5) / (freq + 0.5))
            for token, freq in document_frequency.items()
        }
        total_length = sum(sum(freqs.values()) for freqs in self._doc_freqs)
        self._avg_length = total_length / corpus_size if corpus_size else 0.0

    def scores(self, query_tokens: Sequence[str]) -> list[float]:
        """Return one BM25 score per corpus document."""
        scores = [0.0] * len(self._corpus)
        if not self._corpus:
            return scores
        for index, freqs in enumerate(self._doc_freqs):
            length = sum(freqs.values())
            if length == 0:
                continue
            score = 0.0
            for token in query_tokens:
                frequency = freqs.get(token)
                if not frequency:
                    continue
                idf = self._idf.get(token, 0.0)
                denominator = frequency + self._k1 * (
                    1 - self._b + self._b * length / (self._avg_length or 1.0)
                )
                score += idf * frequency * (self._k1 + 1) / denominator
            scores[index] = score
        return scores


class _RankBM25Adapter:
    """Adapter over the optional rank-bm25 dependency."""

    def __init__(self, corpus_tokens: Sequence[Sequence[str]]) -> None:
        rank_bm25 = importlib.import_module("rank_bm25")
        self._inner = rank_bm25.BM25Okapi([list(tokens) for tokens in corpus_tokens])

    def scores(self, query_tokens: Sequence[str]) -> list[float]:
        """Return one BM25 score per corpus document."""
        return [float(value) for value in self._inner.get_scores(list(query_tokens))]


def _build_bm25(corpus_tokens: Sequence[Sequence[str]]) -> _SimpleBM25 | _RankBM25Adapter:
    """Build a BM25 scorer, preferring rank-bm25 when installed."""
    if importlib.util.find_spec("rank_bm25") is not None:
        try:
            return _RankBM25Adapter(corpus_tokens)
        except Exception:
            pass
    return _SimpleBM25(corpus_tokens)


def _normalize_scores(values: Sequence[float]) -> list[float]:
    """Min-max normalize scores into [0, 1] (constant series map to 1)."""
    if not values:
        return []
    lowest = min(values)
    highest = max(values)
    span = highest - lowest
    if span <= 0:
        return [1.0 if highest > 0 else 0.0 for _ in values]
    return [(value - lowest) / span for value in values]


def _truncate_to_tokens(text: str, tokens: int) -> str:
    """Truncate text to approximately the requested token budget."""
    if tokens <= 0:
        return ""
    total = estimate_tokens(text)
    if total <= tokens:
        return text
    char_budget = max(1, int(len(text) * tokens / max(1, total)))
    return text[:char_budget].rstrip()


@dataclass(frozen=True)
class RankedHit:
    """A retrieval candidate with per-stage scores (higher is better)."""

    id: str
    text: str
    metadata: dict[str, Any]
    vector_score: float = 0.0
    bm25_score: float = 0.0
    score: float = 0.0
    collection: str = ""

    @property
    def title(self) -> str:
        """Return the document title when known."""
        return str(self.metadata.get("title") or "")


@dataclass(frozen=True)
class RetrievalOutcome:
    """Final retrieval result after reranking and context budgeting."""

    query: str
    hits: list[RankedHit]
    used_tokens: int
    budget_tokens: int
    collections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class HybridSearcher:
    """Stage-one recall: combines cosine similarity and BM25 keyword scores."""

    def __init__(self, store: VectorStore, *, alpha: float = 0.7, candidate_pool: int = 200) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError("alpha must be within [0, 1]")
        self._store = store
        self._alpha = alpha
        self._candidate_pool = max(1, candidate_pool)
        #(collection, revision, where) -> (ids, bm25)，按修订号失效
        self._index_cache: dict[tuple[str, int, str], tuple[tuple[str, ...], Any]] = {}
        self._index_lock = threading.Lock()

    @property
    def alpha(self) -> float:
        """Return the vector weight used when blending scores."""
        return self._alpha

    def search(
        self,
        query: str,
        *,
        collection: str,
        top_k: int = 20,
        where: Mapping[str, Any] | None = None,
    ) -> list[RankedHit]:
        """Return the top candidates by blended vector + BM25 score."""
        corpus = self._store.get_documents(collection, where=where)
        if not corpus:
            return []
        vector_hits = self._store.search(
            collection, query, top_k=self._candidate_pool, where=where
        )
        bm25, corpus_ids = self._bm25_index(collection, corpus, where)
        raw_bm25 = bm25.scores(tokenize_text(query))
        max_bm25 = max(raw_bm25) if raw_bm25 else 0.0
        bm25_by_id: dict[str, float] = {}
        if max_bm25 > 0:
            bm25_by_id = {
                doc_id: score / max_bm25 for doc_id, score in zip(corpus_ids, raw_bm25) if score > 0
            }

        candidates: dict[str, RankedHit] = {}
        for hit in vector_hits:
            candidates[hit.id] = RankedHit(
                id=hit.id,
                text=hit.text,
                metadata=hit.metadata,
                vector_score=hit.score,
                collection=collection,
            )
        #BM25 头部候选也可能不在向量结果里，取分数最高的若干条并入候选集
        bm25_top = sorted(bm25_by_id.items(), key=lambda item: item[1], reverse=True)[
            : self._candidate_pool
        ]
        docs_by_id = {doc.id: doc for doc in corpus}
        for doc_id, _score in bm25_top:
            if doc_id in candidates:
                continue
            doc = docs_by_id.get(doc_id)
            if doc is None:
                continue
            candidates[doc_id] = RankedHit(
                id=doc_id, text=doc.text, metadata=doc.metadata, collection=collection
            )

        ranked: list[RankedHit] = []
        for candidate in candidates.values():
            bm25_score = bm25_by_id.get(candidate.id, 0.0)
            vector_normalized = (candidate.vector_score + 1.0) / 2.0
            blended = self._alpha * vector_normalized + (1.0 - self._alpha) * bm25_score
            ranked.append(replace(candidate, bm25_score=bm25_score, score=blended))
        ranked.sort(key=lambda hit: hit.score, reverse=True)
        return ranked[: max(1, top_k)]

    def _bm25_index(
        self,
        collection: str,
        corpus: Sequence[StoredDocument],
        where: Mapping[str, Any] | None,
    ) -> tuple[Any, tuple[str, ...]]:
        """Return a cached BM25 index for the collection's current revision."""
        revision = self._store.revision(collection)
        where_key = stable_json_dumps(dict(where)) if where else ""
        cache_key = (collection, revision, where_key)
        with self._index_lock:
            cached = self._index_cache.get(cache_key)
        if cached is not None and len(cached[0]) == len(corpus):
            return cached[1], cached[0]
        corpus_ids = tuple(doc.id for doc in corpus)
        bm25 = _build_bm25([tokenize_text(doc.text) for doc in corpus])
        with self._index_lock:
            for key in [key for key in self._index_cache if key[0] == collection and key[2] == where_key]:
                self._index_cache.pop(key, None)
            self._index_cache[cache_key] = (corpus_ids, bm25)
        return bm25, corpus_ids


class Reranker:
    """Stage-two reranking with optional LLM scoring and metadata boosts."""

    def __init__(self, *, strategy: str = "auto", llm_reranker: LLMRerankerCallable | None = None) -> None:
        """Configure the reranker.

        Args:
            strategy: ``auto`` (LLM when a caller is injected) / ``heuristic`` / ``llm``.
            llm_reranker: Async callback scoring ``(query, texts) -> scores``.
        """
        normalized = (strategy or "auto").strip().lower()
        if normalized not in {"auto", "heuristic", "llm"}:
            raise ValueError(f"Unknown rerank strategy: {strategy!r}")
        self._strategy = normalized
        self._llm_reranker = llm_reranker

    @property
    def strategy(self) -> str:
        """Return the configured rerank strategy."""
        return self._strategy

    def rerank_sync(self, query: str, hits: Sequence[RankedHit], *, top_n: int = 5) -> list[RankedHit]:
        """Heuristic-only reranking (used by synchronous callers)."""
        boosted = [self._apply_metadata_boost(query, hit) for hit in hits]
        boosted.sort(key=lambda hit: hit.score, reverse=True)
        return boosted[: max(1, top_n)]

    async def rerank(self, query: str, hits: Sequence[RankedHit], *, top_n: int = 5) -> list[RankedHit]:
        """Rerank hits, using the injected LLM scorer when available."""
        if self._llm_reranker is not None and self._strategy in {"auto", "llm"} and hits:
            try:
                raw_scores = await self._llm_reranker(query, [hit.text for hit in hits])
                if len(raw_scores) == len(hits):
                    llm_scores = _normalize_scores([float(score) for score in raw_scores])
                    rescored = [
                        replace(hit, score=0.7 * llm_score + 0.3 * max(min(hit.score, 1.0), -1.0))
                        for hit, llm_score in zip(hits, llm_scores)
                    ]
                    boosted = [self._apply_metadata_boost(query, hit) for hit in rescored]
                    boosted.sort(key=lambda hit: hit.score, reverse=True)
                    return boosted[: max(1, top_n)]
            except Exception:
                #LLM 重排是尽力而为：失败时静默回退启发式
                pass
        return self.rerank_sync(query, hits, top_n=top_n)

    def _apply_metadata_boost(self, query: str, hit: RankedHit) -> RankedHit:
        """Boost hits whose title/keywords overlap the query tokens."""
        query_tokens = set(tokenize_text(query))
        if not query_tokens:
            return hit
        boost = 0.0
        if set(tokenize_text(hit.title)) & query_tokens:
            boost += 0.05
        keywords = hit.metadata.get("keywords")
        if isinstance(keywords, str) and keywords:
            overlap = len(set(tokenize_text(keywords)) & query_tokens)
            if overlap:
                boost += min(0.05, 0.01 * overlap)
        if not boost:
            return hit
        return replace(hit, score=hit.score + boost)


def pack_context_hits(
    hits: Sequence[RankedHit], *, budget_tokens: int
) -> tuple[list[RankedHit], int]:
    """Fill the context budget highest-score-first; truncate the overflow."""
    if budget_tokens <= 0:
        return [], 0
    packed: list[RankedHit] = []
    used = 0
    for hit in hits:
        remaining = budget_tokens - used
        if remaining <= 0:
            break
        hit_tokens = max(1, estimate_tokens(hit.text))
        if hit_tokens <= remaining:
            packed.append(hit)
            used += hit_tokens
            continue
        if remaining >= _MIN_TRUNCATED_TOKENS:
            truncated = _truncate_to_tokens(hit.text, remaining)
            if truncated.strip():
                packed.append(replace(hit, text=truncated))
                used += max(1, estimate_tokens(truncated))
        break
    return packed, used


class Retriever:
    """Two-stage retriever: hybrid recall then reranking within a token budget."""

    DEFAULT_CONTEXT_WINDOW = 16000

    def __init__(
        self,
        store: VectorStore,
        *,
        alpha: float = 0.7,
        candidate_pool: int = 200,
        vector_top_k: int = 20,
        final_top_n: int = 5,
        reranker: Reranker | None = None,
        context_budget_ratio: float = 0.25,
        context_window_tokens: int | None = None,
        max_context_tokens: int = 4000,
        default_collection: str = "knowledge",
    ) -> None:
        self._store = store
        self._searcher = HybridSearcher(store, alpha=alpha, candidate_pool=candidate_pool)
        self._reranker = reranker or Reranker()
        self._vector_top_k = max(1, vector_top_k)
        self._final_top_n = max(1, final_top_n)
        self._context_budget_ratio = min(max(context_budget_ratio, 0.0), 1.0)
        self._context_window_tokens = context_window_tokens or self.DEFAULT_CONTEXT_WINDOW
        self._max_context_tokens = max(1, max_context_tokens)
        self._default_collection = default_collection

    @property
    def store(self) -> VectorStore:
        """Return the underlying vector store."""
        return self._store

    @property
    def default_collection(self) -> str:
        """Return the collection used when the caller passes none."""
        return self._default_collection

    def context_budget(self) -> int:
        """Return the token budget for retrieved context."""
        budget = int(self._context_window_tokens * self._context_budget_ratio)
        return max(0, min(budget, self._max_context_tokens))

    async def retrieve(
        self,
        query: str,
        *,
        collection: str | None = None,
        collections: Sequence[str] | None = None,
        where: Mapping[str, Any] | None = None,
        top_k: int | None = None,
        top_n: int | None = None,
        budget_tokens: int | None = None,
    ) -> RetrievalOutcome:
        """Run the two-stage retrieval asynchronously."""
        targets = self._target_collections(collection, collections)
        hits, notes = await asyncio.to_thread(self._recall, targets, query, where, top_k)
        reranked = await self._reranker.rerank(query, hits, top_n=top_n or self._final_top_n)
        budget = budget_tokens if budget_tokens is not None else self.context_budget()
        packed, used = pack_context_hits(reranked, budget_tokens=budget)
        return RetrievalOutcome(
            query=query,
            hits=packed,
            used_tokens=used,
            budget_tokens=budget,
            collections=targets,
            notes=notes,
        )

    def retrieve_sync(
        self,
        query: str,
        *,
        collection: str | None = None,
        collections: Sequence[str] | None = None,
        where: Mapping[str, Any] | None = None,
        top_k: int | None = None,
        top_n: int | None = None,
        budget_tokens: int | None = None,
    ) -> RetrievalOutcome:
        """Synchronous retrieval with heuristic reranking (LLM rerank needs async)."""
        targets = self._target_collections(collection, collections)
        hits, notes = self._recall(targets, query, where, top_k)
        reranked = self._reranker.rerank_sync(query, hits, top_n=top_n or self._final_top_n)
        budget = budget_tokens if budget_tokens is not None else self.context_budget()
        packed, used = pack_context_hits(reranked, budget_tokens=budget)
        return RetrievalOutcome(
            query=query,
            hits=packed,
            used_tokens=used,
            budget_tokens=budget,
            collections=targets,
            notes=notes,
        )

    def _target_collections(
        self,
        collection: str | None,
        collections: Sequence[str] | None,
    ) -> list[str]:
        if collections:
            targets = [str(name).strip() for name in collections if str(name).strip()]
        elif collection and str(collection).strip():
            targets = [str(collection).strip()]
        else:
            targets = [self._default_collection]
        deduped = list(dict.fromkeys(targets))
        return deduped or [self._default_collection]

    def _recall(
        self,
        targets: Sequence[str],
        query: str,
        where: Mapping[str, Any] | None,
        top_k: int | None,
    ) -> tuple[list[RankedHit], list[str]]:
        limit = max(1, top_k or self._vector_top_k)
        hits: list[RankedHit] = []
        notes: list[str] = []
        for collection_name in targets:
            try:
                collection_count = self._store.count(collection_name)
            except Exception as exc:  # 单个集合失败不应终止整次检索
                notes.append(f"{collection_name}: {exc}")
                continue
            if collection_count == 0:
                notes.append(f"{collection_name}: no documents indexed yet")
                continue
            hits.extend(
                self._searcher.search(query, collection=collection_name, top_k=limit, where=where)
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits, notes


def format_retrieval_for_tool(outcome: RetrievalOutcome, *, max_chars: int = 4000) -> str:
    """Render retrieval results for tool output."""
    if not outcome.hits:
        if outcome.notes:
            return "No matches found. " + "; ".join(outcome.notes)
        return "No matches found."
    lines = [f"Found {len(outcome.hits)} relevant document(s) for '{outcome.query}':"]
    for index, hit in enumerate(outcome.hits, start=1):
        header = f"[{index}] id={hit.id} score={hit.score:.3f}"
        if hit.title:
            header += f" title={hit.title}"
        if hit.collection:
            header += f" collection={hit.collection}"
        lines.append(header)
        lines.append(hit.text.strip())
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n… (truncated)"
    return text


def format_retrieval_for_prompt(outcome: RetrievalOutcome, *, max_chars: int = 12000) -> str:
    """Render retrieval results for system-prompt injection (compact form)."""
    if not outcome.hits:
        return ""
    lines = [
        (
            f"Retrieved {len(outcome.hits)} item(s) from the local knowledge base "
            f"(query: {outcome.query}):"
        ),
        "",
    ]
    for index, hit in enumerate(outcome.hits, start=1):
        heading = hit.title or hit.id
        source = hit.metadata.get("source") or hit.collection or "local"
        lines.append(f"{index}. **{heading}** (relevance {hit.score:.2f}, source: {source})")
        lines.append(f"   {hit.text.strip()}")
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n… (truncated)"
    return text
