"""Local knowledge import entry points for RAG collections."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.rag.ingestion import (
    SUPPORTED_EXTENSIONS,
    DocumentIngestor,
    IngestionReport,
    MetadataEnricher,
    TextChunker,
)
from openharness.rag.vectorstore import VectorStore


@dataclass(frozen=True)
class LocalImportOptions:
    """Options for a local knowledge import run."""

    collection: str = "knowledge"
    recursive: bool = True
    tags: tuple[str, ...] = ()
    category: str = ""
    extensions: tuple[str, ...] = SUPPORTED_EXTENSIONS


class LocalKnowledgeImporter:
    """Import PDF/Markdown/JSON/TXT/HTML files into a vector collection."""

    def __init__(
        self,
        store: VectorStore,
        *,
        state_path: str | Path | None = None,
        chunker: TextChunker | None = None,
        enricher: MetadataEnricher | None = None,
        max_chunks_per_document: int = 400,
    ) -> None:
        self._ingestor = DocumentIngestor(
            store,
            state_path=state_path,
            chunker=chunker,
            enricher=enricher,
            max_chunks_per_document=max_chunks_per_document,
        )

    def import_directory(
        self,
        directory: str | Path,
        *,
        collection: str = "knowledge",
        recursive: bool = True,
        tags: Iterable[str] | None = None,
        category: str = "",
        extensions: Iterable[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionReport:
        """Recursively import supported local documents.

        Tags and category are persisted as metadata on every chunk and included
        in incremental state so changing labels refreshes existing files.
        """

        base_metadata: dict[str, Any] = dict(metadata or {})
        clean_tags = [tag.strip() for tag in tags or () if tag and tag.strip()]
        if clean_tags:
            base_metadata["tags"] = clean_tags
        if category.strip():
            base_metadata["category"] = category.strip()
        return self._ingestor.ingest_directory(
            collection,
            directory,
            recursive=recursive,
            extensions=extensions or SUPPORTED_EXTENSIONS,
            metadata=base_metadata,
        )

    def import_file(
        self,
        path: str | Path,
        *,
        collection: str = "knowledge",
        tags: Iterable[str] | None = None,
        category: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionReport:
        """Import one supported local document."""

        base_metadata: dict[str, Any] = dict(metadata or {})
        clean_tags = [tag.strip() for tag in tags or () if tag and tag.strip()]
        if clean_tags:
            base_metadata["tags"] = clean_tags
        if category.strip():
            base_metadata["category"] = category.strip()
        return self._ingestor.ingest_file(collection, path, metadata=base_metadata)
