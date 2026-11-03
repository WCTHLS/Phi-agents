"""Document ingestion: from an uploaded PDF to a stored, fingerprinted, text-extracted version.

Plain language: *ingesting* a document means taking a file we are allowed to use, keeping an
unchangeable copy of it, recording its fingerprint (SHA-256, a short code that changes if even one
byte of the file changes), and reading its text page by page so it can be searched later.

Order of work, chosen so a failure at any step leaves nothing half-done (ADR-007):
1. Check the request (size, looks like a PDF) and that the source exists and is *eligible*
   (ADR-002). A held document is refused here, and the database would refuse it again.
2. Read the text. A file that cannot be read at all is refused before anything is stored.
3. Store the original and the extracted text in the object store. Both are named after the file's
   own fingerprint, so storing the same file twice changes nothing.
4. Write the database rows. The caller commits, after recording the audit event.

Nothing is committed here: the caller decides when, so the audit trail can be written first.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pharma_ai.features.documents.extraction import (
    ExtractionError,
    ExtractionResult,
    extract_pdf,
    looks_like_pdf,
)
from pharma_ai.infrastructure.db.models import Document, DocumentVersion, Project, ProjectDocument
from pharma_ai.infrastructure.db.repositories import DocumentRepository, SourceRepository
from pharma_ai.infrastructure.storage import ObjectStore


class IngestionError(Exception):
    """A request that was refused. ``code`` is a short stable name for logs and audit details."""

    code = "ingestion_error"


class EmptyFile(IngestionError):
    code = "empty_file"


class FileTooLarge(IngestionError):
    code = "file_too_large"


class NotAPdf(IngestionError):
    code = "not_a_pdf"


class UnreadablePdf(IngestionError):
    code = "unreadable_pdf"


class SourceNotFound(IngestionError):
    code = "source_not_found"


class SourceNotEligible(IngestionError):
    code = "source_not_eligible"


class ProjectNotFound(IngestionError):
    code = "project_not_found"


@dataclass(frozen=True)
class IngestionResult:
    document_id: uuid.UUID
    version_id: uuid.UUID
    source_id: str
    version_number: int
    version_label: str | None
    sha256: str
    byte_size: int
    page_count: int | None
    text_layer: str | None
    extraction_method: str | None
    extraction_status: str | None
    extraction_notes: tuple[str, ...]
    object_key: str
    extracted_key: str | None
    already_ingested: bool  # True if this exact file was ingested before: nothing new was written
    project_ids: tuple[str, ...] = field(default=())


def original_key(source_id: str, sha256: str) -> str:
    return f"originals/{source_id}/{sha256}.pdf"


def extracted_key(source_id: str, sha256: str) -> str:
    return f"extracted/{source_id}/{sha256}.json"


def _result_from(
    document: Document,
    version: DocumentVersion,
    project_ids: Sequence[str],
    *,
    already_ingested: bool,
) -> IngestionResult:
    return IngestionResult(
        document_id=document.document_id,
        version_id=version.version_id,
        source_id=document.source_id,
        version_number=version.version_number,
        version_label=version.version_label,
        sha256=version.sha256,
        byte_size=version.byte_size,
        page_count=version.page_count,
        text_layer=version.text_layer,
        extraction_method=version.extraction_method,
        extraction_status=version.extraction_status,
        extraction_notes=tuple(version.extraction_notes or ()),
        object_key=version.object_key,
        extracted_key=version.extracted_key,
        already_ingested=already_ingested,
        project_ids=tuple(project_ids),
    )


class DocumentIngestionService:
    def __init__(
        self,
        session: Session,
        store: ObjectStore,
        *,
        max_bytes: int,
        extractor: Callable[[bytes], ExtractionResult] = extract_pdf,
    ) -> None:
        self._session = session
        self._store = store
        self._max_bytes = max_bytes
        self._extract = extractor
        self._documents = DocumentRepository(session)
        self._sources = SourceRepository(session)

    def ingest(
        self,
        *,
        source_id: str,
        data: bytes,
        project_ids: Sequence[str] = (),
        version_label: str | None = None,
        retrieved_at: datetime | None = None,
    ) -> IngestionResult:
        # 1. Cheap checks first, before any work is done on the file.
        if not data:
            raise EmptyFile("the file is empty")
        if len(data) > self._max_bytes:
            raise FileTooLarge(f"the file is larger than the limit of {self._max_bytes} bytes")
        if not looks_like_pdf(data):
            raise NotAPdf("the file does not start like a PDF")

        source = self._sources.get(source_id)
        if source is None:
            raise SourceNotFound(f"unknown source: {source_id}")
        if not source.eligible:
            raise SourceNotEligible(
                f"source {source_id} is not eligible for ingestion"
                + (f" ({source.hold_reason})" if source.hold_reason else "")
            )
        projects = list(dict.fromkeys(project_ids))
        if projects:
            known = set(
                self._session.scalars(
                    select(Project.project_id).where(Project.project_id.in_(projects))
                )
            )
            missing = [p for p in projects if p not in known]
            if missing:
                raise ProjectNotFound(f"unknown project: {', '.join(missing)}")

        sha256 = hashlib.sha256(data).hexdigest()
        document = self._session.scalar(select(Document).where(Document.source_id == source_id))

        # The same file again: nothing new to store. Only make sure the projects can see it.
        if document is not None:
            existing = self._session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.document_id,
                    DocumentVersion.sha256 == sha256,
                )
            )
            if existing is not None:
                self._grant(document, projects)
                return _result_from(document, existing, projects, already_ingested=True)

        # 2. Read the text before storing anything: an unreadable file leaves no trace.
        try:
            extraction = self._extract(data)
        except ExtractionError as exc:
            raise UnreadablePdf(str(exc)) from exc

        # 3. Files first. They are named after the fingerprint, so a retry after a later
        #    failure writes the same bytes to the same names and changes nothing.
        object_key = original_key(source_id, sha256)
        text_key = extracted_key(source_id, sha256)
        self._store.put(object_key, data)
        self._store.put(text_key, json.dumps(extraction.to_dict(), ensure_ascii=False).encode())

        # 4. Database rows.
        if document is None:
            document = self._documents.register_document(source_id)
            next_number = 1
        else:
            highest = self._session.scalar(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == document.document_id
                )
            )
            next_number = (highest or 0) + 1
        version = self._documents.add_version(
            document_id=document.document_id,
            version_number=next_number,
            version_label=version_label or source.version_label,
            sha256=sha256,
            object_key=object_key,
            byte_size=len(data),
            retrieved_at=retrieved_at or datetime.now(UTC),
            page_count=extraction.page_count,
            text_layer=extraction.text_layer,
            extraction_method=extraction.method,
            extraction_status=extraction.status,
            extraction_notes=extraction.notes,
            extracted_key=text_key,
        )
        self._grant(document, projects)
        return _result_from(document, version, projects, already_ingested=False)

    def _grant(self, document: Document, project_ids: Sequence[str]) -> None:
        """Link the document to the projects (skipping links that already exist)."""
        if not project_ids:
            return
        already = set(
            self._session.scalars(
                select(ProjectDocument.project_id).where(
                    ProjectDocument.document_id == document.document_id,
                    ProjectDocument.project_id.in_(project_ids),
                )
            )
        )
        for project_id in project_ids:
            if project_id not in already:
                self._documents.grant_project_access(document.document_id, project_id)
