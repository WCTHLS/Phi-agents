"""Tests for the human-review gate (S2-10, NFR-12 / FR-27), against a real database.

A reviewer's decision is checked against the stored answer (S2-09): the run must exist in the
project, the cited sources come from the stored answer, and the run's status and the audit event
are saved together. Each test asks a real question first, so the run is the one the pipeline made.

Run with TEST_DATABASE_URL set to an empty throwaway database (see test_db_integration.py).
"""

from __future__ import annotations

import json

import pytest
from db_fixtures import NITRO, add_document, events, headers, needs_db
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.audit.service import AuditUnavailableError, get_audit_service
from pharma_ai.infrastructure.db.models import WorkflowRun
from pharma_ai.main import app

pytestmark = needs_db

URL = "/api/v1/research/reviews"
QUESTION = "What is the acceptable intake for NDMA?"
PASSAGE = "The acceptable intake for NDMA is 96 ng/day."
RATIONALE = "NDMA 96 ng/day matches FDA Appendix B, page 43; the quote is complete."
ACTIONS = ["ANSWER_REVIEW_APPROVED", "ANSWER_REVIEW_REJECTED", "ANSWER_EVIDENCE_REQUESTED"]

ASKER = headers(["scientist"], ["PRJ-001", "PRJ-002"], sub="asker-1")
REVIEWER = headers(["reviewer"], ["PRJ-001"], sub="reviewer-1")


@pytest.fixture
def run_id(api: TestClient, session: Session, embedder: FakeEmbedder) -> str:
    """A real answer in PRJ-001, waiting for review."""
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    response = api.post(
        "/api/v1/research/query", json={"query": QUESTION, "project_id": "PRJ-001"}, headers=ASKER
    )
    assert response.status_code == 200 and response.json()["verified"] is True
    return response.json()["workflow_run_id"]


def body(run: str, **changes) -> dict:
    return {
        "workflow_run_id": run,
        "project_id": "PRJ-001",
        "decision": "APPROVED",
        "rationale": RATIONALE,
        **changes,
    }


def review_events(client: TestClient) -> list[dict]:
    return [e for action in ACTIONS for e in events(client, action=action)]


def status_of(session: Session, run: str) -> str:
    row = session.get(WorkflowRun, run)
    session.refresh(row)
    return row.status


# --- recording a decision -----------------------------------------------------------------------


def test_a_reviewer_records_a_decision_and_the_stored_answer_moves_to_that_status(
    api: TestClient, session: Session, run_id: str
) -> None:
    assert status_of(session, run_id) == "PENDING_EXPERT_REVIEW"
    response = api.post(URL, json=body(run_id), headers=REVIEWER)
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["decision"] == "APPROVED" and out["rationale"] == RATIONALE
    assert out["workflow_run_id"] == run_id and out["project_id"] == "PRJ-001"
    assert out["cited_sources"] == [f"{NITRO} (v1, p. 1)"]
    assert out["reviewer_email"] == "reviewer-1@pharma.local" and out["reviewer_role"] == "reviewer"
    assert out["timestamp"].endswith("UTC")

    run = session.get(WorkflowRun, run_id)
    session.refresh(run)
    assert run.status == "APPROVED" and run.decided_by == "reviewer-1" and run.decided_at

    (event,) = events(api, action="ANSWER_REVIEW_APPROVED")
    assert out["review_id"] == out["audit_event_id"] == event["event_id"]
    assert event["outcome"] == "SUCCESS" and event["project_id"] == "PRJ-001"
    assert event["resource_type"] == "workflow_run" and event["resource_id"] == run_id
    assert event["actor_subject"] == "reviewer-1" and event["actor_roles"] == ["reviewer"]
    details = event["details"]
    assert details["decision"] == "APPROVED" and details["rationale"] == RATIONALE
    assert details["previous_status"] == "PENDING_EXPERT_REVIEW"
    assert [c["source_id"] for c in details["cited"]] == [NITRO]
    assert details["cited"][0]["chunk_id"] == run.citations[0]["chunk_id"]


