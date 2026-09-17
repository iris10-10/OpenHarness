"""Tests for document ingestion: chunking, enrichment, parsing and incremental sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.rag.ingestion import (
    DocumentIngestor,
    MetadataEnricher,
    TextChunker,
    load_document_text,
)
from openharness.rag.vectorstore import VectorStore


def _word_para(prefix: str, words: int = 10) -> str:
    """Return a paragraph of ASCII words (one token each)."""
    return " ".join(f"{prefix}{index:02d}" for index in range(words))


def _write_minimal_pdf(path: Path, text: str = "Hello RAG PDF") -> None:
    """Write a single-page PDF with one text object (no external dependency)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
    out += f"{xref_offset}\n".encode("ascii") + b"%%EOF\n"
    path.write_bytes(bytes(out))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "ingest_state.json"


@pytest.fixture
def ingestor(store: VectorStore, state_path: Path) -> DocumentIngestor:
    return DocumentIngestor(
        store,
        chunker=TextChunker(max_tokens=60, overlap_tokens=10),
        state_path=state_path,
    )


# --------------------------------------------------------------------------- 分块


def test_chunker_semantic_packs_paragraphs_with_overlap() -> None:
    text = "\n\n".join(_word_para(name) for name in ("aa", "bb", "cc", "dd"))
    chunks = TextChunker(max_tokens=25, overlap_tokens=8).chunk(text)
    assert len(chunks) == 3
    assert all(chunk.token_count <= 25 for chunk in chunks)
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    # overlap: the paragraph that ended one chunk starts the next one
    assert "bb00" in chunks[0].text and "bb00" in chunks[1].text


def test_chunker_semantic_keeps_heading_with_its_section() -> None:
    section_a = "# 第一部分\n\n" + _word_para("aa", 20)
    section_b = "# 第二部分\n\n" + _word_para("bb", 20)
    chunks = TextChunker(max_tokens=25, overlap_tokens=0).chunk(f"{section_a}\n\n{section_b}")
    assert len(chunks) == 2
    assert chunks[0].text.startswith("# 第一部分")
    assert "aa00" in chunks[0].text and "bb00" not in chunks[0].text
    assert chunks[1].text.startswith("# 第二部分")


def test_chunker_fixed_splits_sentences() -> None:
    text = "第一句。第二句。第三句。第四句。"
    chunks = TextChunker(max_tokens=6, overlap_tokens=1, strategy="fixed").chunk(text)
    assert len(chunks) >= 2
    assert "第一句" in chunks[0].text
    joined = "".join(chunk.text for chunk in chunks)
    for sentence in ("第一句", "第二句", "第三句", "第四句"):
        assert sentence in joined


def test_chunker_hard_splits_oversized_paragraph() -> None:
    text = _word_para("w", 100)
    chunks = TextChunker(max_tokens=30, overlap_tokens=5, strategy="fixed").chunk(text)
    assert len(chunks) == 4
    assert all(chunk.token_count <= 30 for chunk in chunks)
    assert chunks[0].text.startswith("w00")
    assert chunks[-1].text.endswith("w99")


def test_chunker_empty_input_yields_no_chunks() -> None:
    assert TextChunker().chunk("   \n\n  ") == []


def test_chunker_validates_configuration() -> None:
    with pytest.raises(ValueError):
        TextChunker(max_tokens=0)
    with pytest.raises(ValueError):
        TextChunker(max_tokens=10, overlap_tokens=10)
    with pytest.raises(ValueError):
        TextChunker(strategy="paragraph")


# --------------------------------------------------------------------------- 元数据


def test_enricher_derives_metadata() -> None:
    text = "# 面试准备\n\nJava 并发编程经常考察线程池。"
    enriched = MetadataEnricher().enrich(source="docs/interview.md", text=text)
    assert enriched["source"] == "docs/interview.md"
    assert enriched["doc_type"] == "markdown"
    assert enriched["title"] == "面试准备"
    assert enriched["char_count"] == len(text)
    assert isinstance(enriched["keywords"], str) and enriched["keywords"]


