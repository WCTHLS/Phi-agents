"""What gets written to the audit trail when real things happen (S1-11).

Plain language: the server itself records who did what, when, and with what result. The person is
always taken from the verified login token. These tests use the in-memory trail; the same
behaviour against a real database is tested in test_db_integration.py.
"""

import hashlib
import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from pharma_ai.core.config import Settings
from pharma_ai.infrastructure.audit.recorder import fingerprint_text
from pharma_ai.infrastructure.audit.service import (
    AuditActor,
    AuditConfigurationError,
    AuditUnavailableError,
    InMemoryAuditLogService,
    build_audit_service,
    get_audit_service,
    validate_event_fields,
)
from pharma_ai.main import app

client = TestClient(app)
SECRET_QUESTION = "Does excipient ZX-9981 in our confidential tablet cause nitrite problems?"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _events(make_token: Callable[..., str], query: str = "") -> list[dict]:
    admin = _bearer(make_token(sub="auditor-1", roles=["admin"]))
    res = client.get(f"/api/v1/audit/events?page_size=100{query}", headers=admin)
    assert res.status_code == 200
    return res.json()["items"]


# --- successful actions -----------------------------------------------------------------------


def test_listing_projects_is_recorded_with_the_callers_verified_identity(make_token):
    token = make_token(sub="scientist-7", roles=["scientist"], projects=["PRJ-001"])
    assert client.get("/api/v1/projects", headers=_bearer(token)).status_code == 200

    (event,) = _events(make_token, "&action=PROJECT_LISTED")
    assert event["actor_subject"] == "scientist-7"  # the stable ID from the token
    assert event["actor_email"] == "scientist-7@pharma.local"
    assert event["actor_id"] == "scientist-7@pharma.local"  # what the viewer shows
    assert event["outcome"] == "SUCCESS"
    assert event["details"] == {"count": 1}
    assert event["event_id"] and event["timestamp"].endswith(" UTC")


def test_viewing_a_project_is_recorded_against_that_project(make_token):
    token = make_token(roles=["scientist"], projects=["PRJ-001"])
    assert client.get("/api/v1/projects/PRJ-001", headers=_bearer(token)).status_code == 200

    (event,) = _events(make_token, "&action=PROJECT_VIEWED")
    assert (event["resource_type"], event["resource_id"], event["project_id"]) == (
        "project",
        "PRJ-001",
        "PRJ-001",
    )


def test_listing_the_source_register_is_recorded(make_token):
    token = make_token(roles=["scientist"])
    assert client.get("/api/v1/research/sources", headers=_bearer(token)).status_code == 200
    (event,) = _events(make_token, "&action=RESEARCH_SOURCES_LISTED")
    assert event["resource_type"] == "source_register"
    assert event["details"]["count"] >= 1


def test_a_research_query_is_recorded_without_the_question_text(make_token):
    token = make_token(roles=["scientist"])
    res = client.post(
        "/api/v1/research/query", json={"query": SECRET_QUESTION}, headers=_bearer(token)
    )
    assert res.status_code == 200

    (event,) = _events(make_token, "&action=RESEARCH_QUERY_EXECUTED")
    assert event["details"]["query"] == {
        "sha256": hashlib.sha256(SECRET_QUESTION.encode()).hexdigest(),
        "length": len(SECRET_QUESTION),
    }
    assert isinstance(event["details"]["retrieved_source_ids"], list)
    assert {"top_k", "citation_count", "evidence_gap_count", "verified"} <= set(event["details"])
    # The question must not appear anywhere in any stored record, in any field.
    everything = json.dumps(_events(make_token))
    assert "ZX-9981" not in everything
    assert "confidential" not in everything


def test_reading_the_audit_log_is_itself_recorded(make_token):
    admin = _bearer(make_token(sub="auditor-1", roles=["admin"]))
    first = client.get("/api/v1/audit/events?action=PROJECT_LISTED", headers=admin)
    assert first.json()["total"] == 0  # a page never contains the event for its own request

    # The second read shows the record the first read left behind.
    (event,) = _events(make_token, "&action=AUDIT_LOG_VIEWED")
    assert event["actor_subject"] == "auditor-1"
    assert event["details"]["filters"] == {"action": "PROJECT_LISTED"}


# --- refused and failed actions ---------------------------------------------------------------


def test_a_refused_project_request_is_recorded_as_denied(make_token):
    token = make_token(sub="scientist-9", roles=["scientist"], projects=["PRJ-001"])
    res = client.get("/api/v1/projects/PRJ-002", headers=_bearer(token))
    assert res.status_code == 403

    (event,) = _events(make_token, "&action=ACCESS_DENIED")
    assert event["outcome"] == "DENIED"
    assert event["actor_subject"] == "scientist-9"
    assert event["project_id"] == "PRJ-002"  # the project they tried to reach
    assert event["resource_id"] == "GET /api/v1/projects/PRJ-002"
    assert event["details"]["status"] == 403


def test_a_refused_role_check_is_recorded_as_denied(make_token):
    token = make_token(sub="scientist-9", roles=["scientist"])
    assert client.get("/api/v1/audit/events", headers=_bearer(token)).status_code == 403

    (event,) = _events(make_token, "&action=ACCESS_DENIED")
    assert event["resource_id"] == "GET /api/v1/audit/events"
    assert "Requires role: admin" in event["details"]["reason"]


def test_requests_with_no_valid_login_are_not_recorded(make_token):
    """Nobody was identified, so there is no one to attribute the request to (it is a 401)."""
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/projects", headers=_bearer("not-a-real-token")).status_code == 401
    assert _events(make_token) == []  # nothing at all was recorded for those two requests


