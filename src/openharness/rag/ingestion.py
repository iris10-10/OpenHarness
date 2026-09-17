"""Document ingestion pipeline: parsing, chunking, enrichment, incremental updates.

Supports PDF / Markdown / JSON / HTML / plain-text sources. Documents are
chunked by approximate token size with configurable overlap, enriched with
source/type/title/keyword metadata, and upserted into a :class:`VectorStore`.
Directory ingestion is incremental: unchanged files (by content hash) are
skipped, changed files replace their previous chunks, and chunks of deleted
files are removed.
"""

from __future__ import annotations

import html as html_module
import importlib
import json
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openharness.config.paths import get_data_dir
from openharness.rag.utils import content_hash, estimate_tokens, file_hash, tokenize_text
from openharness.rag.vectorstore import VectorStore
from openharness.utils.fs import atomic_write_text

SUPPORTED_EXTENSIONS: tuple[str, ...] = (".md", ".markdown", ".txt", ".json", ".html", ".htm", ".pdf")

_DOC_TYPE_BY_EXTENSION = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
}

_HEADING_SPLIT_RE = re.compile(r"(?=^#{1,6}\s)", re.MULTILINE)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;.])")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_KEYWORD_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "are", "was",
    "were", "have", "has", "will", "not", "but", "can", "our", "their", "they", "its",
    "的是", "我们", "你们", "他们", "以及", "并且", "可以", "这个", "那个", "进行",
    "通过", "如果", "因为", "所以", "但是", "而且", "或者", "一个", "不是", "没有",
    "需要", "要求", "熟悉", "负责", "相关",
}


@dataclass(frozen=True)
class TextChunk:
    """A chunk of document text with position and size metadata."""

    text: str
    index: int
    token_count: int


@dataclass
class IngestionReport:
    """Summary of an ingestion run."""

    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: IngestionReport) -> None:
        """Fold another report's counters and errors into this one."""
        self.files_processed += other.files_processed
        self.files_skipped += other.files_skipped
        self.files_failed += other.files_failed
        self.chunks_created += other.chunks_created
        self.chunks_deleted += other.chunks_deleted
        self.errors.extend(other.errors)


def _normalize_text(text: str) -> str:
    """Normalize line endings and collapse redundant whitespace."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _overlap_tail(parts: Sequence[str], overlap_tokens: int) -> list[str]:
    """Return the trailing parts that fit within the overlap budget."""
    if overlap_tokens <= 0 or not parts:
        return []
    tail: list[str] = []
    total = 0
    for part in reversed(parts):
        part_tokens = estimate_tokens(part)
        if tail and total + part_tokens > overlap_tokens:
            break
        tail.insert(0, part)
        total += part_tokens
        if total >= overlap_tokens:
            break
    return tail


def _hard_split(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split an oversized unit by characters using the token/char ratio."""
    tokens = max(1, estimate_tokens(text))
    if tokens <= max_tokens:
        return [text]
    char_budget = max(1, int(len(text) * max_tokens / tokens))
    overlap_chars = max(0, int(len(text) * overlap_tokens / tokens))
    step = max(1, char_budget - overlap_chars)
    pieces = [text[start : start + char_budget].strip() for start in range(0, len(text), step)]
    return [piece for piece in pieces if piece]


