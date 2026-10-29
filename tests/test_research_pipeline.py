"""Tests for Phase 1 literature and regulatory research retrieval and question answering."""

from fastapi.testclient import TestClient

from pharma_ai.features.research.answering import ResearchAnsweringService
from pharma_ai.features.research.retrieval import HybridEvidenceRetriever
from pharma_ai.features.research.schemas import ResearchQuery
from pharma_ai.features.research.source_registry import get_approved_documents
from pharma_ai.main import app

client = TestClient(app)


def test_approved_sources_have_valid_metadata_and_hashes():
    """Verify all approved sources contain required metadata and SHA-256 hashes."""
    sources = get_approved_documents()
    assert len(sources) >= 3

    source_ids = [s.source_id for s in sources]
    assert "fda-nitrosamines-rev1" in source_ids
    assert "ich-q8-r2-pharma-dev" in source_ids
    assert "ich-q3d-elemental-impurities" in source_ids

    for source in sources:
        assert len(source.content_hash) == 64  # Valid SHA-256 string
        assert source.publisher in ["FDA", "ICH", "EMA", "MHRA"]
        assert source.url_or_identifier is not None


def test_retrieval_returns_relevant_fda_chunk_for_ndma():
    """Verify hybrid retrieval accurately finds the FDA acceptable intake limits for NDMA."""
    retriever = HybridEvidenceRetriever()
    query = ResearchQuery(
        query="What is the acceptable intake limit for NDMA according to FDA?", top_k=2
    )
    chunks = retriever.retrieve(query)

    assert len(chunks) > 0
    top_chunk = chunks[0]
    assert top_chunk.source_id == "fda-nitrosamines-rev1"
    assert "96 ng/day" in top_chunk.text
    assert top_chunk.similarity_score > 0.0


def test_retrieval_applies_publisher_filter():
    """Verify that specifying publisher_filter excludes other publishers."""
    retriever = HybridEvidenceRetriever()
    # Query mentions FDA but filter restricts to ICH
    query = ResearchQuery(
        query="What is Design Space and CQA?",
        publisher_filter=["ICH"],
        top_k=3,
    )
    chunks = retriever.retrieve(query)

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.source_id.startswith("ich-")


def test_grounded_answer_includes_citations_and_verification():
    """Verify that answering service includes exact citations and sets verified=True."""
    service = ResearchAnsweringService()
    query = ResearchQuery(query="What are the acceptable intake limits for NDMA and NDEA?")
    response = service.answer_query(query)

    assert response.verified is True
    assert response.confidence_score > 0.0
    assert len(response.citations) > 0

    # Ensure citation has page and section details
    citation = response.citations[0]
    assert citation.source_id == "fda-nitrosamines-rev1"
    assert "Section III" in citation.section
    assert citation.page == 10
    assert "96 ng/day" in citation.quoted_evidence


def test_answering_fails_closed_when_evidence_is_missing():
    """Verify system blocks hallucinations and flags evidence gaps for ungrounded queries."""
    service = ResearchAnsweringService()
    query = ResearchQuery(query="What is the average flight speed of an unladen swallow?")
    response = service.answer_query(query)

    assert response.verified is False
    assert response.confidence_score == 0.0
    assert len(response.citations) == 0
    assert len(response.evidence_gaps) > 0
    assert "No approved regulatory literature found" in response.evidence_gaps[0]
    assert "ungrounded response is withheld" in response.answer


def test_api_list_sources_endpoint(auth_headers: dict[str, str]):
    """Verify GET /api/v1/research/sources returns the approved sources list."""
    res = client.get("/api/v1/research/sources", headers=auth_headers)
    assert res.status_code == 200
    sources = res.json()
    assert isinstance(sources, list)
    assert len(sources) >= 3
    assert any(s["source_id"] == "ich-q3d-elemental-impurities" for s in sources)


def test_api_query_endpoint_for_elemental_impurities(auth_headers: dict[str, str]):
    """Verify POST /api/v1/research/query returns grounded response for elemental impurities."""
    payload = {
        "query": "What are the Class 1 elemental impurities and oral PDE limits in ICH Q3D?",
        "top_k": 2,
    }
    res = client.post("/api/v1/research/query", json=payload, headers=auth_headers)
    assert res.status_code == 200

    data = res.json()
    assert data["verified"] is True
    assert "Cadmium" in data["answer"]
    assert "Lead" in data["answer"]
    assert len(data["citations"]) > 0
    assert data["citations"][0]["source_id"] == "ich-q3d-elemental-impurities"
