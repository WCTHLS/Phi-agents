"""Tests that an audit event can be saved in the same transaction as the change it records.

These use the real database audit service and real committing sessions (not the rolled-back test
session of the other API tests), because the question is what really reaches the database.
ADR-005: a change and its audit event are saved together or not at all.

Run with TEST_DATABASE_URL set to an empty throwaway database (see test_db_integration.py).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from db_fixtures import NITRO, add_document, headers, needs_db
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from pharma_ai.api.routes.search import embedder_provider
from pharma_ai.infrastructure.audit.database import DatabaseAuditLogService
from pharma_ai.infrastructure.audit.service import (
    AuditActor,
    AuditUnavailableError,
    get_audit_service,
)
from pharma_ai.infrastructure.db.models import AuditEvent, Document, Project, WorkflowRun
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import get_db_session
from pharma_ai.main import app

pytestmark = needs_db

COMMIT_FAILS = {"on": False}  # when on, the API request's own commit fails (see real_api)
ACTOR = AuditActor(subject="tx-test", email="tx@pharma.local", roles=("reviewer",))
PASSAGE = "The acceptable intake for NDMA is 96 ng/day."


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def count_events(factory: sessionmaker[Session], action: str) -> int:
    with factory() as other:  # a different connection: sees only what has been committed
        return (
            other.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == action)
            )
            or 0
        )


def make_run_row(run_id: str) -> WorkflowRun:
    return WorkflowRun(
        run_id=run_id,
        project_id="PRJ-001",
        asked_by="tx-test",
        question="q",
        question_sha256="0" * 64,
        answer="a",
        answer_mode="quotes-only",
        verified=True,
        coverage=1.0,
        embedding_model="m",
    )


@pytest.fixture
def seeded(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        if s.get(Project, "PRJ-001") is None:
            seed_all(s)
            s.commit()


# --- the audit service on its own ---------------------------------------------------------------


def test_an_event_given_a_session_is_saved_only_when_that_session_commits(
    factory: sessionmaker[Session],
) -> None:
    service = DatabaseAuditLogService(factory)
    with factory() as caller:
        event = service.record_event(
            actor=ACTOR, action="TX_JOINED_COMMIT", resource_type="test", session=caller
        )
        assert event.event_id and event.timestamp.endswith("UTC")  # returned, with its id
        assert count_events(factory, "TX_JOINED_COMMIT") == 0  # not yet visible to anyone else
        caller.commit()
    assert count_events(factory, "TX_JOINED_COMMIT") == 1


def test_an_event_given_a_session_disappears_if_the_caller_rolls_back(
    factory: sessionmaker[Session],
) -> None:
    service = DatabaseAuditLogService(factory)
    with factory() as caller:
        service.record_event(
            actor=ACTOR, action="TX_JOINED_ROLLBACK", resource_type="test", session=caller
        )
        caller.rollback()
    assert count_events(factory, "TX_JOINED_ROLLBACK") == 0


def test_an_event_without_a_session_is_saved_at_once_as_before(
    factory: sessionmaker[Session],
) -> None:
    DatabaseAuditLogService(factory).record_event(
        actor=ACTOR, action="TX_OWN_SESSION", resource_type="test"
    )
    assert count_events(factory, "TX_OWN_SESSION") == 1


def test_an_event_the_database_refuses_raises_and_the_callers_change_is_not_saved(
    factory: sessionmaker[Session], seeded: None
) -> None:
    service = DatabaseAuditLogService(factory)
    too_long = AuditActor(subject="x" * 200, email=None, roles=("reviewer",))  # column is 128
    with factory() as caller:
        caller.add(make_run_row("RUN-TXREFUSED"))
        with pytest.raises(AuditUnavailableError):
            service.record_event(
                actor=too_long, action="TX_REFUSED", resource_type="test", session=caller
            )
        caller.rollback()  # what the API does: the request ends without a commit
    with factory() as other:
        assert other.get(WorkflowRun, "RUN-TXREFUSED") is None
    assert count_events(factory, "TX_REFUSED") == 0


# --- through the API ----------------------------------------------------------------------------


@pytest.fixture
def real_api(
    factory: sessionmaker[Session], seeded: None, embedder: FakeEmbedder
) -> Iterator[tuple[TestClient, DatabaseAuditLogService]]:
    """The API on real committing sessions and the real database audit service."""
    with factory() as setup:
        # Committed for real, so it is added once and stays for the later tests of this module.
        if setup.scalar(select(Document).where(Document.source_id == NITRO)) is None:
            add_document(setup, embedder, NITRO, "PRJ-001", [PASSAGE])
            setup.commit()

    def per_request() -> Iterator[Session]:
        with factory() as s:
            real_commit = s.commit

            def commit() -> None:
                # Fails only the request's own commit. The audit service uses sessions of its own,
                # so an event saved separately (not joined to this one) would still get through.
                if COMMIT_FAILS["on"]:
                    raise RuntimeError("the database went away at commit")
                real_commit()

            s.commit = commit  # type: ignore[method-assign]
            yield s

    service = DatabaseAuditLogService(factory)
    app.dependency_overrides[get_db_session] = per_request
    app.dependency_overrides[embedder_provider] = lambda: lambda: embedder
    app.dependency_overrides[get_audit_service] = lambda: service
    yield TestClient(app), service
    for dep in (get_db_session, embedder_provider, get_audit_service):
        app.dependency_overrides.pop(dep, None)


def ask(client: TestClient, question: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/research/query",
        json={"query": question, "project_id": "PRJ-001"},
        headers=headers(["scientist"], ["PRJ-001"], sub="asker-tx"),
    )
    assert response.status_code == 200, response.text
    return response.json()


def decide(client: TestClient, run_id: str):
    return client.post(
        "/api/v1/research/reviews",
        json={
            "workflow_run_id": run_id,
            "project_id": "PRJ-001",
            "decision": "APPROVED",
            "rationale": "Checked against the guidance.",
        },
        headers=headers(["reviewer"], ["PRJ-001"], sub="reviewer-tx"),
    )


def stored_status(factory: sessionmaker[Session], run_id: str) -> str | None:
    with factory() as other:
        run = other.get(WorkflowRun, run_id)
        return run.status if run else None


def test_a_question_and_its_audit_event_are_saved_together(
    real_api: tuple[TestClient, DatabaseAuditLogService], factory: sessionmaker[Session]
) -> None:
    client, service = real_api
    run_id = ask(client, "What is the acceptable intake for NDMA?")["workflow_run_id"]
    assert stored_status(factory, run_id) == "PENDING_EXPERT_REVIEW"
    events, _ = service.list_events(resource_id=run_id)
    assert [e.action for e in events] == ["RESEARCH_QUERY_EXECUTED"]


def test_if_the_commit_fails_neither_the_question_nor_its_event_is_saved(
    real_api: tuple[TestClient, DatabaseAuditLogService],
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = real_api  # the fixture installs the real database audit service and sessions

    def counts() -> tuple[int, int]:
        with factory() as other:
            runs = other.scalar(select(func.count()).select_from(WorkflowRun)) or 0
            events = other.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "RESEARCH_QUERY_EXECUTED")
            )
        return runs, events or 0

    before = counts()

    with monkeypatch.context() as patch:
        patch.setitem(COMMIT_FAILS, "on", True)
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/v1/research/query",
            json={"query": "What is the acceptable intake for NDMA?", "project_id": "PRJ-001"},
            headers=headers(["scientist"], ["PRJ-001"], sub="asker-tx"),
        )
    assert response.status_code == 500
    assert counts() == before  # neither a run without its event nor an event without its run


def test_a_decision_and_its_audit_event_are_saved_together(
    real_api: tuple[TestClient, DatabaseAuditLogService], factory: sessionmaker[Session]
) -> None:
    client, service = real_api
    run_id = ask(client, "What is the acceptable intake for NDMA?")["workflow_run_id"]
    assert decide(client, run_id).status_code == 201
    assert stored_status(factory, run_id) == "APPROVED"
    events, _ = service.list_events(resource_id=run_id, action="ANSWER_REVIEW_APPROVED")
    assert len(events) == 1


def test_if_the_decision_event_cannot_be_written_the_status_does_not_change(
    real_api: tuple[TestClient, DatabaseAuditLogService],
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = real_api
    run_id = ask(client, "What is the acceptable intake for NDMA?")["workflow_run_id"]
    real: Callable[..., Any] = service.record_event

    def refuse_review_events(**kwargs: Any) -> Any:
        if kwargs["action"].startswith("ANSWER_REVIEW"):
            raise AuditUnavailableError("the trail refused the event")
        return real(**kwargs)

    monkeypatch.setattr(service, "record_event", refuse_review_events)
    assert decide(client, run_id).status_code == 503
    assert stored_status(factory, run_id) == "PENDING_EXPERT_REVIEW"  # nothing half-saved
    events, _ = service.list_events(resource_id=run_id, action="ANSWER_REVIEW_APPROVED")
    assert events == []


def test_if_the_commit_fails_neither_the_status_nor_the_event_is_saved(
    real_api: tuple[TestClient, DatabaseAuditLogService],
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = real_api
    run_id = ask(client, "What is the acceptable intake for NDMA?")["workflow_run_id"]

    monkeypatch.setitem(COMMIT_FAILS, "on", True)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/research/reviews",
        json={
            "workflow_run_id": run_id,
            "project_id": "PRJ-001",
            "decision": "REJECTED",
            "rationale": "x",
        },
        headers=headers(["reviewer"], ["PRJ-001"], sub="reviewer-tx"),
    )
    monkeypatch.undo()
    assert response.status_code == 500
    assert stored_status(factory, run_id) == "PENDING_EXPERT_REVIEW"
    events, _ = service.list_events(resource_id=run_id, action="ANSWER_REVIEW_REJECTED")
    assert events == []
