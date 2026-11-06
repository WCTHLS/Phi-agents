"""Pydantic schemas for trusted regulatory and scientific research queries and responses."""

from typing import Literal

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    """Metadata and provenance tracking for an approved scientific/regulatory source."""

    source_id: str = Field(
        ..., description="Unique slug or identifier, e.g., 'fda-nitrosamines-rev1'"
    )
    title: str = Field(..., description="Official document title")
    publisher: str = Field(..., description="Issuing authority, e.g. FDA, EMA, ICH, MHRA")
    publication_date: str = Field(..., description="Publication or revision date (YYYY-MM-DD)")
    version: str = Field("1.0", description="Revision or version string")
    url_or_identifier: str = Field(..., description="Official URL, docket ID, or DOI")
    content_hash: str = Field(..., description="SHA-256 hash of the source document")
    notes: str | None = Field(None, description="Scope notes, applicability, or regulatory status")


class EvidenceChunk(BaseModel):
    """A granular, searchable passage extracted from an approved document."""

    chunk_id: str
    source_id: str
    document_title: str
    section: str
    page: int  # the first page of the passage
    text: str
    similarity_score: float = Field(0.0, description="Relevance similarity or reranking score")
    # Set when the passage comes from a project's stored documents (S2-09).
    page_end: int | None = Field(
        None, description="The last page of the passage (it can span pages)"
    )
    version_label: str | None = None
    version_number: int | None = None
    sha256: str | None = Field(None, description="SHA-256 of the document version quoted")


class Citation(BaseModel):
    """Exact citation grounding a factual statement in an AI-generated answer."""

    source_id: str
    document_title: str
    section: str
    page: int
    quoted_evidence: str
    # Set when the citation comes from a project's stored documents (S2-09).
    chunk_id: str | None = None
    page_end: int | None = None
    version_id: str | None = None
    version_number: int | None = None
    version_label: str | None = None
    sha256: str | None = Field(None, description="SHA-256 of the document version quoted")
    attribution: str | None = Field(None, description="Must be shown next to the citation")


class ResearchQuery(BaseModel):
    """Scientist's query payload."""

    query: str = Field(
        ..., min_length=3, max_length=2000, description="Scientific or regulatory question"
    )
    project_id: str | None = Field(
        None,
        max_length=64,
        description=(
            "Ask the documents of this project (members only). Without it the built-in sample "
            "passages are used, which is only for trying the interface."
        ),
    )
    publisher_filter: list[str] | None = Field(
        None, description="Optional filter by publisher (e.g., ['FDA', 'ICH'])"
    )
    top_k: int = Field(3, ge=1, le=10, description="Number of evidence chunks to retrieve")
    # There is no "strict mode" switch: an answer is always built from quotes that are checked
    # word for word against the retrieved passages (ADR-010). Older callers that still send
    # ``strict_citation_mode`` are not refused; the field is ignored.


class ResearchResponse(BaseModel):
    """Validated, traceable response returned to the scientist."""

    query: str
    answer: str
    citations: list[Citation]
    evidence_gaps: list[str] = Field(
        default_factory=list,
        description="Identified uncertainties, ungrounded areas, or data gaps missing from approved sources",
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence based on evidence coverage"
    )
    verified: bool = Field(
        ..., description="True only if claims are fully backed by retrieved citations"
    )
    retrieved_evidence: list[EvidenceChunk]
    data_source: Literal["project_documents", "built_in_sample"] = Field(
        "built_in_sample",
        description=(
            "project_documents: passages from the project's stored documents. built_in_sample: "
            "invented sample passages for trying the interface; never show these as real evidence."
        ),
    )
    workflow_run_id: str | None = Field(
        None, description="The stored run; send it back with the reviewer's decision"
    )
    review_status: str | None = Field(None, description="Where the answer stands in human review")
    answer_mode: str | None = Field(
        None, description="quotes-only, or the name of the model that wrote the answer"
    )
