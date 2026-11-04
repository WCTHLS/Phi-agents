"""Document endpoints: ingest an approved source, list a project's documents (S2-03, ADR-007).

- ``PUT /api/v1/sources/{source_id}/versions``: store a PDF as a new version of an approved source.
  Administrators only. The raw PDF is the request body (no form encoding needed).
- ``GET /api/v1/projects/{project_id}/documents``: the documents a project may search, with the
  attribution text that must accompany any citation of them (ADR-002). Project members only.

Every outcome is written to the audit trail, refusals included. If the trail cannot be written the
request fails with 503 and nothing is saved (ingestion is committed only after the audit event).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pharma_ai.core.config import settings
from pharma_ai.features.documents.ingestion import (
    DocumentIngestionService,
    EmptyFile,
    FileTooLarge,
    IngestionError,
    IngestionResult,
    NotAPdf,
    ProjectNotFound,
    SourceNotEligible,
    SourceNotFound,
    UnreadablePdf,
)
from pharma_ai.infrastructure.audit.recorder import AuditRecorder, AuditRecorderDep
from pharma_ai.infrastructure.db.models import DocumentVersion
from pharma_ai.infrastructure.db.repositories import DocumentRepository
from pharma_ai.infrastructure.db.session import get_db_session
from pharma_ai.infrastructure.security.auth import (
    CurrentPrincipal,
    ensure_project_access,
    require_role,
)
from pharma_ai.infrastructure.storage import ObjectStore, get_object_store

router = APIRouter(prefix="/api/v1", tags=["documents"])

SessionDep = Annotated[Session, Depends(get_db_session)]
StoreDep = Annotated[ObjectStore, Depends(get_object_store)]

# Plain numbers for 413 and 422: Starlette renamed those two constants between versions.
_HTTP_STATUS: dict[type[IngestionError], int] = {
    EmptyFile: 400,
    NotAPdf: 415,
    FileTooLarge: 413,
    UnreadablePdf: 422,
    SourceNotFound: 404,
    ProjectNotFound: 404,
    SourceNotEligible: 409,
}


class VersionOut(BaseModel):
    version_id: str
    version_number: int
    version_label: str | None
    sha256: str
    byte_size: int
    page_count: int | None
    text_layer: str | None  # yes, partial or no: can the text be selected?
    extraction_method: str | None
    extraction_status: str | None  # ok, partial or failed
    extraction_notes: list[str]
    retrieved_at: datetime | None = None


class IngestedOut(VersionOut):
    document_id: str
    source_id: str
    already_ingested: bool  # true if this exact file was stored before; nothing new was written
    project_ids: list[str]


class ProjectDocumentOut(BaseModel):
    document_id: str
    source_id: str
    title: str
    authority: str
    attribution: str | None  # must be shown next to any citation of this document
    latest_version: VersionOut
    versions: list[VersionOut] = []
    preview_text: str | None = None


def _extract_preview(
    store: ObjectStore, extracted_key: str | None, max_chars: int = 400
) -> str | None:
    if not extracted_key or not store.exists(extracted_key):
        return None
    try:
        payload = json.loads(store.get(extracted_key).decode("utf-8"))
        for page in payload.get("pages", []):
            text = (page.get("text") or "").strip()
            if text:
                snippet = text[:max_chars] + ("..." if len(text) > max_chars else "")
                return f"[Page {page.get('page', 1)}] {snippet}"
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def _version_out(version: DocumentVersion) -> VersionOut:
    return VersionOut(
        version_id=str(version.version_id),
        version_number=version.version_number,
        version_label=version.version_label,
        sha256=version.sha256,
        byte_size=version.byte_size,
        page_count=version.page_count,
        text_layer=version.text_layer,
        extraction_method=version.extraction_method,
        extraction_status=version.extraction_status,
        extraction_notes=list(version.extraction_notes or []),
        retrieved_at=version.retrieved_at,
    )


def _ingested_out(result: IngestionResult) -> IngestedOut:
    return IngestedOut(
        document_id=str(result.document_id),
        source_id=result.source_id,
        version_id=str(result.version_id),
        version_number=result.version_number,
        version_label=result.version_label,
        sha256=result.sha256,
        byte_size=result.byte_size,
        page_count=result.page_count,
        text_layer=result.text_layer,
        extraction_method=result.extraction_method,
        extraction_status=result.extraction_status,
        extraction_notes=list(result.extraction_notes),
        already_ingested=result.already_ingested,
        project_ids=list(result.project_ids),
    )


async def _read_body(request: Request, limit: int) -> bytes:
    """Read the request body, refusing early if it is larger than ``limit`` bytes."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise FileTooLarge(f"the file is larger than the limit of {limit} bytes")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise FileTooLarge(f"the file is larger than the limit of {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _record_refusal(
    audit: AuditRecorder, source_id: str, error: IngestionError, project_ids: list[str]
) -> None:
    """Write a refused ingestion to the audit trail. A licence refusal is DENIED, the rest FAILURE."""
    record = audit.denied if isinstance(error, SourceNotEligible) else audit.failure
    record(
        "DOCUMENT_INGEST_REFUSED",
        "source",
        resource_id=source_id,
        details={"reason": error.code, "project_ids": project_ids},
    )


def _refusal(error: IngestionError) -> HTTPException:
    return HTTPException(status_code=_HTTP_STATUS.get(type(error), 400), detail=str(error))


def _ingest_and_record(
    *,
    session: Session,
    store: ObjectStore,
    audit: AuditRecorder,
    source_id: str,
    data: bytes,
    project_ids: list[str],
    version_label: str | None,
) -> IngestedOut:
    """Ingest, write the audit event, then commit, in that order (see module docstring)."""
    service = DocumentIngestionService(session, store, max_bytes=settings.max_upload_mb * 1024**2)
    try:
        result = service.ingest(
            source_id=source_id, data=data, project_ids=project_ids, version_label=version_label
        )
    except IngestionError as exc:
        _record_refusal(audit, source_id, exc, project_ids)
        raise _refusal(exc) from exc
    except IntegrityError as exc:
        # For example two uploads of the first version of one source at the same moment.
        session.rollback()
        audit.failure(
            "DOCUMENT_INGEST_REFUSED",
            "source",
            resource_id=source_id,
            details={"reason": "conflict", "project_ids": project_ids},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Conflicting upload; try again."
        ) from exc

    audit.success(
        "DOCUMENT_ALREADY_INGESTED" if result.already_ingested else "DOCUMENT_INGESTED",
        "document_version",
        resource_id=str(result.version_id),
        resource_version=str(result.version_number),
        project_id=result.project_ids[0] if len(result.project_ids) == 1 else None,
        details={
            "source_id": result.source_id,
            "sha256": result.sha256,
            "byte_size": result.byte_size,
            "page_count": result.page_count,
            "text_layer": result.text_layer,
            "extraction_status": result.extraction_status,
            "extraction_method": result.extraction_method,
            "project_ids": list(result.project_ids),
        },
    )
    session.commit()
    return _ingested_out(result)


@router.put(
    "/sources/{source_id}/versions",
    response_model=IngestedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a PDF as a version of an approved source (Admin only)",
    description=(
        "The request body is the raw PDF. Refused (409) if the source is not eligible under the "
        "licence rule (ADR-002). The same file again returns 200 and changes nothing; a changed "
        "file becomes a new version and earlier versions are kept."
    ),
    dependencies=[Depends(require_role("admin"))],
)
async def ingest_document(
    source_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")],
    request: Request,
    response: Response,
    audit: AuditRecorderDep,
    session: SessionDep,
    store: StoreDep,
    project_id: Annotated[
        list[str] | None, Query(description="Project(s) that may search this document")
    ] = None,
    version_label: Annotated[str | None, Query(max_length=64)] = None,
) -> IngestedOut:
    projects = project_id or []
    limit = settings.max_upload_mb * 1024**2
    try:
        data = await _read_body(request, limit)
    except FileTooLarge as exc:
        await run_in_threadpool(_record_refusal, audit, source_id, exc, projects)
        raise _refusal(exc) from exc
    result = await run_in_threadpool(
        _ingest_and_record,
        session=session,
        store=store,
        audit=audit,
        source_id=source_id,
        data=data,
        project_ids=projects,
        version_label=version_label,
    )
    if result.already_ingested:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/projects/{project_id}/documents",
    response_model=list[ProjectDocumentOut],
    summary="List the documents a project may search",
)
def list_project_documents(
    project_id: str,
    principal: CurrentPrincipal,
    audit: AuditRecorderDep,
    session: SessionDep,
    store: StoreDep,
) -> list[ProjectDocumentOut]:
    """Membership is checked first, so a 403 does not reveal which projects exist."""
    ensure_project_access(principal, project_id)
    repo = DocumentRepository(session)
    rows = repo.latest_versions_for_project(project_id, principal.project_ids)
    all_versions = repo.versions_for_documents(doc.document_id for doc, _, _ in rows)
    audit.success(
        "DOCUMENTS_LISTED",
        "project",
        resource_id=project_id,
        project_id=project_id,
        details={"count": len(rows)},
    )
    return [
        ProjectDocumentOut(
            document_id=str(document.document_id),
            source_id=document.source_id,
            title=source.title,
            authority=source.authority,
            attribution=source.attribution,
            latest_version=_version_out(version),
            versions=[_version_out(v) for v in all_versions.get(document.document_id, [version])],
            preview_text=_extract_preview(store, version.extracted_key),
        )
        for document, source, version in rows
    ]
