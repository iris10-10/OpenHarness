"""Vector store abstraction over ChromaDB with a built-in fallback backend.

``VectorStore`` picks its backend automatically:

- ``chroma``: ChromaDB local persistence (requires the ``rag`` extra), stored
  under ``~/.openharness/chromadb/`` by default.
- ``simple``: a dependency-free JSON-persisted backend used when ChromaDB is
  unavailable (degraded mode) and by lightweight tests.

Both backends expose the same collection lifecycle, CRUD and metadata-filter
semantics. Collections remember the embedding model they were indexed with so
that querying with a different embedder fails loudly instead of silently
mixing incomparable vector spaces.
"""

from __future__ import annotations

import importlib
import json
import math
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.config.paths import get_config_dir
from openharness.rag.embedding import EmbeddingService
from openharness.rag.utils import content_hash
from openharness.utils.fs import atomic_write_text

DEFAULT_COLLECTIONS: tuple[str, ...] = ("jobs", "resumes", "interview", "companies", "knowledge")

#向量库元数据只接受标量，列表/字典需要 JSON 序列化后存储
_ALLOWED_METADATA_TYPES = (str, int, float, bool)

_REQUIRED_CHROMA_HINT = (
    "chromadb is required for the ChromaDB backend. Install it with 'uv sync --extra rag'."
)


class EmbeddingMismatchError(ValueError):
    """Raised when a collection was indexed with a different embedding model."""

    def __init__(self, collection: str, stored: str, current: str) -> None:
        super().__init__(
            f"Collection '{collection}' was indexed with embedding model '{stored}', "
            f"but the active embedder is '{current}'. Re-ingest the collection or "
            "configure the matching embedding provider before querying."
        )
        self.collection = collection
        self.stored = stored
        self.current = current


@dataclass(frozen=True)
class SearchHit:
    """A vector-search match; ``score`` is cosine similarity in [-1, 1]."""

    id: str
    text: str
    metadata: dict[str, Any]
    score: float


