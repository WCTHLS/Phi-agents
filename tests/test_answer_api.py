"""Tests for asking a question of a project's documents (S2-09), against a real database.

A stand-in embedding model is used, so these tests say nothing about search quality (see evals/).
They check the promises around the answer: only quotes found in the retrieved passages, nothing
from other projects, a stored run, and an audit record that holds a fingerprint and not the question.

Run with TEST_DATABASE_URL set to an empty throwaway database (see test_db_integration.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from db_fixtures import (
    NITRO,
    PV,
    Q3D,
    add_document,
    add_spanning_document,
    events,
    headers,
    needs_db,
)
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_llm_writer import FakeModel, picks

from pharma_ai.features.research.answer_workflow import NO_EVIDENCE_ANSWER
from pharma_ai.features.research.answer_writer import AnswerWriter
from pharma_ai.features.research.llm_writer import LlmQuoteWriter
from pharma_ai.features.research.schemas import ResearchQuery
from pharma_ai.features.research.writer_factory import get_answer_writer
from pharma_ai.infrastructure.audit.service import AuditUnavailableError, get_audit_service
from pharma_ai.infrastructure.db.models import WorkflowRun
from pharma_ai.infrastructure.llm import LlmUnavailable
from pharma_ai.main import app

pytestmark = needs_db

URL = "/api/v1/research/query"
SENTENCE = "The acceptable intake for NDMA is 96 ng/day."
PASSAGE = f"Table 1 lists the limits. {SENTENCE} Other nitrosamines follow another rule."
OTHER = "Cadmium oral permitted daily exposure is five micrograms per day."
QUESTION = "What is the acceptable intake for NDMA?"
MEMBER_1 = headers(["scientist"], ["PRJ-001"])
MEMBER_BOTH = headers(["scientist"], ["PRJ-001", "PRJ-002"], sub="both-api-test")
MEMBER_2 = headers(["scientist"], ["PRJ-002"], sub="other-api-test")


def ask(
    api: TestClient, auth: dict, question: str = QUESTION, project: str | None = "PRJ-001", **extra
):
    body = {"query": question, **extra}
    if project is not None:
        body["project_id"] = project
    return api.post(URL, json=body, headers=auth)


def run_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(WorkflowRun)) or 0


# --- a supported question -----------------------------------------------------------------------


def test_a_member_gets_a_quoted_answer_with_everything_a_citation_needs(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE, "Unrelated filler text."])
    response = ask(api, MEMBER_1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_source"] == "project_documents" and body["answer_mode"] == "quotes-only"
    assert body["verified"] is True and body["review_status"] == "PENDING_EXPERT_REVIEW"
    assert body["workflow_run_id"].startswith("RUN-")
    assert f'"{SENTENCE}"' in body["answer"]
    first = body["citations"][0]
    assert first["quoted_evidence"] == SENTENCE and first["quoted_evidence"] in PASSAGE
    assert first["source_id"] == NITRO and first["document_title"]
    assert first["attribution"] and len(first["sha256"]) == 64
    assert (first["version_number"], first["version_label"]) == (1, "Revision 1")
    assert (first["page"], first["page_end"], first["section"]) == (1, 1, "Section 1")
    assert 0 < body["confidence_score"] <= 1  # coverage of the question's words, not a probability
    assert body["retrieved_evidence"][0]["text"] == PASSAGE


def test_the_confidence_figure_is_the_share_of_the_questions_words_that_were_quoted(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    # "acceptable", "intake" and "ndma" are quoted; "stability" is not in any passage.
    body = ask(api, MEMBER_1, question="acceptable intake NDMA stability").json()
    assert body["confidence_score"] == 0.75
    assert any("stability" in gap for gap in body["evidence_gaps"])
    assert body["verified"] is True  # coverage is not the same thing as verification


def test_the_run_is_stored_with_its_question_answer_and_citations(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    body = ask(api, MEMBER_1).json()
    run = session.get(WorkflowRun, body["workflow_run_id"])
    assert run is not None and run.project_id == "PRJ-001"
    assert run.question == QUESTION and run.answer == body["answer"]
    assert run.status == "PENDING_EXPERT_REVIEW" and run.decided_by is None
    assert run.asked_by == "user-api-test" and run.verified is True
    assert run.citations[0]["quoted_evidence"] == SENTENCE
    assert run.embedding_model == embedder.model_name and run.answer_mode == "quotes-only"
    assert next(r["chunk_id"] for r in run.retrieved) == run.citations[0]["chunk_id"]


def test_the_audit_event_holds_a_fingerprint_not_the_question(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    body = ask(api, MEMBER_1).json()
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="workflow_run")
    assert event["resource_id"] == body["workflow_run_id"] and event["project_id"] == "PRJ-001"
    assert event["outcome"] == "SUCCESS"
    details = event["details"]
    assert (
        details["question"]["length"] == len(QUESTION) and len(details["question"]["sha256"]) == 64
    )
    assert details["verified"] is True and details["withheld"] is False
    assert [c["chunk_id"] for c in details["citations"]] == [
        c["chunk_id"] for c in body["citations"]
    ]
    dumped = json.dumps(event)
    assert QUESTION not in dumped and SENTENCE not in dumped and PASSAGE not in dumped


def test_top_k_limits_the_passages_returned(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(
        session, embedder, NITRO, "PRJ-001", [f"NDMA limit passage {i}." for i in range(6)]
    )
    body = ask(api, MEMBER_1, top_k=2).json()
    assert len(body["retrieved_evidence"]) == 2


# --- when there is no evidence ------------------------------------------------------------------


def test_there_is_no_strict_mode_switch_and_an_old_caller_that_sends_one_is_not_refused(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    # Answers are always built from verified quotes. Callers that still send the old setting get
    # the same answer either way, and it is not recorded.
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE, "Unrelated filler text."])
    on = ask(api, MEMBER_1, strict_citation_mode=True)
    off = ask(api, MEMBER_1, strict_citation_mode=False)
    assert on.status_code == off.status_code == 200
    assert on.json()["answer"] == off.json()["answer"] and on.json()["verified"] is True
    assert "strict_citation_mode" not in ResearchQuery.model_fields
    for event in events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="workflow_run"):
        assert "strict_citation_mode" not in event["details"]


def test_the_sample_mode_audit_record_does_not_hold_the_old_setting(api: TestClient) -> None:
    assert ask(api, MEMBER_1, project=None, strict_citation_mode=False).status_code == 200
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="research_query")
    assert "strict_citation_mode" not in event["details"]


def test_a_question_the_documents_cannot_answer_is_withheld_not_guessed(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    response = ask(api, MEMBER_1, question="What is the capital of France?")
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == NO_EVIDENCE_ANSWER
    assert body["verified"] is False and body["citations"] == [] and body["confidence_score"] == 0.0
    assert body["evidence_gaps"]
    assert "NDMA" not in body["answer"]
    run = session.get(WorkflowRun, body["workflow_run_id"])  # the attempt is still on record
    assert run is not None and run.verified is False and run.citations == []
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="workflow_run")
    assert event["details"]["withheld"] is True


def test_a_project_with_no_documents_gets_no_answer(api: TestClient) -> None:
    body = ask(api, MEMBER_1).json()
    assert body["answer"] == NO_EVIDENCE_ANSWER and body["retrieved_evidence"] == []


# --- who may not (the leakage cases) ------------------------------------------------------------


def test_a_non_member_is_refused_and_nothing_is_embedded_searched_or_stored(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    model_loads: list[int],
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    embedder.calls.clear()
    response = ask(api, MEMBER_2)  # a member of PRJ-002 only
    assert response.status_code == 403
    assert SENTENCE not in response.text
    assert model_loads == [] and embedder.calls == []
    assert run_count(session) == 0
    assert events(api, action="RESEARCH_QUERY_EXECUTED") == []
    (denied,) = events(api, action="ACCESS_DENIED")
    assert denied["outcome"] == "DENIED"


def test_a_project_that_does_not_exist_is_refused_like_one_you_may_not_see(
    api: TestClient,
) -> None:
    unknown = ask(api, MEMBER_1, project="PRJ-404")
    forbidden = ask(api, MEMBER_1, project="PRJ-002")
    assert unknown.status_code == forbidden.status_code == 403
    assert unknown.json() == forbidden.json()


def test_another_projects_passages_never_appear_even_for_a_member_of_both(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    add_document(session, embedder, PV, "PRJ-002", [OTHER])
    first = ask(
        api, MEMBER_BOTH, question="cadmium oral permitted daily exposure", project="PRJ-001"
    )
    assert first.status_code == 200
    assert OTHER not in first.text and first.json()["answer"] == NO_EVIDENCE_ANSWER
    second = ask(
        api, MEMBER_BOTH, question="cadmium oral permitted daily exposure", project="PRJ-002"
    )
    assert OTHER in second.json()["answer"]
    assert PASSAGE not in second.text


def test_the_request_cannot_widen_the_search(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, PV, "PRJ-002", [OTHER])
    response = ask(
        api,
        MEMBER_1,
        question="cadmium oral permitted daily exposure",
        project_ids=["PRJ-002"],
        allowed_project_ids=["PRJ-002"],
        projects=["PRJ-002"],
    )
    assert response.status_code == 200 and OTHER not in response.text


def test_an_answer_is_stored_in_the_project_it_was_asked_in(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    add_document(session, embedder, Q3D, "PRJ-002", [OTHER])
    ask(api, MEMBER_BOTH, project="PRJ-001")
    ask(api, MEMBER_BOTH, question="cadmium exposure", project="PRJ-002")
    projects = sorted(session.scalars(select(WorkflowRun.project_id)))
    assert projects == ["PRJ-001", "PRJ-002"]


def test_a_query_needs_a_login(api: TestClient) -> None:
    assert api.post(URL, json={"query": QUESTION, "project_id": "PRJ-001"}).status_code == 401


# --- bad requests and failures ------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra, status",
    [
        ({"publisher_filter": ["FDA"]}, 422),
        ({"top_k": 0}, 422),
        ({"top_k": 11}, 422),
        ({"query": "x" * 2001}, 422),
        ({"query": "ab"}, 422),
        ({"project_id": "P" * 65}, 422),
    ],
)
def test_malformed_requests_are_refused_and_store_nothing(
    api: TestClient, session: Session, extra: dict, status: int
) -> None:
    body = {"query": QUESTION, "project_id": "PRJ-001", **extra}
    assert api.post(URL, json=body, headers=MEMBER_1).status_code == status
    assert run_count(session) == 0


def test_without_a_project_the_built_in_samples_are_used_and_marked_as_such(
    api: TestClient, session: Session
) -> None:
    body = ask(api, MEMBER_1, question="NDMA limit", project=None).json()
    assert body["data_source"] == "built_in_sample" and body["workflow_run_id"] is None
    assert run_count(session) == 0


def test_if_the_audit_trail_cannot_be_written_the_run_is_not_saved(
    api: TestClient, session: Session, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])

    class BrokenTrail:
        def record_event(self, **_kwargs):
            raise AuditUnavailableError("the trail is down")

    committed: list[bool] = []
    monkeypatch.setattr(session, "commit", lambda: committed.append(True))
    app.dependency_overrides[get_audit_service] = lambda: BrokenTrail()
    try:
        response = ask(api, MEMBER_1)
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert response.status_code == 503
    assert committed == [] and SENTENCE not in response.text


def test_a_failing_search_is_audited_and_stores_no_run(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_texts):
        raise RuntimeError("the model fell over")

    monkeypatch.setattr(embedder, "embed", broken)
    response = TestClient(app, raise_server_exceptions=False).post(
        URL, json={"query": QUESTION, "project_id": "PRJ-001"}, headers=MEMBER_1
    )
    assert response.status_code == 500
    assert run_count(session) == 0
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED")
    assert event["outcome"] == "FAILURE" and "fell over" not in json.dumps(event)


# --- with a language model behind the writer ----------------------------------------------------


@pytest.fixture
def use_writer() -> Iterator[Callable[[AnswerWriter], None]]:
    def install(writer: AnswerWriter) -> None:
        app.dependency_overrides[get_answer_writer] = lambda: writer

    yield install
    app.dependency_overrides.pop(get_answer_writer, None)


def test_a_model_backed_answer_is_cited_stored_and_recorded_as_sent_to_the_provider(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    use_writer: Callable[[AnswerWriter], None],
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    model = FakeModel(picks((1, SENTENCE)))
    use_writer(LlmQuoteWriter(model, provider="groq"))
    body = ask(api, MEMBER_1).json()
    assert body["answer_mode"] == "fake:model" and body["verified"] is True
    assert body["citations"][0]["quoted_evidence"] == SENTENCE
    run = session.get(WorkflowRun, body["workflow_run_id"])
    assert run is not None and run.answer_mode == "fake:model"
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="workflow_run")
    assert event["details"]["sent_to"] == "groq" and event["details"]["answer_mode"] == "fake:model"


def test_only_the_question_and_passages_are_sent_to_the_model(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    use_writer: Callable[[AnswerWriter], None],
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    model = FakeModel(picks((1, SENTENCE)))
    use_writer(LlmQuoteWriter(model, provider="groq"))
    body = ask(api, MEMBER_1).json()
    (call,) = model.calls
    sent = call["system"] + call["user"]
    assert QUESTION in sent and PASSAGE in sent
    chunk_id = body["retrieved_evidence"][0]["chunk_id"]
    for private in ("user-api-test", "@pharma.local", "PRJ-001", body["workflow_run_id"], chunk_id):
        assert private not in sent
    assert body["retrieved_evidence"][0]["sha256"] not in sent


def test_a_model_that_cannot_be_used_falls_back_and_the_attempt_is_still_recorded(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    use_writer: Callable[[AnswerWriter], None],
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    model = FakeModel(error=LlmUnavailable("groq answered with HTTP 429"))
    use_writer(LlmQuoteWriter(model, provider="groq"))
    body = ask(api, MEMBER_1).json()
    assert body["verified"] is True and body["answer_mode"] == "quotes-only"
    assert body["evidence_gaps"][0].startswith("The language model could not be used (")
    run = session.get(WorkflowRun, body["workflow_run_id"])
    assert run is not None and run.evidence_gaps[0] == body["evidence_gaps"][0]
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="workflow_run")
    assert event["details"]["sent_to"] == "groq"  # the data was sent even though the reply failed


def test_a_made_up_quote_from_the_model_never_reaches_the_answer_or_the_stored_run(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    use_writer: Callable[[AnswerWriter], None],
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    invented = "The acceptable intake for NDMA is 960 ng/day."
    use_writer(LlmQuoteWriter(FakeModel(picks((1, invented))), provider="groq"))
    body = ask(api, MEMBER_1).json()
    assert body["verified"] is False and body["citations"] == []
    assert "960" not in body["answer"] and body["answer"] == NO_EVIDENCE_ANSWER
    run = session.get(WorkflowRun, body["workflow_run_id"])
    assert run is not None and "960" not in run.answer and run.citations == []


def test_a_refused_request_never_reaches_the_model(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    use_writer: Callable[[AnswerWriter], None],
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    model = FakeModel(picks((1, SENTENCE)))
    use_writer(LlmQuoteWriter(model, provider="groq"))
    assert ask(api, MEMBER_2).status_code == 403
    assert model.calls == []


def test_without_a_model_nothing_is_recorded_as_sent(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    ask(api, MEMBER_1)
    (event,) = events(api, action="RESEARCH_QUERY_EXECUTED", resource_type="workflow_run")
    assert event["details"]["sent_to"] is None


# --- the page a citation names is the page of the quote -----------------------------------------

PAGE_ONE = "Contributions from processing equipment would be expected to be minimal."
PAGE_TWO = f"ICH Q3D(R2) Guideline\nOther opening text. {SENTENCE}"


def test_a_citation_names_the_page_the_quote_is_on_not_the_first_page_of_its_passage(
    api: TestClient, session: Session, embedder: FakeEmbedder, store
) -> None:
    add_spanning_document(session, embedder, store, NITRO, "PRJ-001", [PAGE_ONE, PAGE_TWO])
    body = ask(api, MEMBER_1).json()
    (citation,) = body["citations"]
    assert citation["quoted_evidence"] == SENTENCE
    assert (citation["page"], citation["page_end"]) == (2, 2)  # the passage starts on page 1
    # The passage itself is still reported with its whole range.
    passage = body["retrieved_evidence"][0]
    assert (passage["page"], passage["page_end"]) == (1, 2)
    # The stored run and a later review use the quote's page too.
    run = session.get(WorkflowRun, body["workflow_run_id"])
    assert run is not None and (run.citations[0]["page_start"], run.citations[0]["page_end"]) == (
        2,
        2,
    )


def test_a_reviewer_sees_the_quotes_page_in_the_cited_sources(
    api: TestClient, session: Session, embedder: FakeEmbedder, store
) -> None:
    add_spanning_document(session, embedder, store, NITRO, "PRJ-001", [PAGE_ONE, PAGE_TWO])
    run_id = ask(api, MEMBER_BOTH).json()["workflow_run_id"]
    review = api.post(
        "/api/v1/research/reviews",
        json={
            "workflow_run_id": run_id,
            "project_id": "PRJ-001",
            "decision": "APPROVED",
            "rationale": "Checked on page 2.",
        },
        headers=headers(["reviewer"], ["PRJ-001"], sub="reviewer-pages"),
    )
    assert review.status_code == 201
    assert review.json()["cited_sources"] == [f"{NITRO} (v1, p. 2)"]


def test_without_stored_page_text_the_citation_keeps_the_passages_page_range(
    api: TestClient, session: Session, embedder: FakeEmbedder, store
) -> None:
    add_spanning_document(
        session, embedder, store, NITRO, "PRJ-001", [PAGE_ONE, PAGE_TWO], keep_page_text=False
    )
    (citation,) = ask(api, MEMBER_1).json()["citations"]
    assert (citation["page"], citation["page_end"]) == (1, 2)  # true, if less exact


def test_unreadable_page_text_does_not_break_the_answer(
    api: TestClient, session: Session, embedder: FakeEmbedder, store
) -> None:
    add_spanning_document(session, embedder, store, NITRO, "PRJ-001", [PAGE_ONE, PAGE_TWO])
    (stored,) = list(store.root.rglob("*spanning.json"))
    stored.write_bytes(b"this is not json")
    response = ask(api, MEMBER_1)
    assert response.status_code == 200
    (citation,) = response.json()["citations"]
    assert (citation["page"], citation["page_end"]) == (1, 2)
