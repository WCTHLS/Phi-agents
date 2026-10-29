"""Tests for the immutable audit logging API and service (S1-11, S1-12, S1-13).

Verifies:
- Append-only recording of audit events.
- Role-based authorization: admin only for GET /api/v1/audit/events (403 for scientist/reviewer).
- 401 for unauthenticated calls.
- Filtering by project_id, actor, action, and pagination.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from pharma_ai.main import app

client = TestClient(app)


def test_audit_events_endpoint_requires_authentication():
    """Unauthenticated request must return 401."""
    res = client.get("/api/v1/audit/events")
    assert res.status_code == 401


def test_audit_events_endpoint_refuses_scientist_role(make_token: Callable[..., str]):
    """Scientist role must be refused with 403 Forbidden."""
    token = make_token(roles=["scientist"])
    res = client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403
    assert "Requires role: admin" in res.json().get("detail", "")


def test_audit_events_endpoint_refuses_reviewer_role(make_token: Callable[..., str]):
    """Reviewer role must be refused with 403 Forbidden."""
    token = make_token(roles=["reviewer"])
    res = client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403
    assert "Requires role: admin" in res.json().get("detail", "")


def test_admin_can_read_audit_events(make_token: Callable[..., str]):
    """Admin role must receive 200 with list of audit events."""
    token = make_token(roles=["admin"])
    res = client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 4
    assert len(data["items"]) >= 4

    first = data["items"][0]
    assert "event_id" in first
    assert "timestamp" in first
    assert "actor_id" in first
    assert "action" in first


def test_admin_can_filter_audit_events_by_project(make_token: Callable[..., str]):
    """Filtering by project returns only records matching that project."""
    token = make_token(roles=["admin"])
    res = client.get(
        "/api/v1/audit/events?project_id=PRJ-001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) > 0
    assert all(item["project_id"] == "PRJ-001" for item in items)


def test_admin_can_filter_audit_events_by_action(make_token: Callable[..., str]):
    """Filtering by action returns only matching audit actions."""
    token = make_token(roles=["admin"])
    res = client.get(
        "/api/v1/audit/events?action=RESEARCH_QUERY_EXECUTED",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    assert all(item["action"] == "RESEARCH_QUERY_EXECUTED" for item in items)


def test_admin_can_filter_audit_events_by_actor(make_token: Callable[..., str]):
    """Filtering by actor returns substring matching records."""
    token = make_token(roles=["admin"])
    res = client.get(
        "/api/v1/audit/events?actor=thorne",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1
    assert all("thorne" in item["actor_id"].lower() for item in items)


def test_audit_pagination(make_token: Callable[..., str]):
    """Audit events endpoint respects page and page_size."""
    token = make_token(roles=["admin"])
    res = client.get(
        "/api/v1/audit/events?page=1&page_size=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total"] >= 4


def test_authenticated_user_can_record_audit_event(make_token: Callable[..., str]):
    """Authenticated user (e.g. scientist or reviewer) can record an audit trail event."""
    token = make_token(sub="scientist-042", roles=["scientist"], projects=["PRJ-001"])
    payload = {
        "action": "REPORT_DRAFT_GENERATED",
        "resource_type": "report",
        "resource_id": "CTD-3.2.P.2",
        "project_id": "PRJ-001",
        "details": {"notes": "Generated initial formulation review draft"},
    }
    res = client.post(
        "/api/v1/audit/events",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    created = res.json()
    assert created["action"] == "REPORT_DRAFT_GENERATED"
    assert created["project_id"] == "PRJ-001"
    assert created["actor_role"] == "scientist"
    assert created["event_id"].startswith("AUD-")
