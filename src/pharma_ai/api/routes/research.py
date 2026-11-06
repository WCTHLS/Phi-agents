"""API routes for regulatory research and evidence retrieval.

``POST /api/v1/research/query`` has two modes:

- **With ``project_id`` (S2-09):** the real pipeline. The caller must belong to the project (403
  before anything is searched). The project's documents are searched, an answer is built only from
  quotes found word for word in the retrieved passages, and the run is stored with its review
  status. The run and its audit event are saved in one transaction. Without a verified quote the
  answer is withheld.
- **Without it:** the built-in sample passages, for trying the interface. The response says so
  (``data_source: built_in_sample``). This mode is to be removed once nothing uses it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pharma_ai.api.routes.documents import StoreDep
from pharma_ai.api.routes.search import EmbedderProviderDep, SessionDep
from pharma_ai.features.research.answer_workflow import GroundedAnswer, answer_question
from pharma_ai.features.research.answer_writer import AnswerWriter
from pharma_ai.features.research.answering import ResearchAnsweringService
from pharma_ai.features.research.citation_pages import refine_citation_pages
from pharma_ai.features.research.schemas import (
    Citation,
    DocumentMetadata,
    EvidenceChunk,
    ResearchQuery,
    ResearchResponse,
)
from pharma_ai.features.research.search import FusedHit, PassageSearch
from pharma_ai.features.research.source_registry import get_approved_documents
from pharma_ai.features.research.writer_factory import get_answer_writer
from pharma_ai.infrastructure.audit.recorder import (
    AuditRecorder,
    AuditRecorderDep,
    fingerprint_text,
)
from pharma_ai.infrastructure.db.repositories import (
    RunInput,
    WorkflowRunRepository,
    new_run_id,
)
from pharma_ai.infrastructure.embeddings import Embedder
from pharma_ai.infrastructure.security.auth import (
    CurrentPrincipal,
    ensure_project_access,
    get_current_principal,
)
from pharma_ai.infrastructure.storage import ObjectStore

# Every research endpoint requires an authenticated caller (401 otherwise).
router = APIRouter(
    prefix="/api/v1/research",
    tags=["research"],
    dependencies=[Depends(get_current_principal)],
)
_answering_service = ResearchAnsweringService()
# Quoting by word matching unless LLM_PROVIDER names a model (writer_factory.py).
AnswerWriterDep = Annotated[AnswerWriter, Depends(get_answer_writer)]


@router.get(
    "/sources",
    response_model=list[DocumentMetadata],
    summary="List approved regulatory sources",
    description="Returns all verified, versioned regulatory documents and guidelines with SHA-256 hashes.",
)
def list_approved_sources(audit: AuditRecorderDep) -> list[DocumentMetadata]:
    """Retrieve metadata of all approved sources in the registry."""
    sources = get_approved_documents()
    audit.success("RESEARCH_SOURCES_LISTED", "source_register", details={"count": len(sources)})
    return sources


def _retrieved_summary(found: list[FusedHit]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "chunk_id": str(f.hit.chunk_id),
            "source_id": f.hit.source_id,
            "version_id": str(f.hit.version_id),
            "score": f.score,
            "vector_rank": f.vector_rank,
            "keyword_rank": f.keyword_rank,
        }
        for rank, f in enumerate(found, start=1)
    ]


def _response(
    query: ResearchQuery, result: GroundedAnswer, found: list[FusedHit], run_id: str, status_: str
) -> ResearchResponse:
    citations = [
        Citation(
            source_id=c["source_id"],
            document_title=c["title"] or c["source_id"],
            section=c["section"] or "",
            page=c["page_start"],
            quoted_evidence=c["quoted_evidence"],
            chunk_id=c["chunk_id"],
            page_end=c["page_end"],
            version_id=c["version_id"],
            version_number=c["version_number"],
            version_label=c["version_label"],
            sha256=c["sha256"],
            attribution=c["attribution"],
        )
        for c in result.citations
    ]
    evidence = [
        EvidenceChunk(
            chunk_id=str(f.hit.chunk_id),
            source_id=f.hit.source_id,
            document_title=f.hit.title or f.hit.source_id,
            section=f.hit.section or "",
            page=f.hit.page_start,
            page_end=f.hit.page_end,
            text=f.hit.text,
            similarity_score=max(0.0, 1.0 - f.hit.distance) if f.hit.distance is not None else 0.0,
            version_label=f.hit.version_label,
            version_number=f.hit.version_number,
            sha256=f.hit.sha256,
        )
        for f in found
    ]
    return ResearchResponse(
        query=query.query,
        answer=result.answer,
        citations=citations,
        evidence_gaps=result.evidence_gaps,
        confidence_score=result.coverage,
        verified=result.verified,
        retrieved_evidence=evidence,
        data_source="project_documents",
        workflow_run_id=run_id,
        review_status=status_,
        answer_mode=result.mode,
    )


def _answer_in_project(
    query: ResearchQuery,
    principal: CurrentPrincipal,
    audit: AuditRecorder,
    session: Session,
    embedder_for_search: Callable[[], Embedder],
    writer: AnswerWriter,
    store: ObjectStore,
) -> ResearchResponse:
    project_id = query.project_id
    assert project_id is not None
    # Membership first: nothing is embedded or searched for a non-member.
    ensure_project_access(principal, project_id)
    if query.publisher_filter:
        raise HTTPException(
            status_code=422,  # a plain number: Starlette renamed the constant between versions
            detail="publisher_filter is not supported when project_id is given yet",
        )
    embedder = embedder_for_search()
    fingerprint = fingerprint_text(query.query)
    try:
        found = PassageSearch(session, embedder).search(
            query.query, [project_id], limit=query.top_k
        )
        result = answer_question(query.query, found, writer)
        # A passage can span two pages; each citation names the page its quote is on.
        result = replace(result, citations=refine_citation_pages(session, store, result.citations))
    except Exception as exc:
        audit.failure(
            "RESEARCH_QUERY_EXECUTED",
            "project",
            resource_id=project_id,
            project_id=project_id,
            details={"question": fingerprint, "error_type": type(exc).__name__},
        )
        raise

    run_id = new_run_id()
    run = WorkflowRunRepository(session).add(
        RunInput(
            run_id=run_id,
            project_id=project_id,
            asked_by=principal.subject,
            asked_by_email=principal.email,
            question=query.query,
            question_sha256=fingerprint["sha256"],
            answer=result.answer,
            answer_mode=result.mode,
            verified=result.verified,
            coverage=result.coverage,
            evidence_gaps=result.evidence_gaps,
            citations=result.citations,
            retrieved=_retrieved_summary(found),
            embedding_model=embedder.model_name,
        )
    )
    # The event joins this transaction: the run and its record are saved together or not at all.
    audit.success(
        "RESEARCH_QUERY_EXECUTED",
        "workflow_run",
        resource_id=run_id,
        project_id=project_id,
        session=session,
        details={
            "question": fingerprint,
            "top_k": query.top_k,
            "embedding_model": embedder.model_name,
            "answer_mode": result.mode,
            # The provider that saw the question and passages (even if its reply was unusable).
            "sent_to": result.sent_to,
            "verified": result.verified,
            "withheld": result.withheld,
            "rejected_quotes": result.rejected_quotes,
            "coverage": result.coverage,
            "citations": [
                {
                    "chunk_id": c["chunk_id"],
                    "source_id": c["source_id"],
                    "version_id": c["version_id"],
                }
                for c in result.citations
            ],
            "retrieved": _retrieved_summary(found),
        },
    )
    session.commit()
    return _response(query, result, found, run_id, run.status)


@router.post(
    "/query",
    response_model=ResearchResponse,
    summary="Ask a question; answers are quotes from the project's documents",
    description=(
        "With project_id: searches that project's documents (members only) and answers only with "
        "quotes found word for word in the retrieved passages, with version, page and attribution. "
        "Without enough evidence the answer is withheld. The run is stored and awaits human "
        "review (POST /api/v1/research/reviews). Without project_id: built-in sample passages."
    ),
)
def query_regulatory_research(
    query: ResearchQuery,
    principal: CurrentPrincipal,
    audit: AuditRecorderDep,
    session: SessionDep,
    embedder_for_search: EmbedderProviderDep,
    writer: AnswerWriterDep,
    store: StoreDep,
) -> ResearchResponse:
    """Execute a grounded regulatory query.

    The audit record holds a fingerprint of the question, not the question: it may contain
    confidential product details, and the audit trail can never be edited or deleted.
    """
    if query.project_id is not None:
        return _answer_in_project(
            query, principal, audit, session, embedder_for_search, writer, store
        )

    try:
        response = _answering_service.answer_query(query)
    except Exception as exc:
        audit.failure(
            "RESEARCH_QUERY_EXECUTED",
            "research_query",
            details={"query": fingerprint_text(query.query), "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while processing research query: {exc!s}",
        ) from exc
    audit.success(
        "RESEARCH_QUERY_EXECUTED",
        "research_query",
        details={
            "query": fingerprint_text(query.query),
            "top_k": query.top_k,
            "publisher_filter": query.publisher_filter,
            "retrieved_source_ids": sorted({c.source_id for c in response.retrieved_evidence}),
            "citation_count": len(response.citations),
            "evidence_gap_count": len(response.evidence_gaps),
            "verified": response.verified,
        },
    )
    return response
