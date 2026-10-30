"""Tests for the audit log API (S1-11, S1-12, S1-13).

Verifies:
- Role-based authorization: admin only for GET /api/v1/audit/events (403 for scientist/reviewer).
- 401 for unauthenticated calls.
- Filtering by project_id, actor and action, and pagination.
- There is no endpoint a client can use to write an audit event.

Events are created the real way: by doing things through the API as a scientist. (Behaviour of
each recorded action is tested in test_audit_actions.py.)
"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from pharma_ai.main import app

client = TestClient(app)

QUESTION = {"query": "What are the Class 1 elemental impurities in ICH Q3D?"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scientist_activity(make_token: Callable[..., str]) -> None:
    """A scientist lists projects, opens one and asks a question: three audited actions."""
    headers = _bearer(make_token(sub="scientist-042", roles=["scientist"], projects=["PRJ-001"]))
    assert client.get("/api/v1/projects", headers=headers).status_code == 200
    assert client.get("/api/v1/projects/PRJ-001", headers=headers).status_code == 200
    assert client.post("/api/v1/research/query", json=QUESTION, headers=headers).status_code == 200


def test_audit_events_endpoint_requires_authentication():
    """Unauthenticated request must return 401."""
    res = client.get("/api/v1/audit/events")
    assert res.status_code == 401


def test_audit_events_endpoint_refuses_scientist_role(make_token: Callable[..., str]):
    """Scientist role must be refused with 403 Forbidden."""
    token = make_token(roles=["scientist"])
    res = client.get("/api/v1/audit/events", headers=_bearer(token))
    assert res.status_code == 403
    assert "Requires role: admin" in res.json().get("detail", "")


def test_audit_events_endpoint_refuses_reviewer_role(make_token: Callable[..., str]):
    """Reviewer role must be refused with 403 Forbidden."""
    token = make_token(roles=["reviewer"])
    res = client.get("/api/v1/audit/events", headers=_bearer(token))
    assert res.status_code == 403
    assert "Requires role: admin" in res.json().get("detail", "")


def test_admin_can_read_audit_events(make_token: Callable[..., str]):
    """Admin role must receive 200 with the events that real actions produced."""
    _scientist_activity(make_token)
    res = client.get("/api/v1/audit/events", headers=_bearer(make_token(roles=["admin"])))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3

    first = data["items"][0]
    for field in ("event_id", "timestamp", "actor_id", "actor_role", "action", "outcome"):
        assert field in first
    assert first["action"] == "RESEARCH_QUERY_EXECUTED"  # newest first


def test_empty_trail_returns_an_empty_page(make_token: Callable[..., str]):
    """A fresh system has no invented history."""
    res = client.get("/api/v1/audit/events", headers=_bearer(make_token(roles=["admin"])))
    assert res.json()["total"] == 0
    assert res.json()["items"] == []


def test_admin_can_filter_audit_events_by_project(make_token: Callable[..., str]):
    """Filtering by project returns only records matching that project."""
    _scientist_activity(make_token)
    res = client.get(
        "/api/v1/audit/events?project_id=PRJ-001",
        headers=_bearer(make_token(roles=["admin"])),
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1  # only PROJECT_VIEWED names a project
    assert all(item["project_id"] == "PRJ-001" for item in items)


def test_admin_can_filter_audit_events_by_action(make_token: Callable[..., str]):
    """Filtering by action returns only matching audit actions."""
    _scientist_activity(make_token)
    res = client.get(
        "/api/v1/audit/events?action=RESEARCH_QUERY_EXECUTED",
        headers=_bearer(make_token(roles=["admin"])),
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert all(item["action"] == "RESEARCH_QUERY_EXECUTED" for item in items)


def test_admin_can_filter_audit_events_by_actor(make_token: Callable[..., str]):
    """Filtering by actor matches part of the person's email or stable ID."""
    _scientist_activity(make_token)
    res = client.get(
        "/api/v1/audit/events?actor=scientist-042",
        headers=_bearer(make_token(roles=["admin"])),
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3
    assert all("scientist-042" in item["actor_subject"] for item in items)


def test_audit_pagination(make_token: Callable[..., str]):
    """Audit events endpoint respects page and page_size."""
    _scientist_activity(make_token)
    res = client.get(
        "/api/v1/audit/events?page=1&page_size=2",
        headers=_bearer(make_token(roles=["admin"])),
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total"] == 3


def test_clients_cannot_write_audit_events(make_token: Callable[..., str]):
    """The old endpoint that let any caller create an event is gone: events are server-written."""
    token = make_token(sub="scientist-042", roles=["scientist"], projects=["PRJ-001"])
    payload = {
        "action": "REPORT_SECTION_APPROVED",
        "resource_type": "report_section",
        "project_id": "PRJ-002",
        "details": {"electronic_signature": "forged"},
    }
    res = client.post("/api/v1/audit/events", json=payload, headers=_bearer(token))
    assert res.status_code == 405

    admin = _bearer(make_token(roles=["admin"]))
    listed = client.get("/api/v1/audit/events?action=REPORT_SECTION_APPROVED", headers=admin)
    assert listed.json()["total"] == 0
