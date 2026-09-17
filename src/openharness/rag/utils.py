"""Shared helpers for the RAG package (tokenization, hashing, token estimates).

These helpers stay dependency-free on purpose: the RAG modules must import
cleanly even when the optional ``rag`` / ``rag-local`` extras are not
installed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

#ASCII 单词（含数字与下划线）
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
#CJK 字符（含扩展区、假名、谚文，覆盖中日韩混排文本）
_CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]+")


def tokenize_text(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text for keyword scoring.

    ASCII words are lowercased; CJK runs contribute single characters plus
    bigrams so that spaceless Chinese queries still receive partial matches.
    """
    tokens = [match.group(0).lower() for match in _ASCII_WORD_RE.finditer(text)]
    for run in _CJK_RUN_RE.findall(text):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def estimate_tokens(text: str) -> int:
    """Approximate a token count: one token per CJK char or ASCII word.

    This intentionally avoids a real tokenizer dependency; it is only used for
    chunk sizing and context budgeting where an estimate is sufficient.
    """
    return len(_CJK_CHAR_RE.findall(text)) + len(_ASCII_WORD_RE.findall(text))


def content_hash(text: str, *, length: int = 16) -> str:
    """Return a stable short hash used for document ids and cache keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def file_hash(path: str | Path, *, length: int = 32) -> str:
    """Return a stable hash of a file's bytes (used for incremental ingest)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()[:length]


def stable_json_dumps(value: Any) -> str:
    """Serialize a value deterministically (stable key order, UTF-8 kept)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
