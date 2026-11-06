"""Tests for scripts/assign_documents.py, against a real PostgreSQL + pgvector database.

The script commits, so these tests rebuild the (throwaway) database before and after running.
Three documents are stored, each linked to both projects, the way ingestion used to leave them.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.infrastructure.db.models import AuditEvent, Document, ProjectDocument
from pharma_ai.infrastructure.db.repositories import DocumentRepository
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "assign_documents", REPO_ROOT / "scripts" / "assign_documents.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

NITRO, M9, Q9 = "FDA-NITRO-2024", "ICH-M9", "ICH-Q9R1"


@pytest.fixture
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
    with Session(eng) as session:
        seed_all(session)
        docs = DocumentRepository(session)
        for source_id in (NITRO, M9):  # linked to both projects
            document = docs.register_document(source_id)
            for project in ("PRJ-001", "PRJ-002"):
                docs.grant_project_access(document.document_id, project)
        docs.register_document(Q9)  # linked to no project
        session.commit()
    yield eng
    eng.dispose()
    command.downgrade(cfg, "base")  # leave the database as clean as the other test modules expect
    command.upgrade(cfg, "head")


def _run(*extra: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = script.main(["--database-url", TEST_DATABASE_URL, *extra])
    return code, capsys.readouterr().out


def _links(engine: Engine) -> set[tuple[str, str]]:
    with Session(engine) as session:
        rows = session.execute(
            select(ProjectDocument.project_id, Document.source_id).join(
                Document, Document.document_id == ProjectDocument.document_id
            )
        )
        return {(project, source) for project, source in rows}


def _events(engine: Engine) -> list[AuditEvent]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("DOCUMENT_PROJECT_ACCESS_%"))
                .order_by(AuditEvent.action, AuditEvent.project_id)
            )
        )


BEFORE = {("PRJ-001", NITRO), ("PRJ-002", NITRO), ("PRJ-001", M9), ("PRJ-002", M9)}


def test_a_dry_run_lists_the_changes_and_changes_nothing(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run("--dry-run", capsys=capsys)
    assert code == 0
    assert "3 link(s) would change. Nothing was changed." in out
    assert _links(engine) == BEFORE and _events(engine) == []


def test_a_real_run_divides_the_documents_and_records_every_change(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys=capsys)
    assert code == 0 and "3 link(s) changed." in out
    # NITRO and Q9 belong to PRJ-001; M9 belongs to PRJ-002.
    assert _links(engine) == {("PRJ-001", NITRO), ("PRJ-001", Q9), ("PRJ-002", M9)}
    events = _events(engine)
    assert [(e.action, e.project_id, e.details["source_id"]) for e in events] == [
        ("DOCUMENT_PROJECT_ACCESS_GRANTED", "PRJ-001", Q9),
        ("DOCUMENT_PROJECT_ACCESS_REVOKED", "PRJ-001", M9),
        ("DOCUMENT_PROJECT_ACCESS_REVOKED", "PRJ-002", NITRO),
    ]
    assert {e.actor_id for e in events} == {"system:assign-script"}
    assert all(e.outcome == "SUCCESS" and e.resource_type == "document" for e in events)
    # The documents not yet stored are named, not invented.
    assert "Not ingested yet, skipped:" in out and "ICH-Q3DR2" in out


def test_running_it_twice_changes_and_records_nothing_the_second_time(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(capsys=capsys)
    links, events = _links(engine), len(_events(engine))
    code, out = _run(capsys=capsys)
    assert code == 0 and "Nothing to do" in out
    assert _links(engine) == links and len(_events(engine)) == events


def test_a_change_is_not_kept_if_its_audit_event_cannot_be_written(
    engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(change: object) -> AuditEvent:
        raise RuntimeError("the audit trail went away")

    monkeypatch.setattr(script, "audit_row", broken)
    with pytest.raises(RuntimeError):
        _run(capsys=capsys)
    assert _links(engine) == BEFORE and _events(engine) == []
