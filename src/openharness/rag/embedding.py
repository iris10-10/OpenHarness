"""Text embedding service with multi-provider strategy and graceful degradation.

Providers are tried in order until one succeeds:

1. :class:`OpenAIEmbeddingProvider` - remote API (``text-embedding-3-small`` by default).
2. :class:`LocalEmbeddingProvider` - sentence-transformers (``BAAI/bge-m3`` by default).
3. :class:`HashEmbeddingProvider` - deterministic offline fallback (no network, no extras).

The ``auto`` strategy prefers the online API when credentials are available,
falls back to the local model, and finally degrades to the offline hash
embedder so retrieval keeps working even with no provider configured.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from openharness.config.paths import get_data_dir
from openharness.rag.utils import content_hash, tokenize_text
from openharness.utils.fs import atomic_write_text

_CACHE_FILE_NAME = "embedding_cache.json"


class EmbeddingProvider(ABC):
    """Base class for embedding providers."""

    name: str = "provider"
    model: str = "unknown"
    network: bool = False

    @property
    def identifier(self) -> str:
        """Return the provider/model tag recorded on collections and caches."""
        return f"{self.name}:{self.model}"

    @abstractmethod
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Remote embeddings through any OpenAI-compatible endpoint."""

    name = "openai"
    network = True

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        api_key: str,
        base_url: str = "",
        timeout: float = 30.0,
        batch_size: int = 64,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAIEmbeddingProvider requires an API key")
        self.model = model
        self._api_key = api_key.strip()
        self._base_url = base_url.strip()
        self._timeout = timeout
        self._batch_size = max(1, batch_size)
        self._client: Any = None

    def _get_client(self) -> Any:
        #延迟导入 openai SDK，只有真正调用时才创建客户端
        if self._client is None:
            openai = importlib.import_module("openai")
            kwargs: dict[str, Any] = {"api_key": self._api_key, "timeout": self._timeout}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        client = self._get_client()
        vectors: list[list[float]] = []
        items = list(texts)
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            response = client.embeddings.create(model=self.model, input=batch)
            vectors.extend([float(value) for value in item.embedding] for item in response.data)
        return vectors


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local sentence-transformers embeddings (requires the ``rag-local`` extra)."""

    name = "local"

    @staticmethod
    def is_available() -> bool:
        """Return whether sentence-transformers is importable."""
        return importlib.util.find_spec("sentence_transformers") is not None

    def __init__(self, *, model: str = "BAAI/bge-m3") -> None:
        self.model = model
        self._encoder: Any = None

    def _get_encoder(self) -> Any:
        #延迟加载：模型体积大，只有真正调用时才初始化
        if self._encoder is None:
            module = importlib.import_module("sentence_transformers")
            self._encoder = module.SentenceTransformer(self.model)
        return self._encoder

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        encoder = self._get_encoder()
        encoded = encoder.encode(list(texts), normalize_embeddings=True)
        return [[float(value) for value in vector] for vector in encoded]


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline embeddings used for degraded mode and tests.

    Token hashing plus L2 normalization yields a bag-of-words cosine space:
    texts sharing vocabulary land close together, which keeps lexical recall
    working when no external provider is reachable.
    """

    name = "hash"

    def __init__(self, *, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.model = f"blake2b-{dimension}"
        self._dimension = dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dimension
            for token in tokenize_text(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                slot = int.from_bytes(digest[:4], "big") % self._dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[slot] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            if norm > 0:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


class EmbeddingService:
    """Embed texts through the first working provider, with hash-keyed caching.

    Requests are served from a content-hash cache when possible; provider
    failures downgrade to the next provider in the chain and stay downgraded
    for the lifetime of the service to avoid hammering a failing endpoint.
    """

    def __init__(
        self,
        *,
        provider: str = "auto",
        openai_model: str = "text-embedding-3-small",
        openai_api_key: str = "",
        openai_base_url: str = "",
        local_model: str = "BAAI/bge-m3",
        cache_enabled: bool = True,
        cache_ttl_days: int = 30,
        cache_dir: str | Path | None = None,
        providers: Sequence[EmbeddingProvider] | None = None,
    ) -> None:
        """Configure the service.

        Args:
            provider: ``auto`` / ``openai`` / ``local`` / ``hash``.
            providers: Optional explicit provider chain (mainly for tests and
                custom integrations). When given, it replaces the built chain.
        """
        self._requested_provider = (provider or "auto").strip().lower() or "auto"
        self._openai_model = openai_model
        self._openai_api_key = (
            openai_api_key.strip()
            or os.environ.get("OPENHARNESS_EMBEDDING_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        self._openai_base_url = openai_base_url
        self._local_model = local_model
        self._cache_enabled = cache_enabled
        self._cache_ttl_seconds = cache_ttl_days * 86400 if cache_ttl_days > 0 else 0
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_loaded = False
        self._cache_lock = threading.Lock()
        self._downgrade_notes: list[str] = []
        if providers is not None:
            self._providers = list(providers)
            if not self._providers:
                raise ValueError("providers must not be empty")
        else:
            self._providers = self._build_provider_chain()
        self._active_index = 0

    # ------------------------------------------------------------------状态

    @property
    def active_provider(self) -> EmbeddingProvider:
        """Return the provider currently serving requests."""
        return self._providers[self._active_index]

    @property
    def provider_name(self) -> str:
        """Return the active provider name (``openai`` / ``local`` / ``hash``)."""
        return self.active_provider.name

    @property
    def model(self) -> str:
        """Return the active provider/model tag (``openai:text-embedding-3-small``)."""
        return self.active_provider.identifier

    @property
    def provider_chain(self) -> list[str]:
        """Return the full fallback chain as identifier strings."""
        return [provider.identifier for provider in self._providers]

    @property
    def downgrade_notes(self) -> list[str]:
        """Return notes recorded when providers were skipped or downgraded."""
        return list(self._downgrade_notes)

    def _build_provider_chain(self) -> list[EmbeddingProvider]:
        """Assemble the provider chain for the requested strategy."""
        requested = self._requested_provider
        chain: list[EmbeddingProvider] = []
        if requested in {"auto", "openai"}:
            if self._openai_api_key:
                chain.append(
                    OpenAIEmbeddingProvider(
                        model=self._openai_model,
                        api_key=self._openai_api_key,
                        base_url=self._openai_base_url,
                    )
                )
            elif requested == "openai":
                self._downgrade_notes.append(
                    "openai embedding provider requested but no API key is configured; "
                    "using the fallback embedder"
                )
        if requested in {"auto", "local"}:
            if LocalEmbeddingProvider.is_available():
                chain.append(LocalEmbeddingProvider(model=self._local_model))
            elif requested == "local":
                self._downgrade_notes.append(
                    "local embedding provider requested but sentence-transformers is not "
                    "installed (uv sync --extra rag-local); using the fallback embedder"
                )
        if requested == "hash":
            return [HashEmbeddingProvider()]
        #离线兜底：hash 提供者不依赖网络与额外依赖，保证服务始终可用
        chain.append(HashEmbeddingProvider())
        return chain

    # ------------------------------------------------------------------嵌入

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts, serving cache hits where possible."""
        items = list(texts)
        if not items:
            return []
        vectors: list[list[float] | None] = [None] * len(items)
        pending: list[tuple[int, str]] = []
        for index, text in enumerate(items):
            cached = self._cache_lookup(text)
            if cached is not None:
                vectors[index] = cached
            else:
                pending.append((index, text))
        if pending:
            fresh = self._embed_with_fallback([text for _, text in pending])
            for (index, text), vector in zip(pending, fresh):
                vectors[index] = vector
                self._cache_store(text, vector)
            self._save_cache()
        return [vector if vector is not None else [] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []

    def _embed_with_fallback(self, texts: list[str]) -> list[list[float]]:
        """Embed through the chain, downgrading past failing providers."""
        last_error: Exception | None = None
        while self._active_index < len(self._providers):
            provider = self.active_provider
            try:
                return provider.embed_texts(texts)
            except Exception as exc:  # provider 失败后自动降级到下一个候选
                last_error = exc
                failed = provider.identifier
                self._active_index += 1
                if self._active_index < len(self._providers):
                    self._downgrade_notes.append(
                        f"{failed} failed ({exc.__class__.__name__}: {exc}); "
                        f"downgraded to {self.active_provider.identifier}"
                    )
                else:
                    self._downgrade_notes.append(f"{failed} failed ({exc.__class__.__name__}: {exc})")
        raise RuntimeError("All embedding providers failed") from last_error

    # ------------------------------------------------------------------缓存

    def _cache_key(self, text: str) -> str:
        #缓存键包含 provider/model 标签，避免切换 provider 后混用不同向量空间
        return f"{self.model}::{content_hash(text, length=32)}"

    def _cache_path(self) -> Path | None:
        if not self._cache_enabled:
            return None
        directory = self._cache_dir if self._cache_dir is not None else get_data_dir() / "rag_cache"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / _CACHE_FILE_NAME

    def _ensure_cache_loaded(self) -> None:
        if not self._cache_enabled or self._cache_loaded:
            return
        self._cache_loaded = True
        path = self._cache_path()
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return
        now = time.time()
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            vector = entry.get("vector")
            timestamp = entry.get("ts", 0)
            if not isinstance(vector, list) or not isinstance(timestamp, (int, float)):
                continue
            if self._cache_ttl_seconds and now - float(timestamp) > self._cache_ttl_seconds:
                continue
            self._cache[key] = {"vector": vector, "ts": float(timestamp)}

    def _cache_lookup(self, text: str) -> list[float] | None:
        if not self._cache_enabled:
            return None
        with self._cache_lock:
            self._ensure_cache_loaded()
            entry = self._cache.get(self._cache_key(text))
        if entry is None:
            return None
        return [float(value) for value in entry["vector"]]

    def _cache_store(self, text: str, vector: list[float]) -> None:
        if not self._cache_enabled:
            return
        with self._cache_lock:
            self._ensure_cache_loaded()
            self._cache[self._cache_key(text)] = {"vector": [float(value) for value in vector], "ts": time.time()}

    def _save_cache(self) -> None:
        if not self._cache_enabled:
            return
        with self._cache_lock:
            path = self._cache_path()
            if path is None:
                return
            now = time.time()
            if self._cache_ttl_seconds:
                self._cache = {
                    key: entry
                    for key, entry in self._cache.items()
                    if now - float(entry.get("ts", 0)) <= self._cache_ttl_seconds
                }
            try:
                atomic_write_text(path, json.dumps({"entries": self._cache}, ensure_ascii=False))
            except OSError:
                pass