class TextChunker:
    """Split documents into overlapping chunks sized by approximate tokens.

    Two strategies are available: ``semantic`` splits on headings/paragraphs
    first (preferred for structured docs) while ``fixed`` splits on sentence
    boundaries only.
    """

    def __init__(
        self,
        *,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        strategy: str = "semantic",
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("overlap_tokens must be within [0, max_tokens)")
        normalized = (strategy or "semantic").strip().lower()
        if normalized not in {"semantic", "fixed"}:
            raise ValueError(f"Unknown chunking strategy: {strategy!r}")
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens
        self._strategy = normalized

    @property
    def max_tokens(self) -> int:
        """Return the configured chunk size budget."""
        return self._max_tokens

    @property
    def overlap_tokens(self) -> int:
        """Return the configured overlap budget."""
        return self._overlap_tokens

    @property
    def strategy(self) -> str:
        """Return the active split strategy (``semantic`` / ``fixed``)."""
        return self._strategy

    def chunk(self, text: str) -> list[TextChunk]:
        """Split text into chunks (empty input yields no chunks)."""
        normalized = _normalize_text(text)
        if not normalized:
            return []
        units: list[str] = []
        for unit in self._split_units(normalized):
            if estimate_tokens(unit) > self._max_tokens:
                units.extend(_hard_split(unit, self._max_tokens, self._overlap_tokens))
            else:
                units.append(unit)

        pieces: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for unit in units:
            unit_tokens = estimate_tokens(unit)
            if current and current_tokens + unit_tokens > self._max_tokens:
                pieces.append("\n".join(current))
                current = _overlap_tail(current, self._overlap_tokens)
                current_tokens = sum(estimate_tokens(part) for part in current)
                if current_tokens + unit_tokens > self._max_tokens:
                    #重叠部分加新单元仍超限时，放弃重叠以保证块大小受限
                    current = []
                    current_tokens = 0
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            pieces.append("\n".join(current))

        cleaned = [piece.strip() for piece in pieces if piece.strip()]
        return [
            TextChunk(text=piece, index=index, token_count=estimate_tokens(piece))
            for index, piece in enumerate(cleaned)
        ]

    def _split_units(self, text: str) -> list[str]:
        """Split text into the smallest units the packer may merge."""
        if self._strategy == "fixed":
            sentence_units = [
                unit.strip() for unit in _SENTENCE_SPLIT_RE.split(text) if unit.strip()
            ]
            return sentence_units or [text]
        units: list[str] = []
        for section in _HEADING_SPLIT_RE.split(text):
            section = section.strip()
            if not section:
                continue
            paragraphs = [part.strip() for part in _PARAGRAPH_SPLIT_RE.split(section) if part.strip()]
            units.extend(paragraphs or [section])
        return units or [text]


def _extract_title(text: str) -> str:
    """Derive a title from the first meaningful line."""
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        candidate = re.sub(r"^#{1,6}\s*", "", candidate)
        candidate = re.sub(r"^[=\-*•]+\s*", "", candidate)
        return candidate[:80]
    return ""


class MetadataEnricher:
    """Derive source/type/title/keyword metadata for ingested documents."""

    def __init__(self, *, max_keywords: int = 8) -> None:
        self._max_keywords = max(0, max_keywords)

    def enrich(
        self,
        *,
        source: str | None,
        text: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return metadata with derived fields filled in (caller values win)."""
        enriched: dict[str, Any] = dict(metadata or {})
        if source:
            enriched.setdefault("source", str(source))
            suffix = Path(source).suffix.lower()
            enriched.setdefault("doc_type", _DOC_TYPE_BY_EXTENSION.get(suffix, "text"))
        title = _extract_title(text) or (Path(source).stem if source else "")
        if title:
            enriched.setdefault("title", title)
        keywords = self.extract_keywords(text)
        if keywords:
            enriched.setdefault("keywords", ", ".join(keywords))
        enriched.setdefault("char_count", len(text))
        return enriched

    def extract_keywords(self, text: str) -> list[str]:
        """Return the most frequent meaningful tokens (bigrams for Chinese)."""
        counts: dict[str, int] = {}
        for token in tokenize_text(text):
            if len(token) < 2 or token.isdigit():
                continue
            if token.lower() in _KEYWORD_STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
        if self._max_keywords == 0:
            return []
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [token for token, _count in ranked[: self._max_keywords]]


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF using the optional pypdf dependency."""
    try:
        pypdf = importlib.import_module("pypdf")
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required for PDF ingestion; install it with 'uv sync --extra rag'"
        ) from exc
    reader = pypdf.PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page.strip())


def _strip_html_tags(markup: str) -> str:
    """Dependency-free HTML-to-text fallback (used when bs4 is unavailable)."""
    text = _HTML_TAG_RE.sub("\n", markup)
    return html_module.unescape(text)


def _html_to_text(markup: str) -> str:
    """Convert HTML to plain text (bs4 when available, regex fallback otherwise)."""
    try:
        bs4 = importlib.import_module("bs4")
    except ImportError:
        return _strip_html_tags(markup)
    soup = None
    for parser in ("lxml", "html.parser"):
        try:
            soup = bs4.BeautifulSoup(markup, parser)
            break
        except Exception:
            soup = None
    if soup is None:
        return _strip_html_tags(markup)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return str(soup.get_text("\n"))


def _json_to_text(data: Any) -> str:
    """Flatten JSON data into readable ``key: value`` lines."""
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{key}:")
                nested = _json_to_text(value)
                if nested:
                    lines.extend("  " + line for line in nested.splitlines())
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
    if isinstance(data, list):
        blocks = [_json_to_text(item) for item in data]
        return "\n\n".join(block for block in blocks if block)
    return str(data)


