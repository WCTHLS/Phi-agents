"""Database tests against a real PostgreSQL + pgvector instance.

They run only when TEST_DATABASE_URL is set, for example:
    docker run -d --name pharma-ai-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=pharma_test \
        -p 55432:5432 pgvector/pgvector:pg16
    set TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/pharma_test
    pytest tests/test_db_integration.py

Use an empty throwaway database: the tests apply the migration (and, in one test, remove it).
Each test runs inside a transaction that is rolled back, so tests do not affect each other.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.models import (
    EMBEDDING_DIM,
    EMPTY_INPUT_SHA256,
    AuditEvent,
    Base,
    Chunk,
    Document,
    DocumentVersion,
    Project,
    Source,
)
from pharma_ai.infrastructure.db.repositories import (
    ChunkInput,
    ChunkRepository,
    DocumentRepository,
    ProjectRepository,
    SourceRepository,
)
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOOD_SHA = "a" * 64
NOW = datetime(2026, 10, 29, tzinfo=UTC)

TABLES = {
    "roles",
    "users",
    "projects",
    "memberships",
    "sources",
    "documents",
    "document_versions",
    "project_documents",
    "chunks",
    "audit_events",
}


def _alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select table_name from information_schema.tables where table_schema = 'public'")
        )
        return {r[0] for r in rows} - {"alembic_version"}


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    assert TEST_DATABASE_URL
    url = to_sqlalchemy_url(TEST_DATABASE_URL)
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")  # start from a known empty state
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
    yield sess
    sess.close()
    outer.rollback()
    connection.close()


def _refused(session: Session, action: Callable[[], object]) -> str:
    """Run an action that the database must refuse; return the error text."""
    with pytest.raises(DBAPIError) as info, session.begin_nested():
        action()
        session.flush()
    return str(info.value)


def _unit_vector(position: int) -> list[float]:
    """A 384-number vector with a single 1.0, so different positions are far apart."""
    v = [0.0] * EMBEDDING_DIM
    v[position] = 1.0
    return v


def _seeded_source(session: Session, source_id: str = "ICH-Q3DR2") -> Source:
    seed_all(session)
    source = session.get(Source, source_id)
    assert source is not None
    return source


def _document_with_version(
    session: Session, source_id: str, sha: str = GOOD_SHA
) -> DocumentVersion:
    docs = DocumentRepository(session)
    doc = docs.register_document(source_id)
    return docs.add_version(
        document_id=doc.document_id,
        version_number=1,
        version_label="Revision 1",
        sha256=sha,
        object_key=f"originals/{source_id}.pdf",
        byte_size=1234,
        retrieved_at=NOW,
        page_count=10,
        text_layer="yes",
    )


# --- migration -------------------------------------------------------------------------------


def test_migration_creates_all_tables_and_the_vector_extension(engine: Engine) -> None:
    assert _tables(engine) == TABLES
    with engine.connect() as conn:
        assert conn.execute(text("select 1 from pg_extension where extname = 'vector'")).scalar()
        roles = {r[0] for r in conn.execute(text("select role_key from roles"))}
    assert roles == {"scientist", "reviewer", "qa", "admin"}


def test_chunks_have_a_cosine_hnsw_index(engine: Engine) -> None:
    with engine.connect() as conn:
        definition = conn.execute(
            text("select indexdef from pg_indexes where indexname = 'ix_chunks_embedding_hnsw'")
        ).scalar()
    assert definition is not None
    assert "hnsw" in definition
    assert "vector_cosine_ops" in definition


def test_models_match_the_migrated_database(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == []


def test_migration_can_be_reversed_and_applied_again(engine: Engine) -> None:
    cfg = _alembic_config(to_sqlalchemy_url(TEST_DATABASE_URL or ""))
    try:
        command.downgrade(cfg, "base")
        assert _tables(engine) == set()
        with engine.connect() as conn:
            leftover = conn.execute(
                text("select count(*) from pg_proc where proname like '%block%change%'")
            ).scalar()
            assert leftover == 0
    finally:
        command.upgrade(cfg, "head")  # always leave the database ready for the other tests
    assert _tables(engine) == TABLES


# --- seed data -------------------------------------------------------------------------------


def test_seed_loads_the_manifest_and_can_run_twice(session: Session) -> None:
    first = seed_all(session)
    second = seed_all(session)
    assert first == second == {"roles": 4, "projects": 2, "sources": 22}
    sources = SourceRepository(session)
    assert len(sources.list_eligible()) == 15
    q8 = sources.get("ICH-Q8R2")
    assert q8 is not None
    assert q8.eligible is False
    assert q8.tier == "hold"


# --- the licence rule (ADR-002) is enforced by the database ----------------------------------


def test_document_for_an_eligible_source_is_accepted(session: Session) -> None:
    source = _seeded_source(session)
    doc = DocumentRepository(session).register_document(source.source_id)
    assert doc.eligible is True


def test_document_for_a_held_source_is_refused(session: Session) -> None:
    _seeded_source(session)
    message = _refused(session, lambda: DocumentRepository(session).register_document("ICH-Q8R2"))
    assert "fk_documents_source_id_eligible_sources" in message


def test_source_cannot_become_ineligible_while_a_document_exists(session: Session) -> None:
    source = _seeded_source(session)
    DocumentRepository(session).register_document(source.source_id)

    def revoke() -> None:
        session.execute(
            text("update sources set eligible = false, tier = 'hold' where source_id = :s"),
            {"s": source.source_id},
        )

    assert "fk_documents_source_id_eligible_sources" in _refused(session, revoke)


def test_ineligible_source_must_be_on_hold(session: Session) -> None:
    _seeded_source(session)
    message = _refused(
        session,
        lambda: session.execute(
            text("update sources set tier = 'core' where source_id = 'ICH-Q8R2'")
        ),
    )
    assert "ck_sources_ineligible_must_be_hold" in message


def test_eligible_source_needs_attribution_text(session: Session) -> None:
    _seeded_source(session)
    message = _refused(
        session,
        lambda: session.execute(
            text("update sources set attribution = '' where source_id = 'ICH-Q3DR2'")
        ),
    )
    assert "ck_sources_eligible_needs_attribution" in message


# --- stored originals ------------------------------------------------------------------------


def test_the_hash_of_empty_input_is_refused(session: Session) -> None:
    source = _seeded_source(session)
    message = _refused(
        session, lambda: _document_with_version(session, source.source_id, EMPTY_INPUT_SHA256)
    )
    assert "ck_document_versions_sha256_not_empty_input" in message


@pytest.mark.parametrize("bad", ["abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_malformed_hashes_are_refused(session: Session, bad: str) -> None:
    source = _seeded_source(session)
    assert _refused(session, lambda: _document_with_version(session, source.source_id, bad))


@pytest.mark.parametrize(
    ("column", "new_value"),
    [("sha256", "b" * 64), ("object_key", "originals/other.pdf"), ("byte_size", 999)],
)
def test_a_stored_original_cannot_be_changed(
    session: Session, column: str, new_value: object
) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)

    def change() -> None:
        session.execute(
            text(f"update document_versions set {column} = :value where version_id = :v"),
            {"value": new_value, "v": version.version_id},
        )

    assert "stored original cannot be changed" in _refused(session, change)


def test_descriptive_fields_of_a_version_can_still_be_updated(session: Session) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)
    session.execute(
        text("update document_versions set page_count = 11 where version_id = :v"),
        {"v": version.version_id},
    )
    session.flush()
    session.refresh(version)
    assert version.page_count == 11


def test_the_same_file_cannot_be_stored_twice_for_one_document(session: Session) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)

    def duplicate() -> None:
        DocumentRepository(session).add_version(
            document_id=version.document_id,
            version_number=2,
            sha256=GOOD_SHA,
            object_key="originals/again.pdf",
            byte_size=10,
            retrieved_at=NOW,
        )

    assert "uq_document_versions_document_id_sha256" in _refused(session, duplicate)


# --- chunks ----------------------------------------------------------------------------------


def _chunk_kwargs(version: DocumentVersion, index: int = 0) -> dict[str, object]:
    return {
        "version_id": version.version_id,
        "chunk_index": index,
        "page_start": 1,
        "page_end": 1,
        "text": "example passage",
    }


def test_embedding_without_a_model_name_is_refused(session: Session) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)
    message = _refused(
        session, lambda: session.add(Chunk(**_chunk_kwargs(version), embedding=_unit_vector(0)))
    )
    assert "ck_chunks_embedding_with_model" in message


def test_model_name_without_an_embedding_is_refused(session: Session) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)
    message = _refused(
        session, lambda: session.add(Chunk(**_chunk_kwargs(version), embedding_model="m"))
    )
    assert "ck_chunks_embedding_with_model" in message


def test_chunk_page_range_must_be_valid(session: Session) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)
    kwargs = {**_chunk_kwargs(version), "page_start": 5, "page_end": 3}
    assert "ck_chunks_page_range" in _refused(session, lambda: session.add(Chunk(**kwargs)))


def test_repository_rejects_an_embedding_of_the_wrong_size(session: Session) -> None:
    version = _document_with_version(session, _seeded_source(session).source_id)
    bad = ChunkInput(
        chunk_index=0,
        page_start=1,
        page_end=1,
        text="x",
        embedding=[0.1] * 10,
        embedding_model="m",
    )
    with pytest.raises(ValueError, match="expected 384"):
        ChunkRepository(session).add_chunks(version.version_id, [bad])


# --- audit trail -----------------------------------------------------------------------------


def _audit_row(session: Session) -> AuditEvent:
    event = AuditEvent(
        actor_id="a@example.org",
        actor_role="scientist",
        project_id="PRJ-001",
        action="RESEARCH_QUERY_EXECUTED",
        resource_type="document",
        details={"q": "x"},
    )
    session.add(event)
    session.flush()
    session.refresh(event)
    return event


def test_audit_events_can_be_added_with_defaults(session: Session) -> None:
    event = _audit_row(session)
    assert event.outcome == "SUCCESS"
    assert event.created_at is not None


def test_audit_events_cannot_be_updated(session: Session) -> None:
    event = _audit_row(session)
    message = _refused(
        session,
        lambda: session.execute(
            text("update audit_events set outcome = 'FAILURE' where event_id = :e"),
            {"e": event.event_id},
        ),
    )
    assert "append-only" in message


def test_audit_events_cannot_be_deleted(session: Session) -> None:
    event = _audit_row(session)
    message = _refused(
        session,
        lambda: session.execute(
            text("delete from audit_events where event_id = :e"), {"e": event.event_id}
        ),
    )
    assert "append-only" in message


def test_audit_events_cannot_be_truncated(session: Session) -> None:
    _audit_row(session)
    assert "append-only" in _refused(
        session, lambda: session.execute(text("truncate audit_events"))
    )


# --- project-scoped repositories -------------------------------------------------------------


def _two_projects_with_one_document_each(session: Session) -> dict[str, Document]:
    """PRJ-001 can use ICH-Q3DR2 (chunk 0); PRJ-002 can use ICH-Q3CR9 (chunk 1)."""
    seed_all(session)
    docs, chunks = DocumentRepository(session), ChunkRepository(session)
    out: dict[str, Document] = {}
    for project, source_id, position in (("PRJ-001", "ICH-Q3DR2", 0), ("PRJ-002", "ICH-Q3CR9", 1)):
        document = docs.register_document(source_id)
        version = docs.add_version(
            document_id=document.document_id,
            version_number=1,
            version_label="Revision 1",
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            object_key=f"originals/{source_id}.pdf",
            byte_size=100,
            retrieved_at=NOW,
        )
        docs.grant_project_access(document.document_id, project)
        chunks.add_chunks(
            version.version_id,
            [
                ChunkInput(
                    chunk_index=0,
                    page_start=3,
                    page_end=4,
                    section="Table 1",
                    text=f"passage from {source_id}",
                    embedding=_unit_vector(position),
                    embedding_model="test-model",
                )
            ],
        )
        out[project] = document
    return out


def test_search_returns_only_chunks_from_the_callers_projects(session: Session) -> None:
    _two_projects_with_one_document_each(session)
    chunks = ChunkRepository(session)
    # Query vector is identical to the PRJ-002 chunk, but the caller only has PRJ-001.
    hits = chunks.search_nearest(_unit_vector(1), ["PRJ-001"], limit=5)
    assert [h.source_id for h in hits] == ["ICH-Q3DR2"]


def test_search_with_both_projects_returns_nearest_first(session: Session) -> None:
    _two_projects_with_one_document_each(session)
    hits = ChunkRepository(session).search_nearest(_unit_vector(1), ["PRJ-001", "PRJ-002"])
    assert [h.source_id for h in hits] == ["ICH-Q3CR9", "ICH-Q3DR2"]
    assert hits[0].distance == pytest.approx(0.0, abs=1e-6)
    assert hits[1].distance == pytest.approx(1.0, abs=1e-6)  # unrelated direction


def test_search_with_no_allowed_projects_returns_nothing(session: Session) -> None:
    _two_projects_with_one_document_each(session)
    chunks = ChunkRepository(session)
    assert chunks.search_nearest(_unit_vector(0), []) == []
    assert chunks.search_nearest(_unit_vector(0), ["PRJ-999"]) == []


def test_search_hits_carry_what_a_citation_needs(session: Session) -> None:
    _two_projects_with_one_document_each(session)
    hit = ChunkRepository(session).search_nearest(_unit_vector(0), ["PRJ-001"])[0]
    assert hit.source_id == "ICH-Q3DR2"
    assert hit.version_label == "Revision 1"
    assert (hit.page_start, hit.page_end, hit.section) == (3, 4, "Table 1")
    assert (
        hit.attribution and "ICH" in hit.attribution
    )  # ADR-002: attribution travels with the passage
    assert len(hit.sha256) == 64


def test_search_also_checks_eligibility_if_the_foreign_key_were_bypassed(session: Session) -> None:
    """Defence in depth: the foreign key normally makes this situation impossible.

    Inside this test's rolled-back transaction the foreign-key checks are switched off (this needs
    a superuser, as in the throwaway test database), a source is made ineligible, and the search
    must still leave its chunks out.
    """
    _two_projects_with_one_document_each(session)
    # The foreign-key check on an update of "sources" is a trigger on the referenced table.
    session.execute(text("alter table sources disable trigger all"))
    session.execute(
        text("update sources set eligible = false, tier = 'hold' where source_id = 'ICH-Q3DR2'")
    )
    hits = ChunkRepository(session).search_nearest(_unit_vector(0), ["PRJ-001", "PRJ-002"])
    assert [h.source_id for h in hits] == ["ICH-Q3CR9"]


def test_search_ignores_chunks_that_have_no_embedding_yet(session: Session) -> None:
    docs = _two_projects_with_one_document_each(session)
    version = (
        session.query(DocumentVersion).filter_by(document_id=docs["PRJ-001"].document_id).one()
    )
    ChunkRepository(session).add_chunks(
        version.version_id,
        [ChunkInput(chunk_index=1, page_start=1, page_end=1, text="not embedded yet")],
    )
    hits = ChunkRepository(session).search_nearest(_unit_vector(0), ["PRJ-001"], limit=10)
    assert len(hits) == 1


def test_search_rejects_a_query_of_the_wrong_size(session: Session) -> None:
    with pytest.raises(ValueError, match="expected 384"):
        ChunkRepository(session).search_nearest([0.1] * 5, ["PRJ-001"])


def test_project_repository_hides_projects_the_caller_cannot_access(session: Session) -> None:
    seed_all(session)
    projects = ProjectRepository(session)
    assert [p.project_id for p in projects.list_accessible(["PRJ-001"])] == ["PRJ-001"]
    assert projects.list_accessible([]) == []
    assert projects.get("PRJ-002", ["PRJ-001"]) is None
    found = projects.get("PRJ-001", ["PRJ-001"])
    assert isinstance(found, Project)
    assert found.name.startswith("Metformin")


def test_document_list_is_empty_for_a_project_the_caller_cannot_access(session: Session) -> None:
    _two_projects_with_one_document_each(session)
    docs = DocumentRepository(session)
    assert [d.source_id for d in docs.list_for_project("PRJ-001", ["PRJ-001"])] == ["ICH-Q3DR2"]
    assert docs.list_for_project("PRJ-002", ["PRJ-001"]) == []


def test_integrity_errors_are_database_errors(session: Session) -> None:
    # Sanity check for the helper above: constraint violations surface as DBAPIError subclasses.
    assert issubclass(IntegrityError, DBAPIError)
