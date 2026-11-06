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

*Re-extraction* reads a stored original again with the current extractor (for example after
superscripts began to be kept) and points the version at the new text. The original is never
touched and stored objects are never overwritten: the new text is stored under a new name and the
old one is kept, so passages cut from it can still be traced.
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
from pharma_ai.infrastructure.storage.base import ObjectNotFoundError


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


class VersionNotFound(IngestionError):
    code = "version_not_found"


class StoredOriginalUnusable(IngestionError):
    """The stored original is missing or no longer matches its recorded fingerprint."""

    code = "stored_original_unusable"


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


def store_extracted_text(store: ObjectStore, source_id: str, sha256: str, payload: bytes) -> str:
    """Store a file's extracted text and return its key.

    The first text read from a file is named after the file's fingerprint. A different text read
    later from the same file (a newer extractor) is named after the text's own fingerprint as
    well, because a stored object is never overwritten.
    """
    key = extracted_key(source_id, sha256)
    if store.exists(key) and store.get(key) != payload:
        text_sha = hashlib.sha256(payload).hexdigest()[:16]
        key = f"extracted/{source_id}/{sha256}.{text_sha}.json"
    store.put(key, payload)
    return key


@dataclass(frozen=True)
class ReextractionResult:
    version_id: uuid.UUID
    source_id: str
    version_number: int
    sha256: str
    changed: bool  # False if the text read now is exactly the stored text
    changed_pages: tuple[int, ...]  # pages whose text differs from the stored text
    previous_key: str | None
    extracted_key: str | None  # the key now in use (unchanged on a dry run)
    extraction_method: str
    extraction_status: str


def _payload(extraction: ExtractionResult) -> bytes:
    return json.dumps(extraction.to_dict(), ensure_ascii=False).encode()


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
        self._store.put(object_key, data)
        text_key = store_extracted_text(self._store, source_id, sha256, _payload(extraction))

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

    def reextract(self, version_id: uuid.UUID, *, dry_run: bool = False) -> ReextractionResult:
        """Read a stored original again and point its version at the new text, if it differs.

        The passages are not cut again here: run the indexing step with ``force`` afterwards.
        Nothing is committed; on a dry run nothing is stored or changed either.
        """
        version = self._session.get(DocumentVersion, version_id)
        document = self._session.get(Document, version.document_id) if version else None
        if version is None or document is None:
            raise VersionNotFound(f"unknown document version: {version_id}")
        source = self._sources.get(document.source_id)
        if source is None or not source.eligible:
            raise SourceNotEligible(f"source {document.source_id} is not eligible")
        try:
            data = self._store.get(version.object_key)
        except ObjectNotFoundError as exc:
            raise StoredOriginalUnusable(f"{version.object_key} is not in the store") from exc
        if hashlib.sha256(data).hexdigest() != version.sha256:
            raise StoredOriginalUnusable(f"{version.object_key} no longer matches its fingerprint")
        try:
            extraction = self._extract(data)
        except ExtractionError as exc:
            raise UnreadablePdf(str(exc)) from exc

        payload = _payload(extraction)
        stored = self._stored_text(version.extracted_key)
        before = {}
        if stored is not None:
            before = {int(p["page"]): p["text"] for p in json.loads(stored).get("pages", [])}
        changed_pages = tuple(p.page for p in extraction.pages if before.get(p.page) != p.text)
        changed = stored != payload
        previous = version.extracted_key
        if changed and not dry_run:
            version.extracted_key = store_extracted_text(
                self._store, document.source_id, version.sha256, payload
            )
            version.page_count = extraction.page_count
            version.text_layer = extraction.text_layer
            version.extraction_method = extraction.method
            version.extraction_status = extraction.status
            version.extraction_notes = list(extraction.notes)
            self._session.flush()
        return ReextractionResult(
            version_id=version.version_id,
            source_id=document.source_id,
            version_number=version.version_number,
            sha256=version.sha256,
            changed=changed,
            changed_pages=changed_pages,
            previous_key=previous,
            extracted_key=version.extracted_key,
            extraction_method=extraction.method,
            extraction_status=extraction.status,
        )

    def _stored_text(self, key: str | None) -> bytes | None:
        if not key:
            return None
        try:
            return self._store.get(key)
        except ObjectNotFoundError:
            return None

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
