"""Indexing: from a stored document version to searchable, embedded passages (S2-06, ADR-008).

Plain language: *indexing* a version means cutting the text that ingestion extracted into passages
(chunks), turning each passage into 384 numbers with the embedding model, and saving them. After
that the database can find the passages closest in meaning to a question.

Rules, so a failure at any step leaves nothing half-done:
- Only text of an **eligible** source is indexed (ADR-002).
- A version whose pages have no text (a scan; OCR is not available) is **skipped and reported**, not
  silently treated as indexed.
- All passages of a version are written together or not at all. Nothing is committed here: the
  caller commits after recording the audit event, as in ingestion.
- Indexing the same version again with the same model changes nothing. Passages made by a
  different model are replaced, because vectors from two models cannot be compared.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pharma_ai.features.documents.chunking import TextChunk, chunk_pages
from pharma_ai.features.documents.extraction import PageText
from pharma_ai.infrastructure.db.models import Chunk, Document, DocumentVersion, Source
from pharma_ai.infrastructure.db.repositories import ChunkInput, ChunkRepository
from pharma_ai.infrastructure.embeddings import SPECIAL_TOKENS, Embedder
from pharma_ai.infrastructure.storage import ObjectNotFoundError, ObjectStore

IndexStatus = Literal["indexed", "reindexed", "already_indexed", "skipped"]


class IndexingError(Exception):
    """A request that was refused. ``code`` is a short stable name for logs and audit details."""

    code = "indexing_error"


class VersionNotFound(IndexingError):
    code = "version_not_found"


class SourceNotEligible(IndexingError):
    code = "source_not_eligible"


class ExtractedTextMissing(IndexingError):
    code = "extracted_text_missing"


class ChunkSettingsInvalid(IndexingError):
    code = "chunk_settings_invalid"


@dataclass(frozen=True)
class IndexingResult:
    version_id: uuid.UUID
    source_id: str
    version_number: int
    sha256: str
    status: IndexStatus
    chunk_count: int  # chunks stored for the version after this call
    previous_chunk_count: int  # chunks it had before
    embedding_model: str
    max_tokens: int
    overlap_tokens: int
    reason: str | None = None  # why it was skipped


class DocumentIndexingService:
    def __init__(
        self,
        session: Session,
        store: ObjectStore,
        embedder: Embedder,
        *,
        max_tokens: int = 500,
        overlap_tokens: int = 100,
    ) -> None:
        # The model silently cuts off text beyond its limit, which would hide the end of a passage.
        limit = embedder.max_input_tokens - SPECIAL_TOKENS
        if max_tokens > limit:
            raise ChunkSettingsInvalid(
                f"chunk size {max_tokens} tokens is more than {embedder.model_name} reads "
                f"in full ({limit})"
            )
        self._session = session
        self._store = store
        self._embedder = embedder
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens
        self._chunks = ChunkRepository(session)

    def index_version(self, version_id: uuid.UUID, *, force: bool = False) -> IndexingResult:
        version = self._session.get(DocumentVersion, version_id)
        if version is None:
            raise VersionNotFound(f"unknown document version: {version_id}")
        document = self._session.get(Document, version.document_id)
        source = self._session.get(Source, document.source_id) if document else None
        if document is None or source is None:
            raise VersionNotFound(f"document version {version_id} has no source")
        if not source.eligible:
            raise SourceNotEligible(f"source {source.source_id} is not eligible for indexing")

        def result(status: IndexStatus, count: int, previous: int, reason: str | None = None):
            return IndexingResult(
                version_id=version.version_id,
                source_id=source.source_id,
                version_number=version.version_number,
                sha256=version.sha256,
                status=status,
                chunk_count=count,
                previous_chunk_count=previous,
                embedding_model=self._embedder.model_name,
                max_tokens=self._max_tokens,
                overlap_tokens=self._overlap_tokens,
                reason=reason,
            )

        existing, other_model, unembedded = self._existing(version_id)
        if existing and not force and not other_model and not unembedded:
            return result("already_indexed", existing, existing)

        pages = self._load_pages(version)
        if version.extraction_status == "failed" or not any(page.text for page in pages):
            return result(
                "skipped",
                existing,
                existing,
                "no selectable text on any page (a scan?); OCR is not available",
            )

        chunks = chunk_pages(
            pages,
            self._embedder.count_tokens,
            max_tokens=self._max_tokens,
            overlap_tokens=self._overlap_tokens,
        )
        if not chunks:
            return result("skipped", existing, existing, "the text produced no passages")
        vectors = self._embedder.embed([chunk.text for chunk in chunks])

        # Replace whatever was there only now that the new passages exist in memory.
        self._session.execute(delete(Chunk).where(Chunk.version_id == version_id))
        self._chunks.add_chunks(
            version_id, [self._row(c, v) for c, v in zip(chunks, vectors, strict=True)]
        )
        return result("reindexed" if existing else "indexed", len(chunks), existing)

    def _existing(self, version_id: uuid.UUID) -> tuple[int, int, int]:
        """(chunks stored, how many by another model, how many without a vector)."""
        total, other, missing = self._session.execute(
            select(
                func.count(),
                func.count().filter(Chunk.embedding_model != self._embedder.model_name),
                func.count().filter(Chunk.embedding.is_(None)),
            ).where(Chunk.version_id == version_id)
        ).one()
        return total, other, missing

    def _load_pages(self, version: DocumentVersion) -> list[PageText]:
        if not version.extracted_key:
            raise ExtractedTextMissing(f"version {version.version_id} has no extracted text")
        try:
            payload = json.loads(self._store.get(version.extracted_key))
        except ObjectNotFoundError as exc:
            raise ExtractedTextMissing(f"{version.extracted_key} is not in the store") from exc
        return [PageText(int(p["page"]), p["text"]) for p in payload["pages"]]

    def _row(self, chunk: TextChunk, vector: list[float]) -> ChunkInput:
        return ChunkInput(
            chunk_index=chunk.index,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            text=chunk.text,
            section=chunk.section,
            chunk_type=chunk.chunk_type,
            token_count=chunk.token_count,
            embedding=vector,
            embedding_model=self._embedder.model_name,
        )