@dataclass(frozen=True)
class StoredDocument:
    """A stored document chunk as returned by direct lookups."""

    id: str
    text: str
    metadata: dict[str, Any]


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Convert metadata to the scalar subset vector backends can persist.

    Lists and dicts are JSON-encoded (``tags`` becomes ``'["a","b"]'``) and
    ``None`` values are dropped, matching ChromaDB's metadata constraints.
    """
    if not metadata:
        return {}
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, _ALLOWED_METADATA_TYPES):
            clean[str(key)] = value
        elif isinstance(value, (list, tuple, set)):
            clean[str(key)] = json.dumps(list(value), ensure_ascii=False)
        else:
            clean[str(key)] = json.dumps(str(value), ensure_ascii=False)
    return clean


def _match_operators(actual: Any, operators: Mapping[str, Any]) -> bool:
    """Evaluate a Chroma-style operator mapping against a metadata value."""
    for operator, expected in operators.items():
        if operator == "$eq":
            if actual != expected:
                return False
        elif operator == "$ne":
            if actual == expected:
                return False
        elif operator in {"$gt", "$gte", "$lt", "$lte"}:
            if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                return False
            if not isinstance(expected, (int, float)) or isinstance(expected, bool):
                return False
            if operator == "$gt" and not actual > expected:
                return False
            if operator == "$gte" and not actual >= expected:
                return False
            if operator == "$lt" and not actual < expected:
                return False
            if operator == "$lte" and not actual <= expected:
                return False
        elif operator == "$in":
            if not isinstance(expected, (list, tuple)) or actual not in expected:
                return False
        elif operator == "$nin":
            if isinstance(expected, (list, tuple)) and actual in expected:
                return False
        else:
            return False
    return True


def _match_where(metadata: Mapping[str, Any], where: Mapping[str, Any] | None) -> bool:
    """Return whether a document metadata mapping satisfies a where filter."""
    if not where:
        return True
    for key, expected in where.items():
        if str(key).startswith("$"):
            return False
        actual = metadata.get(key)
        if isinstance(expected, Mapping):
            if not _match_operators(actual, expected):
                return False
        elif actual != expected:
            return False
    return True


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors; returns 0.0 on shape mismatch."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class _VectorBackend(ABC):
    """Persistence backend contract shared by ChromaDB and the fallback."""

    name: str = "backend"

    @abstractmethod
    def ensure_collection(self, name: str, embedding_model: str) -> None:
        """Create the collection when missing; tag it with the embedding model."""

    @abstractmethod
    def set_embedding_model(self, name: str, embedding_model: str) -> bool:
        """Record the embedding model on an existing collection."""

    @abstractmethod
    def get_embedding_model(self, name: str) -> str | None:
        """Return the embedding model tag recorded for a collection."""

    @abstractmethod
    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        texts: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Insert or replace documents by id."""

    @abstractmethod
    def query(
        self,
        collection: str,
        query_embedding: Sequence[float],
        top_k: int,
        where: Mapping[str, Any] | None,
    ) -> list[SearchHit]:
        """Return the best matches ranked by descending similarity."""

    @abstractmethod
    def get_documents(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[StoredDocument]:
        """Return stored documents, optionally filtered."""

    @abstractmethod
    def delete(self, collection: str, ids: Sequence[str] | None, where: Mapping[str, Any] | None) -> int:
        """Delete documents by id or metadata filter; returns deleted count."""

    @abstractmethod
    def count(self, collection: str) -> int:
        """Return the number of stored documents."""

    @abstractmethod
    def list_collections(self) -> list[str]:
        """Return all collection names."""

    @abstractmethod
    def drop_collection(self, collection: str) -> bool:
        """Delete a whole collection; returns whether it existed."""


class _SimpleBackend(_VectorBackend):
    """Dependency-free JSON-persisted backend used when ChromaDB is missing."""

    name = "simple"

    def __init__(self, persist_directory: Path) -> None:
        self._path = Path(persist_directory) / "simple_store.json"
        self._lock = threading.RLock()
        #collection -> {"embedding_model": str|None, "documents": {id: {...}}}
        self._collections: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        collections = raw.get("collections") if isinstance(raw, dict) else None
        if not isinstance(collections, dict):
            return
        for name, bucket in collections.items():
            if not isinstance(bucket, dict):
                continue
            documents = bucket.get("documents")
            self._collections[str(name)] = {
                "embedding_model": bucket.get("embedding_model"),
                "documents": documents if isinstance(documents, dict) else {},
            }

    def _save(self) -> None:
        payload = {"version": 1, "collections": self._collections}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(payload, ensure_ascii=False))

    def _bucket(self, collection: str) -> dict[str, Any] | None:
        return self._collections.get(collection)

    # ------------------------------------------------------------ 集合管理

    def ensure_collection(self, name: str, embedding_model: str) -> None:
        with self._lock:
            bucket = self._collections.get(name)
            if bucket is None:
                self._collections[name] = {"embedding_model": embedding_model, "documents": {}}
                self._save()
            elif not bucket.get("embedding_model"):
                bucket["embedding_model"] = embedding_model
                self._save()

    def set_embedding_model(self, name: str, embedding_model: str) -> bool:
        with self._lock:
            bucket = self._collections.get(name)
            if bucket is None:
                return False
            bucket["embedding_model"] = embedding_model
            self._save()
            return True

    def get_embedding_model(self, name: str) -> str | None:
        with self._lock:
            bucket = self._bucket(name)
        if bucket is None:
            return None
        value = bucket.get("embedding_model")
        return str(value) if value else None

    def list_collections(self) -> list[str]:
        with self._lock:
            return sorted(self._collections)

    def drop_collection(self, collection: str) -> bool:
        with self._lock:
            if collection not in self._collections:
                return False
            del self._collections[collection]
            self._save()
            return True

    # ------------------------------------------------------------ 文档操作

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        texts: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        with self._lock:
            bucket = self._collections.setdefault(
                collection, {"embedding_model": None, "documents": {}}
            )
            documents = bucket["documents"]
            for doc_id, text, metadata, embedding in zip(ids, texts, metadatas, embeddings):
                documents[doc_id] = {
                    "text": text,
                    "metadata": dict(metadata),
                    "embedding": [float(value) for value in embedding],
                }
            self._save()

    def query(
        self,
        collection: str,
        query_embedding: Sequence[float],
        top_k: int,
        where: Mapping[str, Any] | None,
    ) -> list[SearchHit]:
        with self._lock:
            bucket = self._bucket(collection)
            if bucket is None:
                return []
            hits: list[SearchHit] = []
            for doc_id, entry in bucket["documents"].items():
                metadata = entry.get("metadata") or {}
                if not _match_where(metadata, where):
                    continue
                score = _cosine_similarity(query_embedding, entry.get("embedding") or [])
                hits.append(
                    SearchHit(
                        id=str(doc_id),
                        text=str(entry.get("text", "")),
                        metadata=dict(metadata),
                        score=float(score),
                    )
                )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: max(1, top_k)]

    def get_documents(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[StoredDocument]:
        with self._lock:
            bucket = self._bucket(collection)
            if bucket is None:
                return []
            documents: list[StoredDocument] = []
            for doc_id, entry in bucket["documents"].items():
                metadata = entry.get("metadata") or {}
                if not _match_where(metadata, where):
                    continue
                documents.append(
                    StoredDocument(
                        id=str(doc_id),
                        text=str(entry.get("text", "")),
                        metadata=dict(metadata),
                    )
                )
                if limit is not None and len(documents) >= limit:
                    break
            return documents

    def delete(self, collection: str, ids: Sequence[str] | None, where: Mapping[str, Any] | None) -> int:
        with self._lock:
            bucket = self._bucket(collection)
            if bucket is None:
                return 0
            documents = bucket["documents"]
            if ids is not None:
                targets = [str(doc_id) for doc_id in ids if str(doc_id) in documents]
            elif where is not None:
                targets = [
                    str(doc_id)
                    for doc_id, entry in documents.items()
                    if _match_where(entry.get("metadata") or {}, where)
                ]
            else:
                return 0
            for doc_id in targets:
                documents.pop(doc_id, None)
            if targets:
                self._save()
            return len(targets)

    def count(self, collection: str) -> int:
        with self._lock:
            bucket = self._bucket(collection)
            return len(bucket["documents"]) if bucket is not None else 0


class _ChromaBackend(_VectorBackend):
    """ChromaDB persistent backend (requires the optional ``rag`` extra)."""

    name = "chroma"

    def __init__(self, persist_directory: Path) -> None:
        try:
            chromadb = importlib.import_module("chromadb")
        except ImportError as exc:
            raise ImportError(_REQUIRED_CHROMA_HINT) from exc
        Path(persist_directory).mkdir(parents=True, exist_ok=True)
        #关闭匿名遥测；两个同路径客户端必须使用相同配置，否则 Chroma 会拒绝
        try:
            config = importlib.import_module("chromadb.config")
            self._client: Any = chromadb.PersistentClient(
                path=str(persist_directory),
                settings=config.Settings(anonymized_telemetry=False),
            )
        except Exception:
            self._client = chromadb.PersistentClient(path=str(persist_directory))

    # ------------------------------------------------------------ 集合管理

    def _get_collection(self, name: str) -> Any | None:
        try:
            return self._client.get_collection(name=name)
        except Exception:
            return None

    def _create_collection(self, name: str, embedding_model: str | None) -> Any:
        metadata: dict[str, Any] = {"hnsw:space": "cosine"}
        if embedding_model:
            metadata["embedding_model"] = embedding_model
        try:
            return self._client.create_collection(name=name, metadata=metadata)
        except Exception:
            #部分 Chroma 版本校验元数据键更严格，去掉距离配置再试一次
            fallback = {"embedding_model": embedding_model} if embedding_model else None
            try:
                return self._client.create_collection(name=name, metadata=fallback)
            except Exception:
                return self._client.create_collection(name=name)

    def ensure_collection(self, name: str, embedding_model: str) -> None:
        collection = self._get_collection(name)
        if collection is None:
            self._create_collection(name, embedding_model)
            return
        metadata = dict(getattr(collection, "metadata", None) or {})
        if not metadata.get("embedding_model"):
            self.set_embedding_model(name, embedding_model)

    def set_embedding_model(self, name: str, embedding_model: str) -> bool:
        collection = self._get_collection(name)
        if collection is None:
            return False
        existing = dict(getattr(collection, "metadata", None) or {})
        #modify 会整体替换元数据；hnsw:* 键不允许通过 modify 再次写入
        metadata = {key: value for key, value in existing.items() if not str(key).startswith("hnsw:")}
        metadata["embedding_model"] = embedding_model
        try:
            collection.modify(metadata=metadata)
            return True
        except Exception:
            try:
                collection.modify(metadata={"embedding_model": embedding_model})
                return True
            except Exception:
                return False

    def get_embedding_model(self, name: str) -> str | None:
        collection = self._get_collection(name)
        if collection is None:
            return None
        metadata = getattr(collection, "metadata", None) or {}
        value = metadata.get("embedding_model")
        return str(value) if value else None

    def list_collections(self) -> list[str]:
        names: list[str] = []
        for item in self._client.list_collections():
            name = getattr(item, "name", item)
            names.append(str(name))
        return sorted(names)

    def drop_collection(self, collection: str) -> bool:
        try:
            self._client.delete_collection(name=collection)
            return True
        except Exception:
            return False

    def _collection(self, name: str) -> Any:
        collection = self._get_collection(name)
        if collection is None:
            collection = self._create_collection(name, None)
        return collection

    def _space(self, collection: Any) -> str:
        """Return the collection distance space (cosine / l2 / ip)."""
        configuration = getattr(collection, "configuration", None)
        if isinstance(configuration, dict):
            hnsw = configuration.get("hnsw")
            if isinstance(hnsw, dict) and isinstance(hnsw.get("space"), str):
                return str(hnsw["space"]).lower()
        metadata = getattr(collection, "metadata", None) or {}
        space = metadata.get("hnsw:space")
        if isinstance(space, str) and space:
            return space.lower()
        return "l2"

    @staticmethod
    def _distance_to_score(distance: float, space: str) -> float:
        """Convert a Chroma distance to a similarity score (higher is better)."""
        if space == "l2":
            #向量已归一化时，l2 距离 d 与余弦相似度满足 cos ≈ 1 - d / 2
            return 1.0 - float(distance) / 2.0
        return 1.0 - float(distance)

    # ------------------------------------------------------------ 文档操作

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        texts: Sequence[str],
        metadatas: Sequence[Mapping[str, Any]],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        target = self._collection(collection)
        target.upsert(
            ids=list(ids),
            documents=list(texts),
            #ChromaDB 拒绝空 dict 元数据，统一转为 None
            metadatas=[dict(metadata) or None for metadata in metadatas],
            embeddings=[list(embedding) for embedding in embeddings],
        )

    def query(
        self,
        collection: str,
        query_embedding: Sequence[float],
        top_k: int,
        where: Mapping[str, Any] | None,
    ) -> list[SearchHit]:
        target = self._collection(collection)
        total = int(target.count())
        if total == 0:
            return []
        kwargs: dict[str, Any] = {
            "query_embeddings": [list(query_embedding)],
            "n_results": min(max(1, top_k), total),
        }
        if where:
            kwargs["where"] = dict(where)
        result = target.query(**kwargs)
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        space = self._space(target)
        hits: list[SearchHit] = []
        for index, doc_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else None
            distance = distances[index] if index < len(distances) else 1.0
            hits.append(
                SearchHit(
                    id=str(doc_id),
                    text=str(documents[index]) if index < len(documents) else "",
                    metadata=dict(metadata or {}),
                    score=self._distance_to_score(distance, space),
                )
            )
        return hits

    def get_documents(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[StoredDocument]:
        target = self._collection(collection)
        kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
        if where:
            kwargs["where"] = dict(where)
        if limit is not None:
            kwargs["limit"] = max(1, limit)
        result = target.get(**kwargs)
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        stored: list[StoredDocument] = []
        for index, doc_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else None
            stored.append(
                StoredDocument(
                    id=str(doc_id),
                    text=str(documents[index]) if index < len(documents) and documents[index] else "",
                    metadata=dict(metadata or {}),
                )
            )
        return stored

    def delete(self, collection: str, ids: Sequence[str] | None, where: Mapping[str, Any] | None) -> int:
        target = self._collection(collection)
        before = int(target.count())
        if ids is not None:
            if not ids:
                return 0
            target.delete(ids=[str(doc_id) for doc_id in ids])
        elif where is not None:
            target.delete(where=dict(where))
        else:
            return 0
        return max(before - int(target.count()), 0)

    def count(self, collection: str) -> int:
        return int(self._collection(collection).count())


class VectorStore:
    """Collection-oriented vector store with automatic backend selection."""

    def __init__(
        self,
        persist_directory: str | Path | None = None,
        *,
        backend: str = "auto",
        embedding_service: EmbeddingService | None = None,
        auto_create_collections: bool = True,
        collections: Sequence[str] = DEFAULT_COLLECTIONS,
    ) -> None:
        """Create a store.

        Args:
            persist_directory: Storage directory; defaults to ``~/.openharness/chromadb``.
            backend: ``auto`` (ChromaDB when available) / ``chroma`` / ``simple``.
            embedding_service: Embedding provider chain; defaults to ``auto``.
            auto_create_collections: Create the predefined collections eagerly.
            collections: Collection names created upfront.
        """
        self._persist_directory = (
            Path(persist_directory) if persist_directory else get_config_dir() / "chromadb"
        )
        self._persist_directory.mkdir(parents=True, exist_ok=True)
        self._embedding = embedding_service or EmbeddingService()
        self._backend = self._create_backend(backend)
        self._lock = threading.RLock()
        self._revisions: dict[str, int] = {}
        self._known_collections = tuple(dict.fromkeys(str(name) for name in collections))
        if auto_create_collections:
            for name in self._known_collections:
                self.ensure_collection(name)

    def _create_backend(self, backend: str) -> _VectorBackend:
        requested = (backend or "auto").strip().lower()
        if requested == "auto":
            try:
                return _ChromaBackend(self._persist_directory)
            except ImportError:
                return _SimpleBackend(self._persist_directory)
        if requested == "chroma":
            return _ChromaBackend(self._persist_directory)
        if requested == "simple":
            return _SimpleBackend(self._persist_directory)
        raise ValueError(f"Unknown vector store backend: {backend!r}")

    # ------------------------------------------------------------------属性

    @property
    def backend_name(self) -> str:
        """Return the active backend name (``chroma`` or ``simple``)."""
        return self._backend.name

    @property
    def embedding_model(self) -> str:
        """Return the provider/model tag of the active embedding service."""
        return self._embedding.model

    @property
    def persist_directory(self) -> Path:
        """Return the storage directory in use."""
        return self._persist_directory

    def revision(self, collection: str) -> int:
        """Return a monotonically increasing revision for cache invalidation."""
        with self._lock:
            return self._revisions.get(collection, 0)

    # ------------------------------------------------------------------内部

    def _normalize_collection(self, name: str) -> str:
        collection = str(name).strip()
        if not collection:
            raise ValueError("Collection name must be non-empty")
        return collection

    def _ensure_ready(self, collection: str) -> None:
        with self._lock:
            self._backend.ensure_collection(collection, self._embedding.model)
            self._revisions.setdefault(collection, 0)

    def _check_embedding_model(self, collection: str) -> None:
        stored = self._backend.get_embedding_model(collection)
        current = self._embedding.model
        if stored is None:
            self._backend.set_embedding_model(collection, current)
            return
        if stored != current:
            raise EmbeddingMismatchError(collection, stored, current)

    # ------------------------------------------------------------------集合

    def ensure_collection(self, name: str) -> None:
        """Create the collection when missing (tagged with the active embedder)."""
        self._ensure_ready(self._normalize_collection(name))

    def list_collections(self) -> list[str]:
        """Return all known collection names."""
        return self._backend.list_collections()

    def count(self, collection: str) -> int:
        """Return the number of stored documents in a collection."""
        return self._backend.count(self._normalize_collection(collection))

    def drop_collection(self, collection: str) -> bool:
        """Delete a whole collection; returns whether it existed."""
        name = self._normalize_collection(collection)
        with self._lock:
            dropped = self._backend.drop_collection(name)
            if dropped:
                self._revisions[name] = self._revisions.get(name, 0) + 1
            return dropped

    # ------------------------------------------------------------------文档

    def add_documents(
        self,
        collection: str,
        documents: Sequence[Mapping[str, Any]],
        *,
        id_prefix: str = "",
    ) -> list[str]:
        """Embed and upsert documents; returns the stored ids.

        Each document is a mapping with optional ``id`` and ``metadata`` keys
        plus one of ``text`` / ``document`` / ``content`` for the payload.
        """
        name = self._normalize_collection(collection)
        texts: list[str] = []
        ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for document in documents:
            text = str(
                document.get("text") or document.get("document") or document.get("content") or ""
            )
            if not text.strip():
                continue
            raw_id = document.get("id")
            doc_id = str(raw_id).strip() if raw_id is not None else ""
            if not doc_id:
                doc_id = content_hash(text, length=20)
            if id_prefix and not doc_id.startswith(id_prefix):
                doc_id = f"{id_prefix}{doc_id}"
            raw_metadata = document.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, Mapping) else None
            texts.append(text)
            ids.append(doc_id)
            metadatas.append(sanitize_metadata(metadata))
        if not texts:
            return []
        #同一批内去重（后写覆盖先写），ChromaDB 会拒绝批内重复 id
        latest: dict[str, int] = {}
        for index, doc_id in enumerate(ids):
            latest[doc_id] = index
        positions = sorted(latest.values())
        ids = [ids[index] for index in positions]
        texts = [texts[index] for index in positions]
        metadatas = [metadatas[index] for index in positions]
        with self._lock:
            self._ensure_ready(name)
            self._check_embedding_model(name)
            embeddings = self._embedding.embed_texts(texts)
            self._backend.upsert(name, ids, texts, metadatas, embeddings)
            self._revisions[name] = self._revisions.get(name, 0) + 1
        return ids

    def search(
        self,
        collection: str,
        query: str,
        *,
        top_k: int = 5,
        where: Mapping[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Vector-search a collection by natural-language query."""
        name = self._normalize_collection(collection)
        if not query.strip():
            return []
        with self._lock:
            self._ensure_ready(name)
            self._check_embedding_model(name)
            query_embedding = self._embedding.embed_query(query)
        if not query_embedding:
            return []
        return self._backend.query(name, query_embedding, max(1, top_k), where)

    def get_documents(
        self,
        collection: str,
        *,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[StoredDocument]:
        """Return stored documents (used for BM25 indexing and inspection)."""
        name = self._normalize_collection(collection)
        self._ensure_ready(name)
        return self._backend.get_documents(name, where, limit)

    def delete_documents(
        self,
        collection: str,
        *,
        ids: Sequence[str] | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> int:
        """Delete documents by id or metadata filter; returns the deleted count."""
        name = self._normalize_collection(collection)
        if ids is None and where is None:
            raise ValueError("delete_documents requires ids or where")
        with self._lock:
            self._ensure_ready(name)
            deleted = self._backend.delete(name, ids, where)
            if deleted:
                self._revisions[name] = self._revisions.get(name, 0) + 1
            return deleted


class CollectionManager:
    """Creates and inspects the predefined RAG collections."""

    def __init__(
        self,
        store: VectorStore,
        collections: Sequence[str] = DEFAULT_COLLECTIONS,
    ) -> None:
        self._store = store
        self._collections = tuple(dict.fromkeys(str(name) for name in collections))

    @property
    def expected_collections(self) -> list[str]:
        """Return the collection names this manager guarantees."""
        return list(self._collections)

    def ensure_all(self) -> list[str]:
        """Create any missing collections; returns the newly created names."""
        existing = set(self._store.list_collections())
        created: list[str] = []
        for name in self._collections:
            if name not in existing:
                self._store.ensure_collection(name)
                created.append(name)
        return created

    def missing(self) -> list[str]:
        """Return expected collections that do not exist yet."""
        existing = set(self._store.list_collections())
        return [name for name in self._collections if name not in existing]
