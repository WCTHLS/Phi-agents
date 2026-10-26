"""Literature and regulatory research feature package."""

from pharma_ai.features.research.schemas import (
    DocumentMetadata,
    EvidenceChunk,
    Citation,
    ResearchQuery,
    ResearchResponse,
)
from pharma_ai.features.research.source_registry import (
    get_approved_documents,
    get_raw_evidence_chunks,
)
from pharma_ai.features.research.retrieval import HybridEvidenceRetriever
from pharma_ai.features.research.answering import ResearchAnsweringService

__all__ = [
    "DocumentMetadata",
    "EvidenceChunk",
    "Citation",
    "ResearchQuery",
    "ResearchResponse",
    "get_approved_documents",
    "get_raw_evidence_chunks",
    "HybridEvidenceRetriever",
    "ResearchAnsweringService",
]
