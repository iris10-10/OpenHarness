"""Tests for RAG context injection into the runtime system prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openharness.config.settings import Settings
from openharness.prompts.context import build_rag_context, build_runtime_system_prompt
from openharness.rag.retriever import Retriever
from openharness.rag.vectorstore import VectorStore


def _patch_factory(monkeypatch: pytest.MonkeyPatch, retriever: Any) -> None:
    """Make ``build_rag_context`` resolve to the supplied retriever."""
    import openharness.rag

    monkeypatch.setattr(
        openharness.rag, "build_retriever_from_settings", lambda settings, **kwargs: retriever
    )


def _patch_factory_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import openharness.rag

    def _boom(settings: Any, **kwargs: Any) -> Any:
        del settings, kwargs
        raise RuntimeError("stack unavailable")

    monkeypatch.setattr(openharness.rag, "build_retriever_from_settings", _boom)


@pytest.fixture
def rag_settings(settings: Settings) -> Settings:
    settings.rag.enabled = True
    return settings


def test_disabled_rag_returns_none(tmp_path: Path, settings: Settings) -> None:
    assert build_rag_context(settings, cwd=tmp_path, latest_user_prompt="Python") is None


def test_missing_prompt_returns_none(tmp_path: Path, rag_settings: Settings) -> None:
    assert build_rag_context(rag_settings, cwd=tmp_path, latest_user_prompt=None) is None
    assert build_rag_context(rag_settings, cwd=tmp_path, latest_user_prompt="   ") is None


def test_no_hits_returns_none(
    tmp_path: Path,
    rag_settings: Settings,
    seeded_store: VectorStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_factory(monkeypatch, Retriever(seeded_store, default_collection="resumes"))
    assert build_rag_context(rag_settings, cwd=tmp_path, latest_user_prompt="anything") is None


def test_section_is_built_when_hits_exist(
    tmp_path: Path, rag_settings: Settings, seeded_store: VectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_factory(monkeypatch, Retriever(seeded_store, default_collection="jobs"))
    section = build_rag_context(rag_settings, cwd=tmp_path, latest_user_prompt="Python 后端开发")
    assert section is not None
    assert section.startswith("# Retrieved Knowledge")
    assert "local knowledge base" in section


def test_retrieval_errors_are_swallowed(
    tmp_path: Path, rag_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_factory_failure(monkeypatch)
    assert build_rag_context(rag_settings, cwd=tmp_path, latest_user_prompt="Python") is None


def test_prompt_excludes_rag_section_when_disabled(tmp_path: Path, settings: Settings) -> None:
    prompt = build_runtime_system_prompt(
        settings, cwd=tmp_path, latest_user_prompt="Python", include_project_memory=False
    )
    assert "Retrieved Knowledge" not in prompt


def test_prompt_includes_rag_section_when_enabled(
    tmp_path: Path, rag_settings: Settings, seeded_store: VectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_factory(monkeypatch, Retriever(seeded_store, default_collection="jobs"))
    prompt = build_runtime_system_prompt(
        rag_settings, cwd=tmp_path, latest_user_prompt="Python 后端开发", include_project_memory=False
    )
    assert "# Retrieved Knowledge" in prompt
    assert "local knowledge base" in prompt


def test_prompt_survives_rag_failures(
    tmp_path: Path, rag_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_factory_failure(monkeypatch)
    prompt = build_runtime_system_prompt(
        rag_settings, cwd=tmp_path, latest_user_prompt="Python", include_project_memory=False
    )
    assert prompt
    assert "Retrieved Knowledge" not in prompt
