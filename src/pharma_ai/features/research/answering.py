"""Evidence-grounded question answering engine with citation enforcement and hallucination guardrails."""

import re

from pharma_ai.features.research.retrieval import HybridEvidenceRetriever
from pharma_ai.features.research.schemas import (
    Citation,
    ResearchQuery,
    ResearchResponse,
)


class ResearchAnsweringService:
    """
    Synthesizes scientific and regulatory answers backed strictly by retrieved evidence.
    Enforces citation extraction, flags evidence gaps, and blocks unverified extrapolation.
    """

    def __init__(self, retriever: HybridEvidenceRetriever = None):
        self.retriever = retriever or HybridEvidenceRetriever()

    def answer_query(self, query: ResearchQuery) -> ResearchResponse:
        """Process a scientist's query and produce a verified, cited response."""
        retrieved_chunks = self.retriever.retrieve(query)

        # Fail closed if no approved evidence is retrieved
        if not retrieved_chunks or retrieved_chunks[0].similarity_score < 0.10:
            return ResearchResponse(
                query=query.query,
                answer=(
                    "No verified evidence was found in the approved regulatory and scientific sources (FDA, EMA, ICH) "
                    "matching this query. To ensure compliance and prevent hallucination, an ungrounded response is withheld."
                ),
                citations=[],
                evidence_gaps=[
                    f"No approved regulatory literature found for query: '{query.query}' in current source registry."
                ],
                confidence_score=0.0,
                verified=False,
                retrieved_evidence=[],
            )

        citations: list[Citation] = []
        synthesized_points: list[str] = []
        query_terms = set(re.findall(r"[a-zA-Z0-9\-]+", query.query.lower()))

        for chunk in retrieved_chunks:
            quote_text = chunk.text

            citation = Citation(
                source_id=chunk.source_id,
                document_title=chunk.document_title,
                section=chunk.section,
                page=chunk.page,
                quoted_evidence=quote_text,
            )
            citations.append(citation)
            synthesized_points.append(
                f"According to {chunk.document_title} ({chunk.section}, p. {chunk.page}):\n"
                f'"{quote_text}"'
            )

        # Assemble the structured answer
        answer_body = "\n\n".join(synthesized_points)

        # Detect potential gaps (e.g. if scientist asked about stability but only AI limits were retrieved)
        evidence_gaps = []
        covered_text = " ".join([c.text.lower() for c in retrieved_chunks])
        for term in query_terms:
            if len(term) > 4 and term not in covered_text:
                evidence_gaps.append(
                    f"Specific data regarding '{term}' is not explicitly defined in the retrieved passages."
                )

        # Compute confidence score
        avg_score = sum(c.similarity_score for c in retrieved_chunks) / len(retrieved_chunks)
        confidence = min(0.99, max(0.50, round(avg_score, 2)))

        return ResearchResponse(
            query=query.query,
            answer=answer_body,
            citations=citations,
            evidence_gaps=evidence_gaps,
            confidence_score=confidence,
            verified=True,
            retrieved_evidence=retrieved_chunks,
        )
