"""Tests for the approved-source register endpoint, which feeds the upload form (decision log D24).

Needs TEST_DATABASE_URL (an empty throwaway database; see test_db_integration.py). The register is
seeded from the real manifest, so held and not-yet-loaded sources are the real ones.
"""

from __future__ import annotations

import pytest
from db_fixtures import NITRO, PV, add_document, events, headers, needs_db
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.audit.service import AuditUnavailableError, get_audit_service
from pharma_ai.infrastructure.db.repositories import DocumentRepository
from pharma_ai.infrastructure.db.seed import DEFAULT_MANIFEST, read_manifest
from pharma_ai.main import app

pytestmark = needs_db

URL = "/api/v1/sources"
ADMIN = headers(["admin"], ["PRJ-001", "PRJ-002"], sub="register-admin")
HELD, DEFERRED = "ICH-Q8R2", "IN-NDCT-2019"
SOURCES = len(read_manifest(DEFAULT_MANIFEST))


def register(api: TestClient) -> dict[str, dict]:
    response = api.get(URL, headers=ADMIN)
    assert response.status_code == 200, response.text
    return {row["source_id"]: row for row in response.json()}


def test_the_register_lists_every_source_with_what_is_stored_and_who_can_search_it(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    # Linked to PRJ-002 first, so the sorted answer cannot be the order they were added in.
    shared = add_document(session, embedder, NITRO, "PRJ-002", ["NDMA is limited."])
    DocumentRepository(session).grant_project_access(shared.document_id, "PRJ-001")
    add_document(session, embedder, PV, "PRJ-002", ["Process validation has three stages."])
    rows = register(api)

    assert len(rows) == SOURCES  # the whole register, held and deferred sources included
    assert (rows[NITRO]["stored"], rows[NITRO]["latest_version"]) == (True, 1)
    assert rows[NITRO]["project_ids"] == ["PRJ-001", "PRJ-002"]
    assert rows[PV]["project_ids"] == ["PRJ-002"]
    assert rows[NITRO]["eligible"] is True and rows[NITRO]["authority"] == "FDA"


def test_a_held_source_is_listed_as_held_with_its_reason(api: TestClient) -> None:
    held = register(api)[HELD]
    assert held["eligible"] is False and held["tier"] == "hold"
    assert held["hold_reason"] and held["stored"] is False and held["project_ids"] == []


def test_an_eligible_source_with_no_file_yet_is_listed_as_not_stored(api: TestClient) -> None:
    deferred = register(api)[DEFERRED]
    assert deferred["eligible"] is True and deferred["tier"] == "deferred"
    assert (deferred["stored"], deferred["latest_version"], deferred["project_ids"]) == (
        False,
        None,
        [],
    )


def test_the_latest_version_number_is_the_highest_stored(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    first = add_document(session, embedder, NITRO, "PRJ-001", ["Version one text."])
    add_document(session, embedder, NITRO, "PRJ-001", ["Version two text."], version_number=2,
                 document=first)  # fmt: skip
    assert register(api)[NITRO]["latest_version"] == 2


@pytest.mark.parametrize("roles", [["scientist"], ["reviewer"], ["qa"]])
def test_only_an_administrator_can_read_the_register(api: TestClient, roles: list[str]) -> None:
    caller = headers(roles, ["PRJ-001", "PRJ-002"], sub="not-an-admin")
    response = api.get(URL, headers=caller)
    assert response.status_code == 403
    assert "ICH-" not in response.text
    denied = events(api, action="ACCESS_DENIED", actor="not-an-admin")
    assert [e["outcome"] for e in denied] == ["DENIED"]


def test_without_a_token_the_register_is_not_readable(api: TestClient) -> None:
    assert api.get(URL).status_code == 401


def test_listing_the_register_is_recorded_in_the_audit_trail(api: TestClient) -> None:
    register(api)
    listed = events(api, action="SOURCE_REGISTER_LISTED")
    assert [(e["actor_subject"], e["details"]["count"]) for e in listed] == [
        ("register-admin", SOURCES)
    ]


def test_if_the_audit_trail_cannot_be_written_nothing_is_returned(api: TestClient) -> None:
    class BrokenTrail:
        def record_event(self, **_kwargs):
            raise AuditUnavailableError("the trail is down")

    app.dependency_overrides[get_audit_service] = lambda: BrokenTrail()
    try:
        response = api.get(URL, headers=ADMIN)
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert response.status_code == 503
    assert "ICH-" not in response.text
