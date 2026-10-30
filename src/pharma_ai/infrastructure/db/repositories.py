"""Project-scoped repositories (S1-07).

"Scoped" means every read that returns project content takes the project IDs the caller is
allowed to use, and the filter is part of the SQL query itself. Nothing is fetched and filtered
afterwards, and an empty list of allowed projects returns nothing (fail closed).

The repositories do not commit. The caller owns the transaction (``session.commit()``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.models import (
    EMBEDDING_DIM,
    Chunk,
    Document,
    DocumentVersion,
    Project,
    ProjectDocument,
    Source,
)


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_accessible(self, allowed_project_ids: Iterable[str]) -> list[Project]:
        ids = list(allowed_project_ids)
        if not ids:
            return []
        stmt = select(Project).where(Project.project_id.in_(ids)).order_by(Project.project_id)
        return list(self._session.scalars(stmt))

    def get(self, project_id: str, allowed_project_ids: Iterable[str]) -> Project | None:
        """Return the project only if the caller may access it; otherwise None."""
        if project_id not in set(allowed_project_ids):
            return None
        return self._session.get(Project, project_id)


class SourceRepository:
    """The approved-source register is shared by all projects, so it is not project-scoped."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_eligible(self) -> list[Source]:
        stmt = select(Source).where(Source.eligible.is_(True)).order_by(Source.source_id)
        return list(self._session.scalars(stmt))

    def get(self, source_id: str) -> Source | None:
        return self._session.get(Source, source_id)


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def register_document(self, source_id: str) -> Document:
        """Create the document record for a source.

        The database raises an integrity error if the source is not eligible (ADR-002).
        """
        document = Document(source_id=source_id)
        self._session.add(document)
        self._session.flush()
        return document

    def add_version(
        self,
        *,
        document_id: uuid.UUID,
        version_number: int,
        sha256: str,
        object_key: str,
        byte_size: int,
        retrieved_at: datetime,
        version_label: str | None = None,
        page_count: int | None = None,
        text_layer: str | None = None,
    ) -> DocumentVersion:
        version = DocumentVersion(
            document_id=document_id,
            version_number=version_number,
            version_label=version_label,
            sha256=sha256,
            object_key=object_key,
            byte_size=byte_size,
            page_count=page_count,
            text_layer=text_layer,
            retrieved_at=retrieved_at,
        )
        self._session.add(version)
        self._session.flush()
        return version

    def grant_project_access(self, document_id: uuid.UUID, project_id: str) -> None:
        self._session.add(ProjectDocument(document_id=document_id, project_id=project_id))
        self._session.flush()

    def list_for_project(
        self, project_id: str, allowed_project_ids: Iterable[str]
    ) -> list[Document]:
        """Documents linked to the project, only if the caller may access that project."""
        if project_id not in set(allowed_project_ids):
            return []
        stmt = (
            select(Document)
            .join(ProjectDocument, ProjectDocument.document_id == Document.document_id)
            .where(ProjectDocument.project_id == project_id)
            .order_by(Document.source_id)
        )
        return list(self._session.scalars(stmt))


@dataclass(frozen=True)
class ChunkInput:
    """One chunk to store. ``embedding`` and ``embedding_model`` are set together or not at all."""

    chunk_index: int
    page_start: int
    page_end: int
    text: str
    section: str | None = None
    chunk_type: str = "paragraph"
    token_count: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    embedding: Sequence[float] | None = None
    embedding_model: str | None = None


@dataclass(frozen=True)
class ChunkHit:
    """A search result with everything a citation needs (ADR-002: passage, version, attribution)."""

    chunk_id: uuid.UUID
    source_id: str
    version_id: uuid.UUID
    version_label: str | None
    version_number: int
    sha256: str
    page_start: int
    page_end: int
    section: str | None
    text: str
    attribution: str | None
    distance: float  # cosine distance: 0 = same direction, larger = less similar


class ChunkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_chunks(self, version_id: uuid.UUID, chunks: Iterable[ChunkInput]) -> int:
        rows = []
        for c in chunks:
            if c.embedding is not None and len(c.embedding) != EMBEDDING_DIM:
                raise ValueError(
                    f"embedding has {len(c.embedding)} values, expected {EMBEDDING_DIM}"
                )
            rows.append(
                Chunk(
                    version_id=version_id,
                    chunk_index=c.chunk_index,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    section=c.section,
                    chunk_type=c.chunk_type,
                    text=c.text,
                    token_count=c.token_count,
                    char_start=c.char_start,
                    char_end=c.char_end,
                    embedding=list(c.embedding) if c.embedding is not None else None,
                    embedding_model=c.embedding_model,
                )
            )
        self._session.add_all(rows)
        self._session.flush()
        return len(rows)

    def search_nearest(
        self,
        query_embedding: Sequence[float],
        allowed_project_ids: Iterable[str],
        *,
        limit: int = 5,
    ) -> list[ChunkHit]:
        """Return the closest chunks the caller may see.

        The permission filter and the eligibility check are in the same SQL statement as the
        vector search, so unauthorised text never leaves the database. With no allowed projects
        the result is empty.
        """
        projects = list(allowed_project_ids)
        if not projects:
            return []
        if len(query_embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"query embedding has {len(query_embedding)} values, expected {EMBEDDING_DIM}"
            )
        distance: Any = Chunk.embedding.cosine_distance(list(query_embedding))
        has_access = exists().where(
            ProjectDocument.document_id == Document.document_id,
            ProjectDocument.project_id.in_(projects),
        )
        stmt = (
            select(Chunk, DocumentVersion, Source, distance.label("distance"))
            .join(DocumentVersion, DocumentVersion.version_id == Chunk.version_id)
            .join(Document, Document.document_id == DocumentVersion.document_id)
            .join(Source, Source.source_id == Document.source_id)
            .where(Chunk.embedding.is_not(None), Source.eligible.is_(True), has_access)
            .order_by(distance)
            .limit(limit)
        )
        hits = []
        for chunk, version, source, dist in self._session.execute(stmt):
            hits.append(
                ChunkHit(
                    chunk_id=chunk.chunk_id,
                    source_id=source.source_id,
                    version_id=version.version_id,
                    version_label=version.version_label,
                    version_number=version.version_number,
                    sha256=version.sha256,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section=chunk.section,
                    text=chunk.text,
                    attribution=source.attribution,
                    distance=float(dist),
                )
            )
        return hits
