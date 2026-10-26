"""Retrieval engine for authentic regulatory and scientific literature."""

import math
import re
from typing import List, Optional
from pharma_ai.features.research.schemas import EvidenceChunk, ResearchQuery
from pharma_ai.features.research.source_registry import get_raw_evidence_chunks, get_approved_documents


STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "of", "and", "or", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "what",
    "which", "who", "whom", "this", "that", "these", "those", "am", "it", "its",
    "can", "could", "should", "would", "how", "why", "when", "where"
}


class HybridEvidenceRetriever:
    """
    Hybrid retriever combining exact pharmaceutical entity token matching
    with lexical scoring and publisher filtering for authentic evidence retrieval.
    """

    def __init__(self):
        self._chunks: List[EvidenceChunk] = get_raw_evidence_chunks()
        self._docs = {d.source_id: d for d in get_approved_documents()}

    def _tokenize(self, text: str) -> set:
        """Tokenize text into lowercase alpha-numeric terms, preserving hyphens for chemical names."""
        tokens = re.findall(r'[a-zA-Z0-9\-\.\/]+', text.lower())
        return set(tokens)

    def retrieve(self, query: ResearchQuery) -> List[EvidenceChunk]:
        """
        Retrieve and rank evidence chunks against the query.
        Applies publisher filters, exact token matching, and normalized relevance scoring.
        """
        raw_terms = self._tokenize(query.query)
        # Exclude common non-informative stopwords
        query_terms = raw_terms - STOPWORDS
        if not query_terms:
            return []

        scored_chunks: List[tuple[float, EvidenceChunk]] = []

        for chunk in self._chunks:
            doc = self._docs.get(chunk.source_id)
            if not doc:
                continue

            # Apply publisher filter if specified
            if query.publisher_filter and doc.publisher.upper() not in [p.upper() for p in query.publisher_filter]:
                continue

            chunk_terms = self._tokenize(chunk.text + " " + chunk.document_title + " " + chunk.section)

            # Calculate term overlap and term importance
            overlap = query_terms.intersection(chunk_terms)
            if not overlap:
                continue

            # Boost exact matches for critical pharma keywords
            score = 0.0
            for term in overlap:
                # Give higher weight to chemical formulas, abbreviations, numbers, and longer tokens
                weight = 1.0
                if any(char.isdigit() for char in term):
                    weight += 1.5
                if len(term) >= 4:
                    weight += 0.5
                if term in ["ndma", "ndea", "nitrosamine", "qtpp", "cqa", "design", "pde", "oral", "fda", "ich", "cadmium", "lead", "arsenic", "mercury"]:
                    weight += 2.0
                score += weight

            # Normalize by query length
            normalized_score = min(1.0, score / max(1.0, len(query_terms) * 1.5))
            scored_chunk = chunk.model_copy()
            scored_chunk.similarity_score = round(normalized_score, 4)
            scored_chunks.append((normalized_score, scored_chunk))

        # Sort descending by score
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        return [c for _, c in scored_chunks[:query.top_k]]
