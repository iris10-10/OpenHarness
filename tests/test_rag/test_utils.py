"""Tests for RAG low-level helpers (tokenization, hashing, token estimates)."""

from __future__ import annotations

from pathlib import Path

from openharness.rag.utils import (
    content_hash,
    estimate_tokens,
    file_hash,
    stable_json_dumps,
    tokenize_text,
)


def test_tokenize_mixed_text_lowercases_ascii_and_keeps_cjk_parts() -> None:
    tokens = tokenize_text("Python 后端开发 Python")
    assert tokens.count("python") == 2
    assert "后" in tokens
    assert "后端" in tokens
    assert "后端开发" not in tokens  # long CJK runs become single chars plus bigrams


def test_tokenize_cjk_bigrams_and_single_chars() -> None:
    assert tokenize_text("你好") == ["你", "好", "你好"]


def test_tokenize_empty_text() -> None:
    assert tokenize_text("") == []


def test_estimate_tokens_counts_cjk_chars_plus_ascii_words() -> None:
    assert estimate_tokens("你好 world 世界") == 5
    assert estimate_tokens("你好，世界！") == 4  # punctuation is not counted
    assert estimate_tokens("") == 0


def test_content_hash_is_stable_and_length_aware() -> None:
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("hello!")
    assert len(content_hash("hello", length=8)) == 8


def test_file_hash_tracks_content(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text("alpha", encoding="utf-8")
    first = file_hash(path)
    assert first == file_hash(path)
    path.write_text("beta", encoding="utf-8")
    assert file_hash(path) != first
    assert len(file_hash(path, length=16)) == 16


def test_stable_json_dumps_sorts_keys_and_keeps_unicode() -> None:
    assert stable_json_dumps({"b": 1, "a": "中文"}) == '{"a": "中文", "b": 1}'
    assert stable_json_dumps({"a": 1}) == stable_json_dumps({"a": 1})
