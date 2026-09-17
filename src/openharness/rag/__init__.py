"""RAG knowledge retrieval (vector store, embeddings, ingestion, ranking).

Heavy third-party dependencies (ChromaDB, sentence-transformers, rank-bm25,
pypdf, beautifulsoup4) are imported lazily inside the modules and backends so
that ``import openharness.rag`` stays lightweight even without the ``rag``
extra installed. Concrete classes are re-exported lazily via PEP 562::

    from openharness.rag import VectorStore, DocumentIngestor, Retriever
"""

from __future__ import annotations

import importlib
import os
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openharness.config.settings import Settings
    from openharness.rag.ingestion import DocumentIngestor
    from openharness.rag.retriever import Retriever
    from openharness.rag.vectorstore import VectorStore

#懒加载导出表：名称 -> 所在模块，避免包导入即拉起重依赖
_EXPORTS: dict[str, str] = {
    # vectorstore
    "CollectionManager": "openharness.rag.vectorstore",
    "DEFAULT_COLLECTIONS": "openharness.rag.vectorstore",
    "EmbeddingMismatchError": "openharness.rag.vectorstore",
    "SearchHit": "openharness.rag.vectorstore",
    "StoredDocument": "openharness.rag.vectorstore",
    "VectorStore": "openharness.rag.vectorstore",
    # embedding
    "EmbeddingProvider": "openharness.rag.embedding",
    "EmbeddingService": "openharness.rag.embedding",
    "HashEmbeddingProvider": "openharness.rag.embedding",
    "LocalEmbeddingProvider": "openharness.rag.embedding",
    "OpenAIEmbeddingProvider": "openharness.rag.embedding",
    # ingestion
    "DocumentIngestor": "openharness.rag.ingestion",
    "IngestionReport": "openharness.rag.ingestion",
    "MetadataEnricher": "openharness.rag.ingestion",
    "SUPPORTED_EXTENSIONS": "openharness.rag.ingestion",
    "TextChunk": "openharness.rag.ingestion",
    "TextChunker": "openharness.rag.ingestion",
    "load_document_text": "openharness.rag.ingestion",
    # retriever
    "HybridSearcher": "openharness.rag.retriever",
    "RankedHit": "openharness.rag.retriever",
    "Reranker": "openharness.rag.retriever",
    "RetrievalOutcome": "openharness.rag.retriever",
    "Retriever": "openharness.rag.retriever",
    "format_retrieval_for_prompt": "openharness.rag.retriever",
    "format_retrieval_for_tool": "openharness.rag.retriever",
    "pack_context_hits": "openharness.rag.retriever",
}

__all__ = [
    *_EXPORTS,
    "build_ingestor_from_settings",
    "build_retriever_from_settings",
    "reset_rag_stack_cache",
]


def __getattr__(name: str) -> Any:
    """Resolve lazily exported symbols (PEP 562)."""
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module 'openharness.rag' has no attribute {name!r}")
    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


#每个配置指纹只构建一个 Retriever/VectorStore，避免重复打开 Chroma 客户端
_STACK_CACHE: dict[tuple[Any, ...], Retriever] = {}
_STACK_LOCK = threading.Lock()


def reset_rag_stack_cache() -> None:
    """Clear the cached retriever stack (tests and post-config-change use)."""
    with _STACK_LOCK:
        _STACK_CACHE.clear()


def _resolve_embedding_api_key(settings: Settings) -> str:
    """Resolve the embedding API key from RAG settings, env or the provider."""
    explicit = settings.rag.embedding.api_key.strip()
    if explicit:
        return explicit
    env_key = os.environ.get("OPENHARNESS_EMBEDDING_API_KEY", "").strip()
    if env_key:
        return env_key
    if settings.api_format == "openai" and settings.api_key:
        return settings.api_key
    return ""


def build_retriever_from_settings(
    settings: Settings,
    *,
    force_backend: str | None = None,
) -> Retriever:
    """Assemble an embedding service, vector store and retriever from settings."""
    from openharness.rag.embedding import EmbeddingService
    from openharness.rag.retriever import Retriever
    from openharness.rag.vectorstore import VectorStore

    rag_settings = settings.rag
    embedding_settings = rag_settings.embedding
    retrieval = rag_settings.retrieval
    cache_key = (
        rag_settings.persist_directory,
        rag_settings.default_collection,
        embedding_settings.provider,
        embedding_settings.openai_model,
        embedding_settings.local_model,
        embedding_settings.api_key,
        retrieval.hybrid_alpha,
        retrieval.vector_top_k,
        retrieval.final_top_n,
        retrieval.candidate_pool,
        retrieval.context_budget_ratio,
        retrieval.max_context_tokens,
        settings.context_window_tokens,
        force_backend or "auto",
    )
    with _STACK_LOCK:
        cached = _STACK_CACHE.get(cache_key)
        if cached is not None:
            return cached
    service = EmbeddingService(
        provider=embedding_settings.provider,
        openai_model=embedding_settings.openai_model,
        openai_api_key=_resolve_embedding_api_key(settings),
        openai_base_url=embedding_settings.base_url,
        local_model=embedding_settings.local_model,
        cache_enabled=embedding_settings.cache_enabled,
        cache_ttl_days=embedding_settings.cache_ttl_days,
    )
    store = VectorStore(
        persist_directory=rag_settings.persist_directory or None,
        backend=force_backend or "auto",
        embedding_service=service,
    )
    retriever = Retriever(
        store,
        alpha=retrieval.hybrid_alpha,
        candidate_pool=retrieval.candidate_pool,
        vector_top_k=retrieval.vector_top_k,
        final_top_n=retrieval.final_top_n,
        context_budget_ratio=retrieval.context_budget_ratio,
        context_window_tokens=settings.context_window_tokens,
        max_context_tokens=retrieval.max_context_tokens,
        default_collection=rag_settings.default_collection,
    )
    with _STACK_LOCK:
        _STACK_CACHE[cache_key] = retriever
    return retriever


def build_ingestor_from_settings(settings: Settings, store: VectorStore) -> DocumentIngestor:
    """Build a document ingestor wired to the ``rag.chunking`` settings."""
    from openharness.rag.ingestion import DocumentIngestor, TextChunker

    chunking = settings.rag.chunking
    return DocumentIngestor(
        store,
        chunker=TextChunker(
            max_tokens=chunking.chunk_tokens,
            overlap_tokens=chunking.overlap_tokens,
            strategy=chunking.strategy,
        ),
        max_chunks_per_document=settings.rag.max_chunks_per_document,
    )