def test_enricher_respects_caller_metadata() -> None:
    enriched = MetadataEnricher().enrich(
        source="a.txt",
        text="# 第一名\n\n正文",
        metadata={"title": "自定义", "source": "custom"},
    )
    assert enriched["title"] == "自定义"
    assert enriched["source"] == "custom"
    assert enriched["doc_type"] == "text"


def test_enricher_extracts_title_without_source() -> None:
    enriched = MetadataEnricher().enrich(source=None, text="## 标题二\n\n正文")
    assert enriched["title"] == "标题二"
    assert "doc_type" not in enriched


def test_extract_keywords_filters_stopwords_and_single_chars() -> None:
    keywords = MetadataEnricher(max_keywords=5).extract_keywords(
        "python python python 的的 java java 数据库"
    )
    assert keywords[0] == "python"
    assert "java" in keywords
    assert all(len(token) >= 2 for token in keywords)


# --------------------------------------------------------------------------- 解析


def test_load_text_and_markdown(tmp_path: Path) -> None:
    md = tmp_path / "notes.md"
    md.write_text("# 标题\n\n正文", encoding="utf-8")
    assert load_document_text(md) == "# 标题\n\n正文"
    txt = tmp_path / "plain.txt"
    txt.write_text("纯文本", encoding="utf-8")
    assert load_document_text(txt) == "纯文本"


def test_load_json_flattens_structure(tmp_path: Path) -> None:
    payload = {"name": "张三", "skills": ["Python", "SQL"], "meta": {"city": "北京"}}
    path = tmp_path / "data.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    text = load_document_text(path)
    assert "name: 张三" in text
    assert "skills:" in text
    assert "Python" in text and "SQL" in text
    assert "city: 北京" in text


