"""Literature and regulatory research feature package."""

from pharma_ai.features.research.answering import ResearchAnsweringService
from pharma_ai.features.research.retrieval import HybridEvidenceRetriever
from pharma_ai.features.research.schemas import (
    Citation,
    DocumentMetadata,
    EvidenceChunk,
    ResearchQuery,
    ResearchResponse,
)
from pharma_ai.features.research.source_registry import (
    get_approved_documents,
    get_raw_evidence_chunks,
)

__all__ = [
    "Citation",
    "DocumentMetadata",
    "EvidenceChunk",
    "HybridEvidenceRetriever",
    "ResearchAnsweringService",
    "ResearchQuery",
    "ResearchResponse",
    "get_approved_documents",
    "get_raw_evidence_chunks",
]
