"""Temporary smoke test for Phase 1 RAG modules (will be deleted)."""

import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="rag_smoke_"))

# 1. light import
import openharness.rag as rag  # noqa: E402

print("import openharness.rag OK")
assert "chromadb" not in sys.modules, "chromadb must not be imported eagerly"

# 2. settings
from openharness.config.settings import Settings  # noqa: E402

settings = Settings()
print("Settings().rag.enabled:", settings.rag.enabled, "| default_collection:", settings.rag.default_collection)

# 3. vector store (simple backend + hash embedding) via lazy export
from openharness.rag import CollectionManager, DocumentIngestor, EmbeddingService, VectorStore  # noqa: E402

service = EmbeddingService(provider="hash", cache_enabled=False, cache_dir=tmp / "cache")
store = VectorStore(tmp / "store", backend="simple", embedding_service=service)
print("store backend:", store.backend_name, "| collections:", store.list_collections())

store.add_documents(
    "jobs",
    [
        {"id": "j1", "text": "Python 后端工程师，负责服务端开发", "metadata": {"city": "北京", "salary_max": 40000}},
        {"id": "j2", "text": "市场运营专员，负责活动策划", "metadata": {"city": "上海", "salary_max": 15000}},
    ],
)
hits = store.search("jobs", "Python 后端开发", top_k=2)
print("vector search:", [(h.id, round(h.score, 3)) for h in hits])
assert hits and hits[0].id == "j1"

# 4. retriever
from openharness.rag.retriever import Retriever, format_retrieval_for_tool  # noqa: E402

retriever = Retriever(store, default_collection="jobs")
outcome = retriever.retrieve_sync("Python 后端")
print("retrieve_sync hits:", [h.id for h in outcome.hits], "| budget:", outcome.budget_tokens)
assert outcome.hits and outcome.hits[0].id == "j1"

# 5. ingestion
from openharness.rag import TextChunker  # noqa: E402

ingestor = DocumentIngestor(store, chunker=TextChunker(max_tokens=60, overlap_tokens=10), state_path=tmp / "state.json")
report = ingestor.ingest_text(
    "knowledge",
    "# 面试准备\n\n" + "Java 并发编程经常考线程池与锁。" * 20 + "\n\n## 算法\n\n手写快排与 LRU。",
    metadata={"source": "interview.md"},
)
print("ingest report:", report.files_processed, report.chunks_created, report.errors)
assert report.chunks_created >= 2
docs = store.get_documents("knowledge")
print("stored chunks:", len(docs), "| first title:", docs[0].metadata.get("title"))

# 6. tools registry
from openharness.tools import create_default_tool_registry  # noqa: E402

registry = create_default_tool_registry()
for name in ("rag_search", "rag_search_jobs", "rag_search_interview"):
    tool = registry.get(name)
    assert tool is not None, name
    print("tool registered:", name, "| read_only:", tool.is_read_only(None))

# 7. tool execution with injected retriever
import asyncio  # noqa: E402

from openharness.tools.base import ToolExecutionContext  # noqa: E402
from openharness.tools.rag_search_tool import RAGSearchJobsTool, RAGSearchJobsToolInput  # noqa: E402

tool = RAGSearchJobsTool(retriever=retriever)
result = asyncio.run(
    tool.execute(RAGSearchJobsToolInput(query="Python 后端", city="北京"), ToolExecutionContext(cwd=tmp))
)
print("tool output head:", result.output.splitlines()[0])
assert not result.is_error and result.metadata["result_count"] == 1

# 8. prompt injection (disabled by default -> None)
from openharness.prompts.context import build_rag_context, build_runtime_system_prompt  # noqa: E402

assert build_rag_context(settings, cwd=tmp, latest_user_prompt="Python") is None
prompt = build_runtime_system_prompt(settings, cwd=tmp, latest_user_prompt="Python", include_project_memory=False)
assert "Retrieved Knowledge" not in prompt
print("prompt injection disabled OK; prompt length:", len(prompt))

# 9. chroma backend availability
try:
    chroma_store = VectorStore(tmp / "chroma", backend="chroma", embedding_service=service)
    chroma_store.add_documents("knowledge", [{"id": "c1", "text": "ChromaDB 后端可用", "metadata": {"k": "v"}}])
    found = chroma_store.search("knowledge", "ChromaDB", top_k=1)
    print("chroma backend OK; hits:", [(h.id, round(h.score, 3)) for h in found])
except ImportError as exc:
    print("chroma backend unavailable:", exc)

print("SMOKE DONE")
