"""Tests for the reviewer's queue: the stored questions waiting for review (decision log D25).

Each run is a real answer made by the pipeline. Needs TEST_DATABASE_URL (an empty throwaway
database; see test_db_integration.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from db_fixtures import NITRO, PV, add_document, events, headers, needs_db
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.audit.service import AuditUnavailableError, get_audit_service
from pharma_ai.infrastructure.db.models import WorkflowRun
from pharma_ai.main import app

pytestmark = needs_db

URL = "/api/v1/research/reviews"
PASSAGE = "The acceptable intake for NDMA is 96 ng/day."
PV_PASSAGE = "Process validation has three stages: design, qualification and verification."
Q1 = "What is the acceptable intake for NDMA?"
Q2 = "How many stages does process validation have?"
NOTHING = "Zzxq wvvk plmn qrst?"

SCIENTIST = headers(
    ["scientist"],
    ["PRJ-001", "PRJ-002"],
    sub="asker-1",
)
REVIEWER_1 = headers(["reviewer"], ["PRJ-001"], sub="reviewer-1")
REVIEWER_2 = headers(["reviewer"], ["PRJ-002"], sub="reviewer-2")


def ask(api: TestClient, question: str, project: str = "PRJ-001") -> dict:
    response = api.post(
        "/api/v1/research/query", json={"query": question, "project_id": project}, headers=SCIENTIST
    )
    assert response.status_code == 200, response.text
    return response.json()


def queue(api: TestClient, auth: dict, project: str = "PRJ-001", **params):
    return api.get(URL, params={"project_id": project, **params}, headers=auth)


def decide(api: TestClient, run: str, decision: str = "APPROVED", project: str = "PRJ-001") -> None:
    body = {
        "workflow_run_id": run,
        "project_id": project,
        "decision": decision,
        "rationale": "Checked against the source.",
    }
    assert api.post(URL, json=body, headers=REVIEWER_1).status_code == 201


@pytest.fixture
def documents(session: Session, embedder: FakeEmbedder) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", [PASSAGE])
    add_document(session, embedder, PV, "PRJ-002", [PV_PASSAGE])


def age(session: Session, run: str, minutes: int) -> None:
    """Make a run older: every row made inside one test transaction shares one timestamp."""
    when = datetime.now(UTC) - timedelta(minutes=minutes)
    session.execute(update(WorkflowRun).where(WorkflowRun.run_id == run).values(created_at=when))
    session.flush()


# --- what a reviewer sees -----------------------------------------------------------------------


def test_a_reviewer_sees_the_waiting_questions_with_the_answer_and_its_evidence(
    api: TestClient, documents: None
) -> None:
    answered = ask(api, Q1)
    response = queue(api, REVIEWER_1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["project_id"], body["status"], body["total"]) == (
        "PRJ-001",
        "PENDING_EXPERT_REVIEW",
        1,
    )
    (item,) = body["items"]
    assert item["run_id"] == answered["workflow_run_id"] and item["question"] == Q1
    assert item["answer"] == answered["answer"] and item["verified"] is True
    assert (item["asked_by"], item["withheld"], item["decided_by"]) == ("asker-1", False, None)
    (citation,) = item["citations"]
    quoted = answered["citations"][0]
    assert (
        citation["source_id"] == NITRO and citation["quoted_evidence"] == quoted["quoted_evidence"]
    )
    assert citation["page"] == quoted["page"] and citation["sha256"] == quoted["sha256"]
    assert citation["version_number"] == 1 and citation["title"]


def test_the_asker_s_email_address_is_not_in_the_listing(api: TestClient, documents: None) -> None:
    ask(api, Q1)
    text = queue(api, REVIEWER_1).text
    assert "@" not in text and "email" not in text.lower()


def test_a_question_that_got_no_answer_is_listed_and_marked_withheld(
    api: TestClient, documents: None
) -> None:
    ask(api, NOTHING)
    (item,) = queue(api, REVIEWER_1).json()["items"]
    assert item["withheld"] is True and item["citations"] == [] and item["verified"] is False


def test_the_longest_waiting_question_comes_first(
    api: TestClient, session: Session, documents: None
) -> None:
    first, second, third = (ask(api, q)["workflow_run_id"] for q in (Q1, NOTHING, Q1 + " Again"))
    age(session, second, minutes=30)  # oldest
    age(session, first, minutes=20)
    age(session, third, minutes=10)
    ids = [i["run_id"] for i in queue(api, REVIEWER_1).json()["items"]]
    assert ids == [second, first, third]


# --- who may see it -----------------------------------------------------------------------------


def test_only_the_projects_own_questions_are_listed(api: TestClient, documents: None) -> None:
    mine = ask(api, Q1, "PRJ-001")["workflow_run_id"]
    other = ask(api, Q2, "PRJ-002")["workflow_run_id"]
    assert [i["run_id"] for i in queue(api, REVIEWER_1).json()["items"]] == [mine]
    assert [i["run_id"] for i in queue(api, REVIEWER_2, "PRJ-002").json()["items"]] == [other]


def test_a_reviewer_of_another_project_is_refused_and_it_is_recorded(
    api: TestClient, documents: None
) -> None:
    ask(api, Q2, "PRJ-002")
    response = queue(api, REVIEWER_1, "PRJ-002")
    assert response.status_code == 403 and PV_PASSAGE not in response.text
    assert "process validation" not in response.text.lower()
    denied = events(api, action="ACCESS_DENIED", actor="reviewer-1")
    assert [(e["project_id"], e["outcome"]) for e in denied] == [("PRJ-002", "DENIED")]
    # A project that does not exist is refused in exactly the same way.
    assert queue(api, REVIEWER_1, "PRJ-999").status_code == 403


@pytest.mark.parametrize(
    "roles, expected",
    [
        (["reviewer"], 200),
        (["qa"], 200),
        (["scientist"], 403),  # a member of the project, but not a reviewer
        (["admin"], 403),  # the administrator does not read project questions
        (["scientist", "reviewer"], 200),
    ],
)
def test_only_reviewers_can_read_the_queue(
    api: TestClient, documents: None, roles: list[str], expected: int
) -> None:
    ask(api, Q1)
    caller = headers(roles, ["PRJ-001"], sub="role-test")
    response = queue(api, caller)
    assert response.status_code == expected
    if expected == 403:
        assert Q1 not in response.text and PASSAGE not in response.text


def test_without_a_token_the_queue_is_not_readable(api: TestClient, documents: None) -> None:
    assert api.get(URL, params={"project_id": "PRJ-001"}).status_code == 401


# --- status, paging, validation -----------------------------------------------------------------


def test_a_decided_question_leaves_the_waiting_list_and_shows_under_its_decision(
    api: TestClient, session: Session, documents: None
) -> None:
    keep = ask(api, Q1)["workflow_run_id"]
    done = ask(api, Q1 + " Again")["workflow_run_id"]
    decide(api, done, "APPROVED")
    assert [i["run_id"] for i in queue(api, REVIEWER_1).json()["items"]] == [keep]
    approved = queue(api, REVIEWER_1, status="APPROVED").json()
    assert approved["total"] == 1 and approved["items"][0]["run_id"] == done
    assert approved["items"][0]["status"] == "APPROVED"
    assert approved["items"][0]["decided_by"] == "reviewer-1" and approved["items"][0]["decided_at"]
    assert queue(api, REVIEWER_1, status="REJECTED").json()["items"] == []


def test_paging_returns_a_slice_and_the_full_count(
    api: TestClient, session: Session, documents: None
) -> None:
    runs = [ask(api, f"{Q1} {n}")["workflow_run_id"] for n in range(3)]
    for minutes, run in zip((30, 20, 10), runs, strict=True):
        age(session, run, minutes)
    page = queue(api, REVIEWER_1, limit=2, offset=1).json()
    assert page["total"] == 3 and [i["run_id"] for i in page["items"]] == runs[1:]
    assert queue(api, REVIEWER_1, limit=1).json()["items"][0]["run_id"] == runs[0]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "DONE"}, {"status": ""}],
)
def test_bad_parameters_are_refused(api: TestClient, documents: None, params: dict) -> None:
    assert queue(api, REVIEWER_1, **params).status_code == 422


def test_a_project_is_required(api: TestClient, documents: None) -> None:
    assert api.get(URL, headers=REVIEWER_1).status_code == 422


# --- the audit trail ----------------------------------------------------------------------------


def test_listing_the_queue_is_recorded_without_any_question_text(
    api: TestClient, documents: None
) -> None:
    ask(api, Q1)
    queue(api, REVIEWER_1)
    (event,) = events(api, action="REVIEW_QUEUE_LISTED")
    assert (event["actor_subject"], event["project_id"], event["outcome"]) == (
        "reviewer-1",
        "PRJ-001",
        "SUCCESS",
    )
    assert event["details"] == {"status": "PENDING_EXPERT_REVIEW", "returned": 1, "total": 1}
    assert Q1 not in json.dumps(event) and PASSAGE not in json.dumps(event)


def test_if_the_audit_trail_cannot_be_written_nothing_is_returned(
    api: TestClient, documents: None
) -> None:
    ask(api, Q1)

    class BrokenTrail:
        def record_event(self, **_kwargs):
            raise AuditUnavailableError("the trail is down")

    app.dependency_overrides[get_audit_service] = lambda: BrokenTrail()
    try:
        response = queue(api, REVIEWER_1)
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert response.status_code == 503
    assert Q1 not in response.text and PASSAGE not in response.text


def test_only_something_shaped_like_a_project_id_is_recorded_for_a_refused_request(
    api: TestClient, documents: None
) -> None:
    assert queue(api, REVIEWER_1, "PRJ 001; <script>").status_code == 403
    (event,) = events(api, action="ACCESS_DENIED", actor="reviewer-1")
    assert event["project_id"] is None