@pytest.mark.parametrize(
    "decision, action",
    [
        ("APPROVED", "ANSWER_REVIEW_APPROVED"),
        ("REJECTED", "ANSWER_REVIEW_REJECTED"),
        ("EVIDENCE_REQUESTED", "ANSWER_EVIDENCE_REQUESTED"),
    ],
)
def test_each_decision_has_its_own_action_and_status(
    api: TestClient, session: Session, run_id: str, decision: str, action: str
) -> None:
    assert api.post(URL, json=body(run_id, decision=decision), headers=REVIEWER).status_code == 201
    assert [e["action"] for e in review_events(api)] == [action]
    assert status_of(session, run_id) == decision


def test_qa_may_review_too(api: TestClient, run_id: str) -> None:
    qa = headers(["qa"], ["PRJ-001"], sub="qa-1")
    assert api.post(URL, json=body(run_id), headers=qa).status_code == 201


def test_the_latest_decision_wins_and_every_decision_is_kept(
    api: TestClient, session: Session, run_id: str
) -> None:
    api.post(
        URL,
        json=body(run_id, decision="EVIDENCE_REQUESTED", rationale="Need page 44"),
        headers=REVIEWER,
    )
    api.post(URL, json=body(run_id, rationale="Page 44 checked"), headers=REVIEWER)
    assert status_of(session, run_id) == "APPROVED"
    history = [e for e in events(api, resource_id=run_id) if e["action"] in ACTIONS]
    assert sorted(e["action"] for e in history) == [
        "ANSWER_EVIDENCE_REQUESTED",
        "ANSWER_REVIEW_APPROVED",
    ]
    approved = next(e for e in history if e["action"] == "ANSWER_REVIEW_APPROVED")
    assert approved["details"]["previous_status"] == "EVIDENCE_REQUESTED"


def test_the_reviewer_and_the_sources_come_from_the_server_not_the_request(
    api: TestClient, run_id: str
) -> None:
    claims = {
        "reviewer_email": "someone.else@pharma.local",
        "reviewer_role": "admin",
        "actor_id": "someone-else",
        "timestamp": "2020-01-01 00:00:00 UTC",
        "query": "a different question",
        "cited_sources": ["MADE-UP-SOURCE (v9, p. 99)"],
    }
    response = api.post(URL, json=body(run_id, **claims), headers=REVIEWER)
    assert response.status_code == 201
    out = response.json()
    assert out["reviewer_email"] == "reviewer-1@pharma.local" and out["reviewer_role"] == "reviewer"
    assert out["cited_sources"] == [f"{NITRO} (v1, p. 1)"]
    (event,) = events(api, action="ANSWER_REVIEW_APPROVED")
    dumped = json.dumps(event)
    assert (
        "someone" not in dumped and "MADE-UP" not in dumped and "different question" not in dumped
    )


# --- the run must be real, and in the project ---------------------------------------------------


def test_a_decision_on_a_run_that_does_not_exist_is_refused_and_recorded_as_a_failure(
    api: TestClient,
) -> None:
    response = api.post(URL, json=body("RUN-DOESNOTEXIST"), headers=REVIEWER)
    assert response.status_code == 404
    (event,) = events(api, action="ANSWER_REVIEW_APPROVED")
    assert event["outcome"] == "FAILURE" and event["details"]["reason"] == "run not found"


