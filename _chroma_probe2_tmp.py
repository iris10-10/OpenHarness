"""Temporary ChromaDB probe #2 for Phase 1 (will be deleted)."""

import tempfile
from pathlib import Path

import chromadb
from chromadb.config import Settings

tmp = Path(tempfile.mkdtemp(prefix="chroma_probe2_"))
db = str(tmp / "db")
settings = Settings(anonymized_telemetry=False)

client = chromadb.PersistentClient(path=db, settings=settings)
col = client.create_collection(name="knowledge", metadata={"hnsw:space": "cosine", "embedding_model": "hash:v1"})
col.add(ids=["a"], documents=["hello"], embeddings=[[1.0, 0.0, 0.0]])

# second client, same path and SAME settings
try:
    client2 = chromadb.PersistentClient(path=db, settings=Settings(anonymized_telemetry=False))
    col2 = client2.get_collection("knowledge")
    print("second client same settings OK; count:", col2.count(), "| metadata:", col2.metadata)
except Exception as exc:  # noqa: BLE001
    print("second client same settings FAILED:", type(exc).__name__, exc)

# modify metadata without hnsw:space
try:
    col.modify(metadata={"embedding_model": "hash:v2"})
    print("modify without hnsw:space OK; metadata now:", col.metadata)
except Exception as exc:  # noqa: BLE001
    print("modify without hnsw:space FAILED:", type(exc).__name__, exc)

# query distance with cosine space and identical vector
res = col.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)
print("cosine distance identical:", res["distances"])

# list_collections shape
print("list_collections:", [c.name for c in client.list_collections()])

# delete(where) no match
try:
    col.delete(where={"missing": "x"})
    print("delete where no match OK")
except Exception as exc:  # noqa: BLE001
    print("delete where no match FAILED:", type(exc).__name__, exc)

# get with where + include metadatas
res2 = col.get(where={"embedding_model": "hash:v2"}, include=["documents"])
print("get filtering by metadata written via modify:", res2["ids"])

print("PROBE2 DONE")