def test_load_json_falls_back_to_raw_text(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_document_text(path) == "{not json"


def test_load_html_strips_tags(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        "<html><body><h1>标题</h1><p>正文段落</p><script>bad()</script></body></html>",
        encoding="utf-8",
    )
    text = load_document_text(path)
    assert "标题" in text and "正文段落" in text
    assert "<p>" not in text


def test_load_pdf_extracts_text(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    path = tmp_path / "doc.pdf"
    _write_minimal_pdf(path)
    text = load_document_text(path)
    assert "Hello" in text


# --------------------------------------------------------------------------- 摄入


def test_ingest_text_stores_enriched_chunks(ingestor: DocumentIngestor, store: VectorStore) -> None:
    text = "# 面试准备\n\n" + "Java 并发编程经常考察线程池与锁。" * 10
    report = ingestor.ingest_text("knowledge", text, metadata={"source": "interview.md"})
    assert report.files_processed == 1
    assert report.chunks_created >= 1
    assert report.chunks_deleted == 0

    docs = store.get_documents("knowledge", where={"source": "interview.md"})
    assert len(docs) == report.chunks_created
    assert {doc.metadata["chunk_index"] for doc in docs} == set(range(len(docs)))
    first = next(doc for doc in docs if doc.metadata["chunk_index"] == 0)
    assert first.id.startswith("interview.md::")
    assert first.metadata["doc_id"] == "interview.md"
    assert first.metadata["total_chunks"] == report.chunks_created
    assert first.metadata["doc_type"] == "markdown"
    assert first.metadata["title"] == "面试准备"
    assert first.metadata["token_count"] > 0


def test_ingest_text_replaces_previous_chunks(
    ingestor: DocumentIngestor, store: VectorStore
) -> None:
    source = {"source": "notes.md"}
    ingestor.ingest_text("knowledge", "# v1\n\n" + "旧内容。" * 30, metadata=source)
    old_docs = store.get_documents("knowledge", where={"source": "notes.md"})
    assert len(old_docs) >= 2

    report = ingestor.ingest_text("knowledge", "# v2\n\n短新内容。", metadata=source)
    docs = store.get_documents("knowledge", where={"source": "notes.md"})
    assert report.chunks_deleted == len(old_docs)
    assert {doc.id for doc in docs} == {f"notes.md::{index}" for index in range(len(docs))}
    assert all("旧内容" not in doc.text for doc in docs)


def test_ingest_text_empty_is_reported(ingestor: DocumentIngestor) -> None:
    report = ingestor.ingest_text("knowledge", "   \n  ")
    assert report.files_failed == 1
    assert report.errors


def test_ingest_file_missing_path(ingestor: DocumentIngestor, tmp_path: Path) -> None:
    report = ingestor.ingest_file("knowledge", tmp_path / "nope.md")
    assert report.files_failed == 1
    assert "file not found" in report.errors[0]


def test_ingest_file_reads_and_enriches(
    ingestor: DocumentIngestor, store: VectorStore, tmp_path: Path
) -> None:
    path = tmp_path / "jd.md"
    path.write_text("# Python 工程师\n\n负责后端开发与性能优化。", encoding="utf-8")
    report = ingestor.ingest_file("jobs", path)
    assert report.files_processed == 1
    docs = store.get_documents("jobs", where={"source": str(path)})
    assert docs
    assert docs[0].metadata["doc_type"] == "markdown"
    assert docs[0].metadata["title"] == "Python 工程师"
    assert docs[0].metadata["file_mtime"] > 0


def test_ingest_directory_is_incremental(
    ingestor: DocumentIngestor, store: VectorStore, state_path: Path, tmp_path: Path
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.md").write_text("# A\n\n第一个文档内容。", encoding="utf-8")
    (docs_dir / "b.txt").write_text("第二个文档内容。", encoding="utf-8")
    (docs_dir / "skip.bin").write_bytes(b"\x00\x01")

    first = ingestor.ingest_directory("knowledge", docs_dir)
    assert first.files_processed == 2
    assert first.files_skipped == 0
    assert store.count("knowledge") == first.chunks_created

    second = ingestor.ingest_directory("knowledge", docs_dir)
    assert second.files_processed == 0
    assert second.files_skipped == 2

    fresh = DocumentIngestor(
        store,
        chunker=TextChunker(max_tokens=60, overlap_tokens=10),
        state_path=state_path,
    )
    third = fresh.ingest_directory("knowledge", docs_dir)
    assert third.files_processed == 0
    assert third.files_skipped == 2
    assert state_path.exists()


def test_ingest_directory_replaces_changed_files(
    ingestor: DocumentIngestor, store: VectorStore, tmp_path: Path
) -> None:
    docs_dir = tmp_path / "docs_changed"
    docs_dir.mkdir()
    target = docs_dir / "a.md"
    target.write_text("# 旧标题\n\n旧内容。", encoding="utf-8")
    ingestor.ingest_directory("knowledge", docs_dir)
    assert store.get_documents("knowledge", where={"source": str(target)})

    target.write_text("# 新标题\n\n完全不同的新内容。", encoding="utf-8")
    report = ingestor.ingest_directory("knowledge", docs_dir)
    assert report.files_processed == 1
    docs = store.get_documents("knowledge", where={"source": str(target)})
    assert docs
    assert all(doc.metadata["title"] == "新标题" for doc in docs)
    assert all("旧内容" not in doc.text for doc in docs)


def test_ingest_directory_cleans_up_deleted_files(
    ingestor: DocumentIngestor, store: VectorStore, tmp_path: Path
) -> None:
    docs_dir = tmp_path / "docs_deleted"
    docs_dir.mkdir()
    keep = docs_dir / "keep.md"
    remove = docs_dir / "remove.md"
    keep.write_text("# 保留\n\n保留内容。", encoding="utf-8")
    remove.write_text("# 删除\n\n即将删除的内容。", encoding="utf-8")
    ingestor.ingest_directory("knowledge", docs_dir)
    assert store.get_documents("knowledge", where={"source": str(remove)})

    remove.unlink()
    report = ingestor.ingest_directory("knowledge", docs_dir)
    assert report.chunks_deleted >= 1
    assert store.get_documents("knowledge", where={"source": str(remove)}) == []
    assert store.get_documents("knowledge", where={"source": str(keep)})


def test_ingest_directory_missing_directory(ingestor: DocumentIngestor, tmp_path: Path) -> None:
    report = ingestor.ingest_directory("knowledge", tmp_path / "missing")
    assert report.files_failed == 1
    assert "directory not found" in report.errors[0]
