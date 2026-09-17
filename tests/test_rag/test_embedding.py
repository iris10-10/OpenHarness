"""Tests for the embedding providers and the multi-provider service."""

from __future__ import annotations

import json
import sys
import time
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from openharness.rag.embedding import (
    EmbeddingProvider,
    EmbeddingService,
    HashEmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from openharness.rag.utils import content_hash


class CountingProvider(HashEmbeddingProvider):
    """Hash provider that records how often ``embed_texts`` is invoked."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed_texts(texts)


class BrokenProvider(EmbeddingProvider):
    """Provider that always fails; used to exercise the fallback chain."""

    name = "broken"
    model = "always-fails"

    def __init__(self) -> None:
        self.calls = 0

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        self.calls += 1
        raise RuntimeError("boom")


def test_hash_provider_is_deterministic_and_normalized() -> None:
    provider = HashEmbeddingProvider(dimension=64)
    first = provider.embed_texts(["Python 后端开发"])[0]
    second = provider.embed_texts(["Python 后端开发"])[0]
    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_hash_provider_ranks_related_text_closer() -> None:
    provider = HashEmbeddingProvider()
    base, near, far = provider.embed_texts(
        ["Python 后端工程师", "Python 后端开发", "市场运营专员"]
    )
    overlap_near = sum(a * b for a, b in zip(base, near))
    overlap_far = sum(a * b for a, b in zip(base, far))
    assert overlap_near > overlap_far


def test_hash_provider_rejects_invalid_dimension() -> None:
    with pytest.raises(ValueError):
        HashEmbeddingProvider(dimension=0)


def test_openai_provider_requires_api_key() -> None:
    with pytest.raises(ValueError):
        OpenAIEmbeddingProvider(api_key="   ")


def test_openai_provider_batches_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    class FakeEmbeddings:
        def create(self, *, model: str, input: list[str]) -> Any:
            calls.append((model, list(input)))
            data = [types.SimpleNamespace(embedding=[0.1, 0.2]) for _ in input]
            return types.SimpleNamespace(data=data)

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.embeddings = FakeEmbeddings()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeClient))
    provider = OpenAIEmbeddingProvider(
        model="demo-model", api_key="key", base_url="https://example.test", batch_size=1
    )
    vectors = provider.embed_texts(["one", "two"])
    assert vectors == [[0.1, 0.2], [0.1, 0.2]]
    assert [text for _model, batch in calls for text in batch] == ["one", "two"]
    assert all(model == "demo-model" for model, _batch in calls)


def test_local_provider_availability_flag() -> None:
    assert isinstance(LocalEmbeddingProvider.is_available(), bool)


def test_service_hash_mode_and_identifiers(tmp_path: Path) -> None:
    service = EmbeddingService(provider="hash", cache_enabled=False, cache_dir=tmp_path)
    assert service.provider_name == "hash"
    assert service.model == "hash:blake2b-384"
    assert service.provider_chain == ["hash:blake2b-384"]
    assert service.embed_texts([]) == []
    assert len(service.embed_query("hello")) == 384


def test_service_auto_without_credentials_degrades_to_hash(tmp_path: Path) -> None:
    service = EmbeddingService(provider="auto", cache_enabled=False, cache_dir=tmp_path)
    assert "hash:blake2b-384" in service.provider_chain
    if not LocalEmbeddingProvider.is_available():
        assert service.provider_name == "hash"


def test_service_openai_requested_without_key_records_note(tmp_path: Path) -> None:
    service = EmbeddingService(provider="openai", cache_enabled=False, cache_dir=tmp_path)
    assert any("no API key" in note for note in service.downgrade_notes)
    assert service.provider_name == "hash"


def test_service_downgrade_is_sticky(tmp_path: Path) -> None:
    broken = BrokenProvider()
    healthy = CountingProvider()
    service = EmbeddingService(
        providers=[broken, healthy],
        cache_enabled=False,
        cache_dir=tmp_path,
    )
    vectors = service.embed_texts(["hello"])
    assert len(vectors[0]) == 384
    assert service.provider_name == "hash"
    assert any("broken:always-fails failed" in note for note in service.downgrade_notes)
    service.embed_texts(["world"])
    assert broken.calls == 1  # the failing provider is never retried
    assert healthy.calls == 2


def test_service_raises_when_all_providers_fail(tmp_path: Path) -> None:
    service = EmbeddingService(
        providers=[BrokenProvider()],
        cache_enabled=False,
        cache_dir=tmp_path,
    )
    with pytest.raises(RuntimeError):
        service.embed_texts(["hello"])


def test_service_rejects_empty_provider_list() -> None:
    with pytest.raises(ValueError):
        EmbeddingService(providers=[], cache_enabled=False)


def test_disk_cache_serves_second_service_instance(tmp_path: Path) -> None:
    first = CountingProvider()
    service_one = EmbeddingService(providers=[first], cache_dir=tmp_path, cache_ttl_days=30)
    vectors_one = service_one.embed_texts(["缓存测试", "第二个"])
    assert first.calls == 1

    second = CountingProvider()
    service_two = EmbeddingService(providers=[second], cache_dir=tmp_path, cache_ttl_days=30)
    vectors_two = service_two.embed_texts(["缓存测试", "第二个"])
    assert second.calls == 0
    assert vectors_one == vectors_two
    assert (tmp_path / "embedding_cache.json").exists()


def test_cache_ignores_expired_entries(tmp_path: Path) -> None:
    cache_path = tmp_path / "embedding_cache.json"
    key = f"hash:blake2b-384::{content_hash('过期内容', length=32)}"
    stale_timestamp = time.time() - 40 * 86400
    cache_path.write_text(
        json.dumps({"entries": {key: {"vector": [0.5] * 384, "ts": stale_timestamp}}}),
        encoding="utf-8",
    )
    provider = CountingProvider()
    service = EmbeddingService(providers=[provider], cache_dir=tmp_path, cache_ttl_days=30)
    vectors = service.embed_texts(["过期内容"])
    assert provider.calls == 1
    assert vectors[0] != [0.5] * 384


def test_cache_can_be_disabled(tmp_path: Path) -> None:
    service = EmbeddingService(provider="hash", cache_enabled=False, cache_dir=tmp_path)
    service.embed_texts(["hello"])
    assert not (tmp_path / "embedding_cache.json").exists()
