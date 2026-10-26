"""API routes for regulatory research and evidence retrieval."""

from typing import List
from fastapi import APIRouter, HTTPException, status
from pharma_ai.features.research.schemas import (
    DocumentMetadata,
    ResearchQuery,
    ResearchResponse,
)
from pharma_ai.features.research.source_registry import get_approved_documents
from pharma_ai.features.research.answering import ResearchAnsweringService

router = APIRouter(prefix="/api/v1/research", tags=["research"])
_answering_service = ResearchAnsweringService()


@router.get(
    "/sources",
    response_model=List[DocumentMetadata],
    summary="List approved regulatory sources",
    description="Returns all verified, versioned regulatory documents and guidelines with SHA-256 hashes.",
)
def list_approved_sources() -> List[DocumentMetadata]:
    """Retrieve metadata of all approved sources in the registry."""
    return get_approved_documents()


@router.post(
    "/query",
    response_model=ResearchResponse,
    summary="Query regulatory evidence with strict citations",
    description="Queries the approved regulatory knowledge base, returning grounded answers with page-level citations.",
)
def query_regulatory_research(query: ResearchQuery) -> ResearchResponse:
    """Execute a grounded regulatory query against approved source evidence."""
    try:
        return _answering_service.answer_query(query)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while processing research query: {str(exc)}",
        ) from exc
