"""Temporary ChromaDB API probe for Phase 1 (will be deleted)."""

import tempfile
import traceback
from pathlib import Path

import chromadb

print("chromadb version:", chromadb.__version__)

tmp = Path(tempfile.mkdtemp(prefix="chroma_probe_"))

try:
    from chromadb.config import Settings

    settings = Settings(anonymized_telemetry=False)
    print("config.Settings OK")
except Exception as exc:  # noqa: BLE001
    settings = None
    print("config.Settings FAILED:", exc)

client = chromadb.PersistentClient(path=str(tmp / "db1"), settings=settings)
print("PersistentClient OK")

try:
    col = client.create_collection(
        name="knowledge", metadata={"hnsw:space": "cosine", "embedding_model": "hash:1"}
    )
    print("create with hnsw:space metadata OK; space metadata:", col.metadata)
except Exception as exc:  # noqa: BLE001
    print("create with hnsw:space metadata FAILED:", type(exc).__name__, exc)
    col = client.create_collection(name="knowledge", metadata={"embedding_model": "hash:1"})
    print("create with plain metadata OK:", col.metadata)

col.add(
    ids=["a", "b"],
    documents=["Python 后端工程师", "市场运营专员"],
    metadatas=[{"city": "北京", "salary_max": 30000, "tags": '["python"]'}, {"city": "上海", "salary_max": 12000, "tags": '["ops"]'}],
    embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
)
print("add OK, count:", col.count())

res = col.query(query_embeddings=[[1.0, 0.1, 0.0]], n_results=2, where={"salary_max": {"$gte": 20000}})
print("query where $gte OK:", res["ids"], res["distances"])

doc = col.get(include=["documents", "metadatas"])
print("get all OK, ids:", doc["ids"])
doc2 = col.get(limit=1, include=["documents"])
print("get limit OK:", doc2["ids"])

try:
    col.modify(metadata={"hnsw:space": "cosine", "embedding_model": "hash:2"})
    print("modify metadata OK:", col.metadata)
except Exception as exc:  # noqa: BLE001
    print("modify metadata FAILED:", type(exc).__name__, exc)

col.delete(ids=["a"])
print("delete by id OK, count:", col.count())
col.delete(where={"city": "上海"})
print("delete by where OK, count:", col.count())

try:
    client.get_collection("missing_collection")
    print("get_collection(missing) unexpectedly OK")
except Exception as exc:  # noqa: BLE001
    print("get_collection(missing) raises:", type(exc).__name__)

# second client to the same path
try:
    client2 = chromadb.PersistentClient(path=str(tmp / "db1"))
    print("second client same path OK; collections:", [c.name for c in client2.list_collections()])
except Exception as exc:  # noqa: BLE001
    print("second client same path FAILED:", type(exc).__name__, exc)

# upsert duplicate ids in one batch
try:
    col2 = client.get_or_create_collection(name="dup")
    col2.upsert(ids=["x", "x"], documents=["1", "2"], embeddings=[[1.0, 0.0], [0.0, 1.0]])
    print("duplicate ids in one batch: OK")
except Exception as exc:  # noqa: BLE001
    print("duplicate ids in one batch raises:", type(exc).__name__, str(exc)[:120])

try:
    client.delete_collection("dup")
    print("delete_collection OK")
except Exception as exc:  # noqa: BLE001
    print("delete_collection FAILED:", type(exc).__name__)

# query on empty collection
try:
    empty = client.get_or_create_collection(name="empty")
    r = empty.query(query_embeddings=[[1.0, 0.0]], n_results=3)
    print("query empty collection OK:", r["ids"])
except Exception as exc:  # noqa: BLE001
    print("query empty collection raises:", type(exc).__name__, str(exc)[:120])

# what distance metric is reported by default (no hnsw:space)?
try:
    plain = client.get_or_create_collection(name="plain")
    plain.add(ids=["p"], documents=["x"], embeddings=[[1.0, 0.0]])
    cfg = getattr(plain, "configuration", None)
    print("plain collection configuration:", cfg)
except Exception as exc:  # noqa: BLE001
    print("plain config probe failed:", type(exc).__name__)
print("PROBE DONE")
