"""Project-scoped repositories (S1-07).

"Scoped" means every read that returns project content takes the project IDs the caller is
allowed to use, and the filter is part of the SQL query itself. Nothing is fetched and filtered
afterwards, and an empty list of allowed projects returns nothing (fail closed).

The repositories do not commit. The caller owns the transaction (``session.commit()``).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.models import (
    EMBEDDING_DIM,
    Chunk,
    Document,
    DocumentVersion,
    Project,
    ProjectDocument,
    Source,
    WorkflowRun,
)


def _newest_versions() -> Any:
    """A subquery giving the highest version number of every document.

    Joined on (document_id, version_number) it keeps only each document's newest version.
    """
    return (
        select(
            DocumentVersion.document_id.label("document_id"),
            func.max(DocumentVersion.version_number).label("version_number"),
        )
        .group_by(DocumentVersion.document_id)
        .subquery()
    )


MAX_KEYWORD_TERMS = 32
# Keyword search ignores a question word that appears in more than this share of the passages the
# caller can see: "drug" or "products" says little, "M13A" or "solubility" says a lot. Measured on
# the golden questions (ADR-009): 15% worked best; 10% and 20% were slightly worse.
COMMON_WORD_SHARE = 0.15
# But at least this many words are always kept (the rarest ones), so a question made only of
# common words still searches.
MIN_KEPT_TERMS = 3
# With fewer visible passages than this, how common a word is means little, so every word is kept.
MIN_PASSAGES_FOR_RARITY = 100


def keyword_terms(text: str) -> list[str]:
    """The plain words of a question, for keyword search.

    Only letters and digits survive, lower-cased, without repeats and without single characters,
    so nothing typed into a question can act as search syntax, and an over-long question cannot
    make an over-long query. ("or" is dropped because the search joins the words with "or".)
    """
    seen: dict[str, None] = {}
    for word in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(word) > 1 and word != "or":
            seen.setdefault(word)
    return list(seen)[:MAX_KEYWORD_TERMS]


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
        extraction_method: str | None = None,
        extraction_status: str | None = None,
        extraction_notes: Iterable[str] = (),
        extracted_key: str | None = None,
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
            extraction_method=extraction_method,
            extraction_status=extraction_status,
            extraction_notes=list(extraction_notes),
            extracted_key=extracted_key,
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

    def latest_versions_for_project(
        self, project_id: str, allowed_project_ids: Iterable[str]
    ) -> list[tuple[Document, Source, DocumentVersion]]:
        """Each document linked to the project with its source and newest version.

        Returns nothing unless the caller may access the project.
        """
        if project_id not in set(allowed_project_ids):
            return []
        newest = _newest_versions()
        stmt = (
            select(Document, Source, DocumentVersion)
            .join(ProjectDocument, ProjectDocument.document_id == Document.document_id)
            .join(Source, Source.source_id == Document.source_id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.document_id)
            .join(
                newest,
                and_(
                    newest.c.document_id == DocumentVersion.document_id,
                    newest.c.version_number == DocumentVersion.version_number,
                ),
            )
            .where(ProjectDocument.project_id == project_id)
            .order_by(Document.source_id)
        )
        return [
            (document, source, version) for document, source, version in self._session.execute(stmt)
        ]

    def versions_for_documents(
        self, document_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, list[DocumentVersion]]:
        """All versions for each given document_id, ordered newest first."""
        ids = list(document_ids)
        if not ids:
            return {}
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(ids))
            .order_by(DocumentVersion.document_id, DocumentVersion.version_number.desc())
        )
        grouped: dict[uuid.UUID, list[DocumentVersion]] = {doc_id: [] for doc_id in ids}
        for ver in self._session.scalars(stmt):
            grouped.setdefault(ver.document_id, []).append(ver)
        return grouped


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
    # Set by the vector search: cosine distance, 0 = same direction, larger = less similar.
    distance: float | None = None
    # Set by the keyword search: how well the words match (higher is better; only comparable
    # within one search).
    keyword_score: float | None = None
    # The document's title from the source register, so a citation can name it.
    title: str | None = None


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

    def _visible_chunks(
        self,
        projects: list[str],
        embedding_model: str,
        latest_only: bool,
        *extra_columns: Any,
    ) -> Any:
        """The chunks a caller may search, as one SQL statement that the searches build on.

        Every rule that decides what the caller may see is here, so the vector search and the
        keyword search cannot disagree: the caller's projects, an eligible source, a chunk that
        has been embedded by the named model, and (by default) the newest version of the document.
        """
        has_access = exists().where(
            ProjectDocument.document_id == Document.document_id,
            ProjectDocument.project_id.in_(projects),
        )
        stmt = (
            select(Chunk, DocumentVersion, Source, *extra_columns)
            .join(DocumentVersion, DocumentVersion.version_id == Chunk.version_id)
            .join(Document, Document.document_id == DocumentVersion.document_id)
            .join(Source, Source.source_id == Document.source_id)
            .where(
                Chunk.embedding.is_not(None),
                Chunk.embedding_model == embedding_model,
                Source.eligible.is_(True),
                has_access,
            )
        )
        if latest_only:
            newest = _newest_versions()
            stmt = stmt.join(
                newest,
                and_(
                    newest.c.document_id == DocumentVersion.document_id,
                    newest.c.version_number == DocumentVersion.version_number,
                ),
            )
        return stmt

    @staticmethod
    def _hit(
        chunk: Chunk,
        version: DocumentVersion,
        source: Source,
        *,
        distance: float | None = None,
        keyword_score: float | None = None,
    ) -> ChunkHit:
        return ChunkHit(
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
            distance=distance,
            keyword_score=keyword_score,
            title=source.title,
        )

    def search_nearest(
        self,
        query_embedding: Sequence[float],
        allowed_project_ids: Iterable[str],
        *,
        embedding_model: str,
        limit: int = 5,
        latest_only: bool = True,
    ) -> list[ChunkHit]:
        """Return the closest chunks the caller may see.

        The permission filter and the eligibility check are in the same SQL statement as the
        vector search, so unauthorised text never leaves the database. With no allowed projects
        the result is empty.

        ``embedding_model`` is required: a question's numbers only mean something next to numbers
        made by the same model, so chunks made by any other model are never compared. By default
        only the newest version of each document is searched; ``latest_only=False`` includes the
        older versions too (they stay in the database so earlier citations still resolve).
        """
        projects = list(allowed_project_ids)
        if not projects:
            return []
        if len(query_embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"query embedding has {len(query_embedding)} values, expected {EMBEDDING_DIM}"
            )
        distance: Any = Chunk.embedding.cosine_distance(list(query_embedding))
        stmt = (
            self._visible_chunks(projects, embedding_model, latest_only, distance.label("distance"))
            .order_by(distance, Chunk.chunk_id)
            .limit(limit)
        )
        return [
            self._hit(chunk, version, source, distance=float(dist))
            for chunk, version, source, dist in self._session.execute(stmt)
        ]

    def _informative_terms(
        self, terms: list[str], projects: list[str], embedding_model: str, latest_only: bool
    ) -> list[str]:
        """The question words worth searching for: those that are not in too many passages.

        How common a word is is counted over the passages the caller can see (the same rules as
        the search itself), never over the whole corpus, so a word's frequency cannot reveal
        anything about documents the caller may not read. A word that occurs in no visible
        passage is dropped. With few visible passages every word is kept (see
        ``MIN_PASSAGES_FOR_RARITY``).
        """
        counts = [
            func.count().filter(Chunk.search_vector.op("@@")(func.plainto_tsquery("english", term)))
            for term in terms
        ]
        stmt = self._visible_chunks(projects, embedding_model, latest_only).with_only_columns(
            func.count(), *counts, maintain_column_froms=True
        )
        total, *frequencies = self._session.execute(stmt).one()
        if total < MIN_PASSAGES_FOR_RARITY:
            return terms
        present = [(term, n) for term, n in zip(terms, frequencies, strict=True) if n > 0]
        kept = [term for term, n in present if n <= COMMON_WORD_SHARE * total]
        if len(kept) < MIN_KEPT_TERMS:
            rarest = sorted(present, key=lambda item: item[1])[:MIN_KEPT_TERMS]
            kept = [term for term, _ in rarest]
        return [term for term in terms if term in kept]  # keep the question's own order

    def search_keywords(
        self,
        query_text: str,
        allowed_project_ids: Iterable[str],
        *,
        embedding_model: str,
        limit: int = 5,
        latest_only: bool = True,
    ) -> list[ChunkHit]:
        """Return the chunks whose words best match the question's words.

        Plain language: this finds exact terms (a guideline code such as "M13A", a substance
        name) that search by meaning can miss. A chunk matches if it has *any* of the question's
        words (after stemming, so "limits" matches "limit"), and chunks with more of them, close
        together, rank higher. It follows exactly the same visibility rules as ``search_nearest``
        (see ``_visible_chunks``), so it cannot return anything that search could not.

        PostgreSQL ranks by how many question words a chunk has and how close together they are,
        but not by how rare a word is, so words that appear in many passages are left out first
        (``_informative_terms``); otherwise a long question matches dozens of unrelated passages.
        The question is turned into plain words first, so nothing the user types can act as
        search syntax.
        """
        projects = list(allowed_project_ids)
        terms = keyword_terms(query_text)
        if not projects or not terms:
            return []
        terms = self._informative_terms(terms, projects, embedding_model, latest_only)
        if not terms:
            return []  # none of the question's words occurs in any passage the caller can see
        query = func.websearch_to_tsquery("english", " or ".join(terms))
        rank: Any = func.ts_rank_cd(Chunk.search_vector, query, 1)
        stmt = (
            self._visible_chunks(projects, embedding_model, latest_only, rank.label("rank"))
            .where(Chunk.search_vector.op("@@")(query))
            .order_by(rank.desc(), Document.source_id, Chunk.chunk_index)
            .limit(limit)
        )
        return [
            self._hit(chunk, version, source, keyword_score=float(score))
            for chunk, version, source, score in self._session.execute(stmt)
        ]


def new_run_id() -> str:
    """A fresh run ID, for example ``RUN-1A2B3C4D5E6F`` (48 random bits; the primary key refuses a repeat)."""
    return "RUN-" + uuid.uuid4().hex[:12].upper()


@dataclass(frozen=True)
class RunInput:
    """Everything stored when an answer is given (see ``WorkflowRun``)."""

    run_id: str
    project_id: str
    asked_by: str
    asked_by_email: str | None
    question: str
    question_sha256: str
    answer: str
    answer_mode: str
    verified: bool
    coverage: float
    evidence_gaps: list[Any]
    citations: list[Any]
    retrieved: list[Any]
    embedding_model: str


class WorkflowRunRepository:
    """Stored answers and their review status. Reads are always inside one project."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, data: RunInput) -> WorkflowRun:
        run = WorkflowRun(
            run_id=data.run_id,
            project_id=data.project_id,
            asked_by=data.asked_by,
            asked_by_email=data.asked_by_email,
            question=data.question,
            question_sha256=data.question_sha256,
            answer=data.answer,
            answer_mode=data.answer_mode,
            verified=data.verified,
            coverage=data.coverage,
            evidence_gaps=list(data.evidence_gaps),
            citations=list(data.citations),
            retrieved=list(data.retrieved),
            embedding_model=data.embedding_model,
        )
        self._session.add(run)
        self._session.flush()
        self._session.refresh(run)  # picks up the status default and the database's timestamp
        return run

    def get_in_project(self, run_id: str, project_id: str) -> WorkflowRun | None:
        """The run, only if it belongs to ``project_id`` (a run of another project is not found)."""
        stmt = select(WorkflowRun).where(
            WorkflowRun.run_id == run_id, WorkflowRun.project_id == project_id
        )
        return self._session.scalars(stmt).one_or_none()

    def record_decision(self, run: WorkflowRun, *, status: str, decided_by: str) -> None:
        """Move the run to the reviewer's latest decision. The caller commits."""
        run.status = status
        run.decided_by = decided_by
        run.decided_at = datetime.now(UTC)
        self._session.flush()
