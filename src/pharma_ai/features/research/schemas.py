"""Pydantic schemas for trusted regulatory and scientific research queries and responses."""

from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    """Metadata and provenance tracking for an approved scientific/regulatory source."""
    source_id: str = Field(..., description="Unique slug or identifier, e.g., 'fda-nitrosamines-rev1'")
    title: str = Field(..., description="Official document title")
    publisher: str = Field(..., description="Issuing authority, e.g. FDA, EMA, ICH, MHRA")
    publication_date: str = Field(..., description="Publication or revision date (YYYY-MM-DD)")
    version: str = Field("1.0", description="Revision or version string")
    url_or_identifier: str = Field(..., description="Official URL, docket ID, or DOI")
    content_hash: str = Field(..., description="SHA-256 hash of the source document")
    notes: Optional[str] = Field(None, description="Scope notes, applicability, or regulatory status")


class EvidenceChunk(BaseModel):
    """A granular, searchable passage extracted from an approved document."""
    chunk_id: str
    source_id: str
    document_title: str
    section: str
    page: int
    text: str
    similarity_score: float = Field(0.0, description="Relevance similarity or reranking score")


class Citation(BaseModel):
    """Exact citation grounding a factual statement in an AI-generated answer."""
    source_id: str
    document_title: str
    section: str
    page: int
    quoted_evidence: str


class ResearchQuery(BaseModel):
    """Scientist's query payload."""
    query: str = Field(..., min_length=3, description="Scientific or regulatory question")
    publisher_filter: Optional[List[str]] = Field(None, description="Optional filter by publisher (e.g., ['FDA', 'ICH'])")
    top_k: int = Field(3, ge=1, le=10, description="Number of evidence chunks to retrieve")
    strict_citation_mode: bool = Field(True, description="Enforce zero-hallucination; flag unsupported claims as evidence gaps")


class ResearchResponse(BaseModel):
    """Validated, traceable response returned to the scientist."""
    query: str
    answer: str
    citations: List[Citation]
    evidence_gaps: List[str] = Field(
        default_factory=list,
        description="Identified uncertainties, ungrounded areas, or data gaps missing from approved sources"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence based on evidence coverage")
    verified: bool = Field(..., description="True only if claims are fully backed by retrieved citations")
    retrieved_evidence: List[EvidenceChunk]