def test_a_run_of_another_project_looks_exactly_like_a_run_that_does_not_exist(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-002", [PASSAGE])
    other = api.post(
        "/api/v1/research/query", json={"query": QUESTION, "project_id": "PRJ-002"}, headers=ASKER
    ).json()["workflow_run_id"]
    both = headers(["reviewer"], ["PRJ-001", "PRJ-002"], sub="reviewer-both")
    wrong_project = api.post(URL, json=body(other), headers=both)  # says PRJ-001, run is PRJ-002's
    missing = api.post(URL, json=body("RUN-DOESNOTEXIST"), headers=both)
    assert wrong_project.status_code == missing.status_code == 404
    assert wrong_project.json() == missing.json()
    assert status_of(session, other) == "PENDING_EXPERT_REVIEW"


def test_a_reviewer_from_another_project_cannot_decide_and_the_run_is_untouched(
    api: TestClient, session: Session, run_id: str
) -> None:
    outsider = headers(["reviewer"], ["PRJ-002"], sub="reviewer-2")
    response = api.post(URL, json=body(run_id), headers=outsider)
    assert response.status_code == 403
    assert status_of(session, run_id) == "PENDING_EXPERT_REVIEW"
    assert review_events(api) == []


def test_a_project_that_does_not_exist_is_refused_like_one_you_may_not_see(
    api: TestClient, run_id: str
) -> None:
    unknown = api.post(URL, json=body(run_id, project_id="PRJ-404"), headers=REVIEWER)
    forbidden = api.post(URL, json=body(run_id, project_id="PRJ-002"), headers=REVIEWER)
    assert unknown.status_code == forbidden.status_code == 403
    assert unknown.json() == forbidden.json()


# --- who may not --------------------------------------------------------------------------------


@pytest.mark.parametrize("roles", [["scientist"], ["admin"], [], ["scientist", "admin"]])
def test_only_reviewers_and_qa_may_record_a_decision(
    api: TestClient, session: Session, run_id: str, roles: list[str]
) -> None:
    response = api.post(URL, json=body(run_id), headers=headers(roles, ["PRJ-001"], sub="nobody"))
    assert response.status_code == 403
    assert status_of(session, run_id) == "PENDING_EXPERT_REVIEW"
    assert review_events(api) == []
    (denied,) = events(api, action="ACCESS_DENIED")
    assert denied["outcome"] == "DENIED" and denied["actor_subject"] == "nobody"


def test_a_decision_needs_a_login(api: TestClient, run_id: str) -> None:
    assert api.post(URL, json=body(run_id)).status_code == 401
    assert review_events(api) == []


# --- bad requests (each must change nothing) ----------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"rationale": ""},
        {"rationale": "   \n\t"},
        {"rationale": "x" * 4001},
        {"decision": "MAYBE"},
        {"decision": "approved"},
        {"decision": None},
        {"workflow_run_id": ""},
        {"workflow_run_id": "has space"},
        {"workflow_run_id": "../../etc"},
        {"workflow_run_id": "R" * 65},
        {"project_id": ""},
        {"project_id": "P" * 65},
    ],
)
def test_malformed_decisions_are_refused_and_change_nothing(
    api: TestClient, session: Session, run_id: str, changes: dict
) -> None:
    payload = body(run_id)
    payload.update(changes)
    assert api.post(URL, json=payload, headers=REVIEWER).status_code == 422
    assert status_of(session, run_id) == "PENDING_EXPERT_REVIEW"
    assert review_events(api) == []


@pytest.mark.parametrize("missing", ["workflow_run_id", "project_id", "decision", "rationale"])
def test_every_required_field_is_required(api: TestClient, run_id: str, missing: str) -> None:
    payload = body(run_id)
    del payload[missing]
    assert api.post(URL, json=payload, headers=REVIEWER).status_code == 422
    assert review_events(api) == []


def test_the_longest_allowed_rationale_is_accepted(api: TestClient, run_id: str) -> None:
    assert (
        api.post(URL, json=body(run_id, rationale="r" * 4000), headers=REVIEWER).status_code == 201
    )


# --- fail closed --------------------------------------------------------------------------------


def test_if_the_audit_trail_cannot_be_written_the_decision_is_not_saved(
    api: TestClient, session: Session, run_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenTrail:
        def record_event(self, **_kwargs):
            raise AuditUnavailableError("the trail is down")

    committed: list[bool] = []
    monkeypatch.setattr(session, "commit", lambda: committed.append(True))
    app.dependency_overrides[get_audit_service] = lambda: BrokenTrail()
    try:
        response = api.post(URL, json=body(run_id), headers=REVIEWER)
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert response.status_code == 503
    assert RATIONALE not in response.text
    assert committed == []  # the status change was never committed
