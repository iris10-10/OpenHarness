"""Shared fixtures for the Phase 2 job-hunt tool tests.

Job-hunt tools are thin adapters: parsing/scoring live in
``openharness.jobhunt`` and retrieval goes through a duck-typed retriever.
Tests inject fake retrievers plus a per-test temp data directory, so nothing
touches the network or the user's real ``~/.openharness`` state.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openharness.config.settings import Settings
from openharness.tools.base import ToolExecutionContext

#一个可被 parsing 稳定解析的 JD；简历使用固定起止日期，避免"至今"随时间漂移
JD_TEXT = """岗位名称：Python 后端开发工程师
公司名称：杭州星辰科技有限公司
工作城市：杭州
薪资范围：25-40K·14薪
岗位职责：
1. 负责后端服务的设计与开发
2. 优化系统性能，参与架构演进
任职要求：
1. 本科及以上学历，3-5 年经验
2. 熟练掌握 Python、FastAPI，熟悉 MySQL、Redis
3. 了解 Docker、Kubernetes 者优先
"""

RESUME_TEXT = """张伟
电话：13800138000  邮箱：zhangwei@example.com
求职意向：Python 后端开发工程师
工作经历
2020.03 - 2024.03  某某科技有限公司  后端开发工程师
- 负责交易系统后端开发，使用 Python、FastAPI、MySQL、Redis，接口延迟降低 40%
教育背景
2016.09 - 2020.06  某某大学  计算机科学与技术  本科
专业技能
Python、FastAPI、MySQL、Redis、Docker、Git
"""

PROFILE_DICT: dict[str, Any] = {
    "basic": {
        "current_city": "杭州",
        "target_cities": ["杭州", "上海"],
        "years_of_experience": 6.5,
        "current_title": "Python 后端开发工程师",
    },
    "skills": {
        "technical": [
            {"name": "Python", "level": "熟练"},
            {"name": "FastAPI", "level": "熟练"},
            {"name": "MySQL", "level": "熟练"},
            {"name": "Redis", "level": "熟练"},
            {"name": "Docker", "level": "了解"},
            {"name": "Kubernetes", "level": "了解"},
        ]
    },
    "education": {"degree": "本科"},
    "preferences": {"expected_salary_min": 30000, "expected_salary_max": 45000},
}


class FakeStore:
    """In-memory RAG store implementing the ``add_documents`` contract."""

    def __init__(self) -> None:
        self.documents: dict[str, list[dict[str, Any]]] = {}

    def add_documents(self, collection: str, documents: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        bucket = self.documents.setdefault(collection, [])
        for index, document in enumerate(documents, start=1):
            doc_id = str(document.get("id") or f"{collection}-doc-{len(bucket) + index}")
            bucket.append(
                {
                    "id": doc_id,
                    "text": str(document.get("text", "")),
                    "metadata": dict(document.get("metadata", {})),
                }
            )
            ids.append(doc_id)
        return ids


def _compare(actual: Any, operator: str, operand: Any) -> bool:
    if operator == "$in":
        return actual in operand
    if actual is None:
        return False
    if operator == "$gte":
        return actual >= operand
    if operator == "$lte":
        return actual <= operand
    if operator == "$gt":
        return actual > operand
    if operator == "$lt":
        return actual < operand
    if operator == "$ne":
        return actual != operand
    return False


def _where_matches(metadata: dict[str, Any], where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if not all(_compare(actual, op, operand) for op, operand in expected.items()):
                return False
        elif actual != expected:
            return False
    return True


class FakeRetriever:
    """Duck-typed retriever returning prepared hits, honouring simple filters."""

    def __init__(
        self,
        hits: dict[str, list[Any]] | None = None,
        notes: dict[str, list[str]] | None = None,
    ) -> None:
        self.hits = hits or {}
        self.notes = notes or {}
        self.store = FakeStore()
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self,
        query: str,
        *,
        collection: str = "",
        collections: list[str] | None = None,
        where: dict[str, Any] | None = None,
        top_n: int = 0,
        **_ignored: Any,
    ) -> SimpleNamespace:
        names = [collection] if collection else list(collections or [])
        self.calls.append({"query": query, "collections": names, "where": where})
        hits: list[Any] = []
        notes: list[str] = []
        for name in names:
            hits.extend(
                hit for hit in self.hits.get(name, []) if _where_matches(hit.metadata, where)
            )
            notes.extend(self.notes.get(name, []))
        if top_n:
            hits = hits[:top_n]
        return SimpleNamespace(hits=hits, notes=notes)


class OfflineStore:
    """Store whose backend is unavailable — writes raise."""

    def add_documents(self, *_args: Any, **_kwargs: Any) -> list[str]:
        raise RuntimeError("rag offline (test)")


class OfflineRetriever:
    """Retriever whose RAG backend is unavailable — reads and writes raise."""

    def __init__(self) -> None:
        self.store = OfflineStore()

    async def retrieve(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("rag offline (test)")


@pytest.fixture()
def jd_text() -> str:
    return JD_TEXT


@pytest.fixture()
def resume_text() -> str:
    return RESUME_TEXT


@pytest.fixture()
def profile_dict() -> dict[str, Any]:
    return copy.deepcopy(PROFILE_DICT)


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path, metadata={"jobhunt_data_dir": str(tmp_path)})


@pytest.fixture()
def make_hit():
    def _make_hit(
        text: str = "",
        *,
        doc_id: str = "doc-1",
        collection: str = "companies",
        score: float = 0.9,
        **metadata: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=doc_id, text=text, metadata=metadata, collection=collection, score=score
        )

    return _make_hit


@pytest.fixture()
def make_retriever():
    def _make_retriever(
        hits: dict[str, list[Any]] | None = None,
        notes: dict[str, list[str]] | None = None,
    ) -> FakeRetriever:
        return FakeRetriever(hits=hits, notes=notes)

    return _make_retriever


@pytest.fixture()
def retriever() -> FakeRetriever:
    return FakeRetriever()


@pytest.fixture()
def offline_retriever() -> OfflineRetriever:
    return OfflineRetriever()