def test_a_missing_project_is_recorded_as_a_failure(make_token):
    token = make_token(roles=["scientist"], projects=["PRJ-777"])  # a member, but no such project
    assert client.get("/api/v1/projects/PRJ-777", headers=_bearer(token)).status_code == 404
    (event,) = _events(make_token, "&action=PROJECT_VIEWED")
    assert event["outcome"] == "FAILURE"
    assert event["details"] == {"reason": "not found"}


# --- who is recorded --------------------------------------------------------------------------


def test_all_roles_are_recorded_in_a_fixed_order(make_token):
    token = make_token(sub="multi-1", roles=["scientist", "reviewer", "admin"])
    for _ in range(3):
        client.get("/api/v1/projects", headers=_bearer(token))
    events = _events(make_token, "&action=PROJECT_LISTED")
    assert {e["actor_role"] for e in events} == {"admin,reviewer,scientist"}
    assert {tuple(e["actor_roles"]) for e in events} == {("admin", "reviewer", "scientist")}


def test_the_caller_cannot_choose_who_is_recorded(make_token):
    """Extra fields in a request are ignored: the actor always comes from the verified token."""
    token = make_token(sub="scientist-5", roles=["scientist"])
    body = {
        "query": "residual solvents Q3C class",
        "actor_id": "someone-else",
        "actor_role": "admin",
    }
    assert (
        client.post("/api/v1/research/query", json=body, headers=_bearer(token)).status_code == 200
    )
    (event,) = _events(make_token, "&action=RESEARCH_QUERY_EXECUTED")
    assert event["actor_subject"] == "scientist-5"
    assert event["actor_role"] == "scientist"


# --- reconstruction ---------------------------------------------------------------------------


def test_the_history_of_one_object_can_be_reconstructed(make_token):
    a = _bearer(make_token(sub="alice", roles=["scientist"], projects=["PRJ-001", "PRJ-002"]))
    b = _bearer(make_token(sub="bob", roles=["scientist"], projects=["PRJ-001"]))
    client.get("/api/v1/projects/PRJ-001", headers=a)
    client.get("/api/v1/projects/PRJ-002", headers=a)
    client.get("/api/v1/projects/PRJ-001", headers=b)

    events = _events(make_token, "&resource_type=project&resource_id=PRJ-001")
    assert [e["actor_subject"] for e in events] == ["bob", "alice"]  # newest first
    assert {e["resource_id"] for e in events} == {"PRJ-001"}


def test_events_can_be_limited_to_a_time_window(make_token):
    token = make_token(roles=["scientist"])
    client.get("/api/v1/projects", headers=_bearer(token))
    assert len(_events(make_token, "&since=2000-01-01T00:00:00")) >= 1
    assert _events(make_token, "&since=2999-01-01T00:00:00") == []
    assert _events(make_token, "&until=2000-01-01T00:00:00") == []


# --- failure behaviour ------------------------------------------------------------------------


class _BrokenTrail(InMemoryAuditLogService):
    def record_event(self, **_: object):
        raise AuditUnavailableError("simulated outage")


def test_if_the_trail_cannot_be_written_the_action_is_not_completed(make_token):
    app.dependency_overrides[get_audit_service] = _BrokenTrail
    try:
        token = make_token(roles=["scientist"], projects=["PRJ-001"])
        res = client.get("/api/v1/projects", headers=_bearer(token))
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert res.status_code == 503
    assert "PRJ-001" not in res.text  # no data is returned without its record
    assert "not completed" in res.json()["detail"]


def test_a_refused_request_is_still_refused_if_the_trail_is_down(make_token):
    app.dependency_overrides[get_audit_service] = _BrokenTrail
    try:
        token = make_token(roles=["scientist"], projects=["PRJ-001"])
        res = client.get("/api/v1/projects/PRJ-002", headers=_bearer(token))
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert res.status_code == 403  # access is never granted because auditing failed


# --- health and configuration -----------------------------------------------------------------


def test_readiness_is_ok_with_the_in_memory_trail():
    assert client.get("/ready").json() == {"status": "ready"}


def test_the_in_memory_trail_is_refused_outside_local_and_test():
    with pytest.raises(AuditConfigurationError, match="not allowed"):
        build_audit_service(
            Settings(environment="production", audit_backend="memory", _env_file=None)
        )
    assert isinstance(
        build_audit_service(Settings(environment="local", audit_backend="memory", _env_file=None)),
        InMemoryAuditLogService,
    )


# --- small pieces -----------------------------------------------------------------------------


def test_fingerprint_hides_the_text_but_is_stable():
    first = fingerprint_text("abc")
    assert first == fingerprint_text("abc")
    assert first == {"sha256": hashlib.sha256(b"abc").hexdigest(), "length": 3}
    assert "abc" not in json.dumps(first)


@pytest.mark.parametrize(
    ("action", "resource_type", "outcome"),
    [("", "x", "SUCCESS"), ("A" * 129, "x", "SUCCESS"), ("A", "", "SUCCESS"), ("A", "x", "MAYBE")],
)
def test_invalid_events_are_rejected(action: str, resource_type: str, outcome: str):
    with pytest.raises(ValueError):
        validate_event_fields(action, resource_type, outcome)


def test_actor_role_label_is_sorted_and_never_empty():
    actor = AuditActor(subject="s", email=None, roles=("reviewer", "admin"))
    assert actor.role_label == "reviewer,admin"  # the caller supplies the order (Principal sorts)
    assert AuditActor(subject="s", email=None, roles=()).role_label == "user"
    long = AuditActor(subject="s", email=None, roles=tuple(f"role-number-{i}" for i in range(20)))
    assert len(long.role_label) <= 64