def load_document_text(path: str | Path) -> str:
    """Parse a supported document file into plain text."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(file_path)
    if suffix in {".html", ".htm"}:
        return _html_to_text(file_path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".json":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            return _json_to_text(json.loads(raw))
        except json.JSONDecodeError:
            return raw
    return file_path.read_text(encoding="utf-8", errors="replace")


class DocumentIngestor:
    """Chunk, enrich and store documents; supports incremental directory sync."""

    def __init__(
        self,
        store: VectorStore,
        *,
        chunker: TextChunker | None = None,
        enricher: MetadataEnricher | None = None,
        state_path: str | Path | None = None,
        max_chunks_per_document: int = 400,
    ) -> None:
        self._store = store
        self._chunker = chunker or TextChunker()
        self._enricher = enricher or MetadataEnricher()
        self._state_path = Path(state_path) if state_path is not None else None
        self._max_chunks = max(1, max_chunks_per_document)
        self._state: dict[str, Any] = {}
        self._state_loaded = False

    # ------------------------------------------------------------------摄入

    def ingest_text(
        self,
        collection: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        doc_id: str | None = None,
        source: str | None = None,
    ) -> IngestionReport:
        """Chunk and store a text document; replaces previous incarnations."""
        report = IngestionReport()
        base: dict[str, Any] = dict(metadata or {})
        if source and "source" not in base:
            base["source"] = source
        effective_doc_id = (doc_id or "").strip() or str(base.get("source") or "") or content_hash(
            text, length=20
        )
        chunks = self._chunker.chunk(text)[: self._max_chunks]
        if not chunks:
            report.files_failed = 1
            report.errors.append("empty document skipped")
            return report

        #替换语义：先删除同一来源/同一 doc_id 的旧块，避免残留过期内容
        deleted = 0
        if base.get("source"):
            deleted += self._store.delete_documents(collection, where={"source": str(base["source"])})
        if doc_id:
            deleted += self._store.delete_documents(collection, where={"doc_id": doc_id})

        enriched = self._enricher.enrich(
            source=str(base.get("source") or source or "") or None, text=text, metadata=base
        )
        enriched["doc_id"] = effective_doc_id
        documents: list[dict[str, Any]] = []
        for chunk in chunks:
            chunk_metadata = dict(enriched)
            chunk_metadata["chunk_index"] = chunk.index
            chunk_metadata["total_chunks"] = len(chunks)
            chunk_metadata["token_count"] = chunk.token_count
            documents.append(
                {
                    "id": f"{effective_doc_id}::{chunk.index}",
                    "text": chunk.text,
                    "metadata": chunk_metadata,
                }
            )
        stored_ids = self._store.add_documents(collection, documents)
        report.files_processed = 1
        report.chunks_created = len(stored_ids)
        report.chunks_deleted = deleted
        return report

    def ingest_file(
        self,
        collection: str,
        path: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionReport:
        """Parse and store a single file."""
        report = IngestionReport()
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            report.files_failed = 1
            report.errors.append(f"{file_path}: file not found")
            return report
        try:
            text = load_document_text(file_path)
        except Exception as exc:  # 解析失败记录错误而不是中断整批摄入
            report.files_failed = 1
            report.errors.append(f"{file_path}: {exc}")
            return report
        base_metadata: dict[str, Any] = dict(metadata or {})
        base_metadata.setdefault("source", str(file_path))
        try:
            base_metadata.setdefault("file_mtime", int(file_path.stat().st_mtime))
        except OSError:
            pass
        inner = self.ingest_text(collection, text, metadata=base_metadata, source=str(file_path))
        report.merge(inner)
        return report

    def ingest_directory(
        self,
        collection: str,
        directory: str | Path,
        *,
        recursive: bool = True,
        extensions: Iterable[str] | None = None,
    ) -> IngestionReport:
        """Ingest a directory incrementally (hash-based skip for unchanged files)."""
        report = IngestionReport()
        directory_path = Path(directory)
        if not directory_path.is_dir():
            report.files_failed = 1
            report.errors.append(f"{directory_path}: directory not found")
            return report
        allowed = (
            {extension.lower() for extension in extensions}
            if extensions
            else set(SUPPORTED_EXTENSIONS)
        )
        pattern = "**/*" if recursive else "*"
        state = self._load_state()
        seen_keys: set[str] = set()
        for file_path in sorted(
            candidate for candidate in directory_path.glob(pattern) if candidate.is_file()
        ):
            if file_path.suffix.lower() not in allowed:
                continue
            state_key = f"{collection}::{file_path.as_posix()}"
            seen_keys.add(state_key)
            try:
                digest = file_hash(file_path)
            except OSError as exc:
                report.files_failed += 1
                report.errors.append(f"{file_path}: {exc}")
                continue
            entry = state.get(state_key)
            if isinstance(entry, dict) and entry.get("hash") == digest:
                report.files_skipped += 1
                continue
            file_report = self.ingest_file(collection, file_path)
            report.merge(file_report)
            if file_report.files_failed == 0 and not file_report.errors:
                state[state_key] = {
                    "hash": digest,
                    "path": str(file_path),
                    "chunks": file_report.chunks_created,
                    "updated_at": time.time(),
                }
        #清理已删除文件的陈旧块与状态记录
        stale_keys = [
            key for key in list(state) if key.startswith(f"{collection}::") and key not in seen_keys
        ]
        for key in stale_keys:
            entry = state.get(key)
            path_str = str(entry.get("path")) if isinstance(entry, dict) and entry.get("path") else key.split("::", 1)[1]
            report.chunks_deleted += self._store.delete_documents(collection, where={"source": path_str})
            state.pop(key, None)
        self._save_state(state)
        return report

    # ------------------------------------------------------------------状态

    def _state_file(self) -> Path:
        if self._state_path is not None:
            return self._state_path
        return get_data_dir() / "rag" / "ingest_state.json"

    def _load_state(self) -> dict[str, Any]:
        if self._state_loaded:
            return self._state
        self._state_loaded = True
        path = self._state_file()
        if not path.exists():
            return self._state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._state
        entries = raw.get("files") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            self._state = dict(entries)
        return self._state

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"version": 1, "files": state}, ensure_ascii=False, indent=2))
