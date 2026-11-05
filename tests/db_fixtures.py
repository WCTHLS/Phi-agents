"""Shared fixtures and helpers for API tests that need a real database and a stand-in model.

Use it from a test module with ``pytest_plugins = ("db_fixtures",)`` (fixtures: ``engine``,
``session``, ``embedder``, ``model_loads``, ``api``) and ``from db_fixtures import ...`` for the
helpers. Needs TEST_DATABASE_URL (an empty throwaway database; see test_db_integration.py).
"""

from __future__ import annotations

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

from pharma_ai.api.routes.search import embedder_provider
from pharma_ai.infrastructure.db.repositories import (
    ChunkInput,
    ChunkRepository,
    DocumentRepository,
)
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import get_db_session, to_sqlalchemy_url
from pharma_ai.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

NOW = datetime(2026, 11, 4, tzinfo=UTC)
Q3D, PV, NITRO = "ICH-Q3DR2", "FDA-PV-2011", "FDA-NITRO-2024"


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
def model_loads() -> list[int]:
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


def headers(roles: list[str], projects: list[str], sub: str = "user-api-test") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub, roles=roles, projects=projects)}"}


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


def events(client: TestClient, **params) -> list[dict]:
    """Audit events matching the filters, read back through the audit endpoint as an admin."""
    page = client.get(
        "/api/v1/audit/events",
        params={"page_size": 100, **params},
        headers=headers(["admin"], [], sub="auditor-api-test"),
    )
    assert page.status_code == 200
    return page.json()["items"]
