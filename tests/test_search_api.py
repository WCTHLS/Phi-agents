"""Tests for the passage search endpoint (S2-07, ADR-009), against a real PostgreSQL + pgvector database.

The questions here are about who may see what: a member finds passages, a non-member is refused
before anything is searched, and no request can reach another project's passages. A stand-in
embedding model is used, so these tests say nothing about search quality (see evals/).

Run with TEST_DATABASE_URL set to an empty throwaway database (see test_db_integration.py).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from conftest import mint_token
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.api.routes import search as search_routes
from pharma_ai.api.routes.search import embedder_provider
from pharma_ai.infrastructure.audit.service import AuditUnavailableError, get_audit_service
from pharma_ai.infrastructure.db.repositories import ChunkInput, ChunkRepository, DocumentRepository
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import get_db_session, to_sqlalchemy_url
from pharma_ai.infrastructure.embeddings import EmbedderUnavailable
from pharma_ai.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

NOW = datetime(2026, 11, 4, tzinfo=UTC)
Q3D, PV = "ICH-Q3DR2", "FDA-PV-2011"
CADMIUM = "Cadmium oral permitted daily exposure is five micrograms per day."
VALIDATION = "Process validation requires three successful consecutive batches."
QUESTION = "cadmium oral permitted daily exposure"


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
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def model_loads(embedder: FakeEmbedder) -> list[int]:
    """One entry each time the route asks for the model."""
    return []


@pytest.fixture
def api(session: Session, embedder: FakeEmbedder, model_loads: list[int]) -> Iterator[TestClient]:
    def provider() -> Callable[[], FakeEmbedder]:
        def get() -> FakeEmbedder:
            model_loads.append(1)
            return embedder

        return get

    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[embedder_provider] = provider
    yield TestClient(app)
    app.dependency_overrides.pop(get_db_session, None)
    app.dependency_overrides.pop(embedder_provider, None)


def headers(roles: list[str], projects: list[str], sub: str = "scientist-search-test") -> dict:
    return {"Authorization": f"Bearer {mint_token(sub=sub, roles=roles, projects=projects)}"}


MEMBER_1 = headers(["scientist"], ["PRJ-001"])
MEMBER_BOTH = headers(["scientist"], ["PRJ-001", "PRJ-002"], sub="both-search-test")
MEMBER_2 = headers(["scientist"], ["PRJ-002"], sub="other-search-test")


def add_document(
    session: Session,
    embedder: FakeEmbedder,
    source_id: str,
    project: str,
    passages: list[str],
    *,
    version_number: int = 1,
    document=None,
):
    """A document in one project, each passage embedded by the stand-in model."""
    docs = DocumentRepository(session)
    if document is None:
        document = docs.register_document(source_id)
        docs.grant_project_access(document.document_id, project)
    version = docs.add_version(
        document_id=document.document_id,
        version_number=version_number,
        version_label=f"Revision {version_number}",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        object_key=f"originals/{source_id}-{version_number}.pdf",
        byte_size=100,
        retrieved_at=NOW,
    )
    vectors = embedder.embed(passages)
    ChunkRepository(session).add_chunks(
        version.version_id,
        [
            ChunkInput(
                chunk_index=i,
                page_start=i + 1,
                page_end=i + 1,
                text=text,
                section=f"Section {i + 1}",
                embedding=vector,
                embedding_model=embedder.model_name,
            )
            for i, (text, vector) in enumerate(zip(passages, vectors, strict=True))
        ],
    )
    return document


def search(api: TestClient, project: str, auth: dict, question: str = QUESTION, **extra):
    return api.post(
        f"/api/v1/projects/{project}/search", json={"question": question, **extra}, headers=auth
    )


def events(api: TestClient, action: str) -> list[dict]:
    admin = headers(["admin"], [], sub="auditor-search-test")
    page = api.get(
        "/api/v1/audit/events", params={"action": action, "page_size": 100}, headers=admin
    )
    assert page.status_code == 200
    return page.json()["items"]


# --- what a member gets -------------------------------------------------------------------------


def test_a_member_gets_ranked_passages_with_everything_a_citation_needs(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [VALIDATION, CADMIUM])
    response = search(api, "PRJ-001", MEMBER_1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == "PRJ-001" and body["embedding_model"] == embedder.model_name
    first = body["results"][0]
    assert first["rank"] == 1 and first["text"] == CADMIUM
    assert first["source_id"] == Q3D and first["title"]
    assert first["attribution"]  # ADR-002: shown next to any citation
    assert first["version_number"] == 1 and first["version_label"] == "Revision 1"
    assert len(first["sha256"]) == 64
    assert (first["page_start"], first["page_end"], first["section"]) == (2, 2, "Section 2")
    assert [r["rank"] for r in body["results"]] == list(range(1, len(body["results"]) + 1))


def test_the_limit_is_respected(api: TestClient, session: Session, embedder: FakeEmbedder) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [f"cadmium passage {i}" for i in range(6)])
    assert len(search(api, "PRJ-001", MEMBER_1, limit=2).json()["results"]) == 2


def test_only_the_newest_version_of_a_document_is_returned(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    old = add_document(session, embedder, Q3D, "PRJ-001", ["Cadmium limit of old revision."])
    add_document(
        session,
        embedder,
        Q3D,
        "PRJ-001",
        ["Cadmium limit of new revision."],
        version_number=2,
        document=old,
    )
    results = search(api, "PRJ-001", MEMBER_1).json()["results"]
    assert [r["text"] for r in results] == ["Cadmium limit of new revision."]
    assert results[0]["version_number"] == 2


def test_passages_embedded_by_another_model_are_not_returned(
    api: TestClient, session: Session
) -> None:
    add_document(session, FakeEmbedder("some-other-model"), Q3D, "PRJ-001", [CADMIUM])
    assert search(api, "PRJ-001", MEMBER_1).json()["results"] == []


# --- who may not (the leakage cases) ------------------------------------------------------------


def test_a_non_member_is_refused_and_nothing_is_embedded_or_searched(
    api: TestClient,
    session: Session,
    embedder: FakeEmbedder,
    model_loads: list[int],
) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [CADMIUM])
    embedder.calls.clear()  # setting up the passages used the model; only the request counts
    response = search(api, "PRJ-001", MEMBER_2)  # a member of PRJ-002 only
    assert response.status_code == 403
    assert CADMIUM not in response.text
    assert model_loads == [] and embedder.calls == []  # refused before any work was done
    assert events(api, "PROJECT_SEARCHED") == []
    (denied,) = events(api, "ACCESS_DENIED")
    assert denied["outcome"] == "DENIED" and denied["project_id"] == "PRJ-001"


def test_a_project_that_does_not_exist_is_refused_exactly_like_one_you_may_not_see(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [CADMIUM])
    unknown = search(api, "PRJ-404", MEMBER_1)
    forbidden = search(api, "PRJ-002", MEMBER_1)  # exists, but the caller is not in it
    assert unknown.status_code == forbidden.status_code == 403
    assert unknown.json() == forbidden.json()


def test_a_search_in_one_project_never_returns_another_projects_passages_even_for_a_member_of_both(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [VALIDATION])
    add_document(session, embedder, PV, "PRJ-002", [CADMIUM])
    in_first = search(api, "PRJ-001", MEMBER_BOTH)
    assert in_first.status_code == 200
    assert CADMIUM not in in_first.text
    assert {r["source_id"] for r in in_first.json()["results"]} == {Q3D}
    in_second = search(api, "PRJ-002", MEMBER_BOTH).json()["results"]
    assert {r["source_id"] for r in in_second} == {PV}


def test_the_request_cannot_name_other_projects(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, PV, "PRJ-002", [CADMIUM])
    smuggled = {
        "project_ids": ["PRJ-002"],
        "project_id": "PRJ-002",
        "projects": ["PRJ-002"],
        "allowed_project_ids": ["PRJ-002"],
    }
    response = search(api, "PRJ-001", MEMBER_1, **smuggled)
    assert response.status_code == 200
    assert response.json()["results"] == [] and CADMIUM not in response.text


def test_a_project_claim_in_the_token_is_not_widened_by_a_header_or_query(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, PV, "PRJ-002", [CADMIUM])
    response = api.post(
        "/api/v1/projects/PRJ-001/search?project_id=PRJ-002",
        json={"question": QUESTION},
        headers={**MEMBER_1, "X-Project-Id": "PRJ-002"},
    )
    assert response.status_code == 200 and response.json()["results"] == []


def test_a_search_needs_a_login(api: TestClient) -> None:
    assert (
        api.post("/api/v1/projects/PRJ-001/search", json={"question": QUESTION}).status_code == 401
    )


def test_a_token_for_no_projects_is_refused(api: TestClient) -> None:
    assert search(api, "PRJ-001", headers(["scientist"], [])).status_code == 403


# --- the audit trail ----------------------------------------------------------------------------


def test_a_search_is_audited_without_the_question_but_with_what_was_returned(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [VALIDATION, CADMIUM])
    returned = search(api, "PRJ-001", MEMBER_1, limit=2).json()["results"]
    (event,) = events(api, "PROJECT_SEARCHED")
    assert event["outcome"] == "SUCCESS" and event["project_id"] == "PRJ-001"
    assert event["resource_type"] == "project" and event["resource_id"] == "PRJ-001"
    details = event["details"]
    assert (
        details["question"]["length"] == len(QUESTION) and len(details["question"]["sha256"]) == 64
    )
    assert details["result_count"] == len(returned) == 2
    assert [r["chunk_id"] for r in details["results"]] == [r["chunk_id"] for r in returned]
    assert details["embedding_model"] == embedder.model_name
    dumped = json.dumps(event)
    assert QUESTION not in dumped and CADMIUM not in dumped  # neither the question nor the text


def test_a_search_with_no_results_is_still_audited(api: TestClient) -> None:
    assert search(api, "PRJ-001", MEMBER_1).json()["results"] == []
    (event,) = events(api, "PROJECT_SEARCHED")
    assert event["details"]["result_count"] == 0 and event["details"]["results"] == []


def test_if_the_audit_trail_cannot_be_written_no_passages_are_returned(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, Q3D, "PRJ-001", [CADMIUM])

    class BrokenTrail:
        def record_event(self, **_kwargs):
            raise AuditUnavailableError("the trail is down")

    app.dependency_overrides[get_audit_service] = lambda: BrokenTrail()
    try:
        response = search(api, "PRJ-001", MEMBER_1)
    finally:
        app.dependency_overrides.pop(get_audit_service, None)
    assert response.status_code == 503
    assert CADMIUM not in response.text


def test_a_search_that_fails_is_audited_as_a_failure_and_the_error_is_not_swallowed(
    api: TestClient, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_texts):
        raise RuntimeError("the model fell over")

    monkeypatch.setattr(embedder, "embed", broken)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/projects/PRJ-001/search", json={"question": QUESTION}, headers=MEMBER_1
    )
    assert response.status_code == 500
    (event,) = events(api, "PROJECT_SEARCHED")
    assert event["outcome"] == "FAILURE" and event["details"]["error_type"] == "RuntimeError"
    assert "fell over" not in json.dumps(event)


# --- bad requests -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "x" * (2000 + 1)},
        {"question": QUESTION, "limit": 0},
        {"question": QUESTION, "limit": 20 + 1},
        {"question": QUESTION, "limit": "many"},
        {"question": ["a", "b"]},
    ],
)
def test_malformed_requests_are_refused_with_422(api: TestClient, payload: dict) -> None:
    response = api.post("/api/v1/projects/PRJ-001/search", json=payload, headers=MEMBER_1)
    assert response.status_code == 422
    assert events(api, "PROJECT_SEARCHED") == []


def test_the_longest_allowed_question_and_the_largest_limit_are_accepted(api: TestClient) -> None:
    response = search(api, "PRJ-001", MEMBER_1, question="a " * (2000 // 2), limit=20)
    assert response.status_code == 200


# --- the embedding model ------------------------------------------------------------------------


def test_if_the_model_cannot_be_loaded_the_answer_is_503_without_the_reason(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cannot_load(*_args, **_kwargs):
        raise EmbedderUnavailable("could not read C:\\secret\\place\\model.bin")

    monkeypatch.setattr(search_routes, "_embedder", None)
    monkeypatch.setattr(search_routes, "SentenceTransformerEmbedder", cannot_load)
    app.dependency_overrides[get_db_session] = lambda: session
    try:
        response = TestClient(app).post(
            "/api/v1/projects/PRJ-001/search", json={"question": QUESTION}, headers=MEMBER_1
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 503
    assert "secret" not in response.text and "model.bin" not in response.text


def test_the_model_is_loaded_once_and_only_for_a_member(
    session: Session, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads: list[str] = []

    def load(name: str, **_kwargs) -> FakeEmbedder:
        loads.append(name)
        return embedder

    monkeypatch.setattr(search_routes, "_embedder", None)
    monkeypatch.setattr(search_routes, "SentenceTransformerEmbedder", load)
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)
    try:
        assert search(client, "PRJ-001", MEMBER_2).status_code == 403
        assert loads == []  # a refused request does not load the model
        assert search(client, "PRJ-001", MEMBER_1).status_code == 200
        assert search(client, "PRJ-001", MEMBER_1).status_code == 200
    finally:
        app.dependency_overrides.pop(get_db_session, None)
    assert len(loads) == 1
