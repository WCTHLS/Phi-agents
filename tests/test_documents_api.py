"""Tests for the document endpoints (S2-03, ADR-007), against a real PostgreSQL + pgvector database.

Run with TEST_DATABASE_URL set to an empty throwaway database (see test_db_integration.py).
The in-memory audit trail (set in conftest) lets each test read back what was recorded.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from conftest import mint_token
from fastapi.testclient import TestClient
from pdf_factory import make_pdf
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.core.config import settings
from pharma_ai.infrastructure.audit.service import AuditUnavailableError, get_audit_service
from pharma_ai.infrastructure.db.models import Document
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import get_db_session, to_sqlalchemy_url
from pharma_ai.infrastructure.storage import LocalFolderStore, get_object_store
from pharma_ai.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

ELIGIBLE, OTHER_ELIGIBLE, HELD = "ICH-Q3DR2", "FDA-PV-2011", "ICH-Q8R2"
PAGES = ["Cadmium oral permitted daily exposure 5 ug/day", "Class 1 elements and risk assessment"]
SECRET_TEXT = "Cadmium oral permitted daily exposure"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    assert TEST_DATABASE_URL
    name = make_url(TEST_DATABASE_URL).database or ""
    if "test" not in name.lower():
        pytest.fail(f"TEST_DATABASE_URL points at {name!r}; the name must contain 'test'")
    url = to_sqlalchemy_url(TEST_DATABASE_URL)
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    outer = connection.begin()
    sess = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        seed_all(sess)
        yield sess
    finally:
        sess.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def store(tmp_path: Path) -> LocalFolderStore:
    return LocalFolderStore(tmp_path / "files")


@pytest.fixture
def api(session: Session, store: LocalFolderStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_object_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.pop(get_db_session, None)
    app.dependency_overrides.pop(get_object_store, None)


def _headers(
    roles: list[str], projects: list[str] | None = None, sub: str = "user-doc-test"
) -> dict[str, str]:
    token = mint_token(sub=sub, roles=roles, projects=projects if projects is not None else [])
    return {"Authorization": f"Bearer {token}"}


ADMIN = _headers(["admin"], ["PRJ-001"], sub="admin-doc-test")


def _put(
    api: TestClient,
    source: str,
    pdf: bytes,
    *,
    projects: tuple[str, ...] = (),
    headers: dict[str, str] | None = None,
    label: str | None = None,
):
    params: list[tuple[str, str]] = [("project_id", p) for p in projects]
    if label:
        params.append(("version_label", label))
    return api.put(
        f"/api/v1/sources/{source}/versions",
        content=pdf,
        params=params,
        headers=headers if headers is not None else ADMIN,
    )


def _events(api: TestClient, action: str) -> list[dict]:
    admin = _headers(["admin"], sub="auditor-doc-test")
    page = api.get(
        "/api/v1/audit/events", params={"action": action, "page_size": 100}, headers=admin
    )
    assert page.status_code == 200
    return page.json()["items"]


def _stored(store: LocalFolderStore) -> list[str]:
    if not store.root.exists():
        return []
    return sorted(p.name for p in store.root.rglob("*") if p.is_file())


# --- uploading ----------------------------------------------------------------------------------


def test_an_admin_can_ingest_a_document_and_a_project_member_can_then_see_it(
    api: TestClient, store: LocalFolderStore
) -> None:
    pdf = make_pdf(PAGES)
    created = _put(api, ELIGIBLE, pdf, projects=("PRJ-001",))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_id"] == ELIGIBLE and body["version_number"] == 1
    assert body["page_count"] == 2 and body["text_layer"] == "yes"
    assert body["extraction_status"] == "ok" and body["already_ingested"] is False
    assert body["project_ids"] == ["PRJ-001"]
    assert len(body["sha256"]) == 64 and body["byte_size"] == len(pdf)
    assert len(_stored(store)) == 2  # the original and its extracted text

    listing = api.get(
        "/api/v1/projects/PRJ-001/documents", headers=_headers(["scientist"], ["PRJ-001"])
    )
    assert listing.status_code == 200
    (entry,) = listing.json()
    assert entry["source_id"] == ELIGIBLE and entry["title"]
    assert entry["attribution"]  # the text that must accompany any citation (ADR-002)
    assert entry["latest_version"]["version_id"] == body["version_id"]
    assert entry["latest_version"]["sha256"] == body["sha256"]


def test_the_same_file_again_returns_200_and_is_recorded_as_such(api: TestClient) -> None:
    pdf = make_pdf(PAGES)
    first = _put(api, ELIGIBLE, pdf, projects=("PRJ-001",))
    second = _put(api, ELIGIBLE, pdf, projects=("PRJ-001",))
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["already_ingested"] is True
    assert second.json()["version_id"] == first.json()["version_id"]
    assert len(_events(api, "DOCUMENT_ALREADY_INGESTED")) == 1


def test_a_changed_file_is_a_new_version_and_the_listing_shows_the_newest(api: TestClient) -> None:
    _put(api, ELIGIBLE, make_pdf(PAGES), projects=("PRJ-001",))
    second = _put(api, ELIGIBLE, make_pdf(PAGES + ["a third page of text"]), projects=("PRJ-001",))
    assert second.status_code == 201 and second.json()["version_number"] == 2
    (entry,) = api.get(
        "/api/v1/projects/PRJ-001/documents", headers=_headers(["scientist"], ["PRJ-001"])
    ).json()
    assert entry["latest_version"]["version_number"] == 2
    assert entry["latest_version"]["page_count"] == 3
    assert [v["version_number"] for v in entry["versions"]] == [2, 1]
    assert entry["preview_text"] and "Cadmium" in entry["preview_text"]


# --- who may do what ----------------------------------------------------------------------------


@pytest.mark.parametrize("roles", [["scientist"], ["reviewer"], ["qa"]])
def test_only_an_admin_can_ingest(
    api: TestClient, store: LocalFolderStore, roles: list[str]
) -> None:
    response = _put(api, ELIGIBLE, make_pdf(PAGES), headers=_headers(roles, ["PRJ-001"]))
    assert response.status_code == 403
    assert _stored(store) == []


def test_ingesting_without_signing_in_is_refused(api: TestClient, store: LocalFolderStore) -> None:
    response = api.put(f"/api/v1/sources/{ELIGIBLE}/versions", content=make_pdf(PAGES))
    assert response.status_code == 401
    assert _stored(store) == []


def test_a_project_only_sees_its_own_documents(api: TestClient) -> None:
    _put(api, ELIGIBLE, make_pdf(PAGES), projects=("PRJ-001",))
    _put(
        api,
        OTHER_ELIGIBLE,
        make_pdf(["Process validation general principles"]),
        projects=("PRJ-002",),
    )

    one = api.get(
        "/api/v1/projects/PRJ-001/documents", headers=_headers(["scientist"], ["PRJ-001"])
    )
    two = api.get(
        "/api/v1/projects/PRJ-002/documents", headers=_headers(["scientist"], ["PRJ-002"])
    )
    assert [d["source_id"] for d in one.json()] == [ELIGIBLE]
    assert [d["source_id"] for d in two.json()] == [OTHER_ELIGIBLE]


def test_a_member_of_one_project_cannot_list_another_projects_documents(api: TestClient) -> None:
    _put(
        api,
        OTHER_ELIGIBLE,
        make_pdf(["Process validation general principles"]),
        projects=("PRJ-002",),
    )
    response = api.get(
        "/api/v1/projects/PRJ-002/documents", headers=_headers(["scientist"], ["PRJ-001"])
    )
    assert response.status_code == 403
    assert "Process validation" not in response.text


def test_a_document_linked_to_no_project_is_listed_nowhere(api: TestClient) -> None:
    assert _put(api, ELIGIBLE, make_pdf(PAGES)).status_code == 201
    for project in ("PRJ-001", "PRJ-002"):
        listing = api.get(
            f"/api/v1/projects/{project}/documents", headers=_headers(["scientist"], [project])
        )
        assert listing.json() == []


# --- the licence rule and bad files -----------------------------------------------------------


def test_a_held_source_is_refused_with_409_and_recorded_as_denied(
    api: TestClient, store: LocalFolderStore
) -> None:
    response = _put(api, HELD, make_pdf(PAGES), projects=("PRJ-001",))
    assert response.status_code == 409
    assert "not eligible" in response.json()["detail"]
    assert _stored(store) == []
    (event,) = _events(api, "DOCUMENT_INGEST_REFUSED")
    assert event["outcome"] == "DENIED"
    assert event["resource_id"] == HELD and event["details"]["reason"] == "source_not_eligible"


@pytest.mark.parametrize(
    "source, body, expected, reason",
    [
        (ELIGIBLE, b"", 400, "empty_file"),
        (ELIGIBLE, b"<html>not a pdf</html>", 415, "not_a_pdf"),
        (ELIGIBLE, b"%PDF-1.4\nbroken\n%%EOF", 422, "unreadable_pdf"),
        ("NO-SUCH-SOURCE", make_pdf(PAGES), 404, "source_not_found"),
    ],
)
def test_bad_requests_are_refused_recorded_and_leave_nothing_behind(
    api: TestClient,
    store: LocalFolderStore,
    session: Session,
    source: str,
    body: bytes,
    expected: int,
    reason: str,
) -> None:
    response = _put(api, source, body)
    assert response.status_code == expected
    assert _stored(store) == []
    assert session.scalar(select(func.count()).select_from(Document)) == 0
    (event,) = _events(api, "DOCUMENT_INGEST_REFUSED")
    assert event["outcome"] == "FAILURE" and event["details"]["reason"] == reason


def test_an_unknown_project_is_refused(api: TestClient, store: LocalFolderStore) -> None:
    response = _put(api, ELIGIBLE, make_pdf(PAGES), projects=("PRJ-404",))
    assert response.status_code == 404 and "PRJ-404" in response.json()["detail"]
    assert _stored(store) == []


def test_an_oversized_file_is_refused_before_it_is_read(
    api: TestClient, store: LocalFolderStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    response = _put(api, ELIGIBLE, b"%PDF-1.4 " + b"x" * (1024 * 1024 + 10))
    assert response.status_code == 413
    assert _stored(store) == []


def test_an_oversized_upload_without_a_declared_length_is_cut_off_too(
    api: TestClient, store: LocalFolderStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    chunks = iter([b"%PDF-1.4 " + b"x" * 600_000, b"y" * 600_000])  # streamed, no Content-Length
    response = api.put(f"/api/v1/sources/{ELIGIBLE}/versions", content=chunks, headers=ADMIN)
    assert response.status_code == 413
    assert _stored(store) == []


# --- the audit trail --------------------------------------------------------------------------


def test_the_audit_event_names_the_actor_and_the_file_but_holds_no_document_text(
    api: TestClient,
) -> None:
    created = _put(api, ELIGIBLE, make_pdf(PAGES), projects=("PRJ-001",)).json()
    (event,) = _events(api, "DOCUMENT_INGESTED")
    assert event["actor_subject"] == "admin-doc-test" and event["outcome"] == "SUCCESS"
    assert event["resource_type"] == "document_version"
    assert event["resource_id"] == created["version_id"] and event["resource_version"] == "1"
    assert event["project_id"] == "PRJ-001"
    assert event["details"]["sha256"] == created["sha256"]
    assert event["details"]["source_id"] == ELIGIBLE
    assert SECRET_TEXT not in json.dumps(event)


def test_if_the_audit_trail_cannot_be_written_nothing_is_saved(
    api: TestClient, session: Session, store: LocalFolderStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenTrail:
        def record_event(self, **_kwargs):
            raise AuditUnavailableError("the trail is down")

    committed: list[bool] = []
    monkeypatch.setattr(session, "commit", lambda: committed.append(True))
    app.dependency_overrides[get_audit_service] = lambda: BrokenTrail()
    try:
        response = _put(api, ELIGIBLE, make_pdf(PAGES), projects=("PRJ-001",))
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert response.status_code == 503
    assert committed == []  # the ingestion was never committed


def test_listing_documents_is_recorded(api: TestClient) -> None:
    _put(api, ELIGIBLE, make_pdf(PAGES), projects=("PRJ-001",))
    api.get(
        "/api/v1/projects/PRJ-001/documents",
        headers=_headers(["scientist"], ["PRJ-001"], sub="reader"),
    )
    (event,) = _events(api, "DOCUMENTS_LISTED")
    assert event["actor_subject"] == "reader" and event["project_id"] == "PRJ-001"
    assert event["details"]["count"] == 1


_ = Callable  # (kept for type-checkers reading this module's imports)
