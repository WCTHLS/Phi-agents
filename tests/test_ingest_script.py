"""Tests for scripts/ingest_corpus.py (S2-03), against a real PostgreSQL + pgvector database.

The script commits, so these tests rebuild the (throwaway) database before and after running.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from pdf_factory import make_pdf
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.infrastructure.db.models import (
    AuditEvent,
    Document,
    DocumentVersion,
    ProjectDocument,
)
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ingest_corpus", REPO_ROOT / "scripts" / "ingest_corpus.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

PAGES = [
    "Permitted daily exposure for elemental impurities",
    "Class 1 elements and risk assessment",
]


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
    yield eng
    eng.dispose()
    command.downgrade(cfg, "base")  # leave the database as clean as the other test modules expect
    command.upgrade(cfg, "head")


@pytest.fixture
def originals(tmp_path: Path) -> Path:
    folder = tmp_path / "originals"
    folder.mkdir()
    (folder / "ICH-Q3DR2.pdf").write_bytes(make_pdf(PAGES))
    (folder / "FDA-PV-2011.pdf").write_bytes(make_pdf(["Process validation general principles"]))
    (folder / "ICH-Q8R2.pdf").write_bytes(make_pdf(PAGES))  # held: must never be ingested
    return folder


def _run(
    originals: Path, tmp_path: Path, *extra: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    code = script.main(
        [
            "--originals",
            str(originals),
            "--storage-root",
            str(tmp_path / "files"),
            "--database-url",
            TEST_DATABASE_URL,
            *extra,
        ]
    )
    return code, capsys.readouterr().out


def _count(engine: Engine, model: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_the_script_ingests_eligible_files_and_skips_held_and_missing_ones(
    engine: Engine, originals: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(originals, tmp_path, "--project", "PRJ-001", capsys=capsys)
    assert code == 0
    assert "ICH-Q3DR2" in out and "v1 added" in out
    assert any(line.startswith("ICH-Q8R2") and "held" in line for line in out.splitlines())
    assert any(line.startswith("ICH-Q9R1") and "missing" in line for line in out.splitlines())

    assert _count(engine, Document) == 2 and _count(engine, DocumentVersion) == 2
    assert _count(engine, ProjectDocument) == 2
    assert (tmp_path / "files" / "originals" / "ICH-Q3DR2").is_dir()
    assert not (
        tmp_path / "files" / "originals" / "ICH-Q8R2"
    ).exists()  # the held file was not copied

    with Session(engine) as session:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.actor_id == script.ACTOR))
        )
    assert len(events) == 2 and {e.action for e in events} == {"DOCUMENT_INGESTED"}
    assert all(e.project_id == "PRJ-001" and e.outcome == "SUCCESS" for e in events)


def test_running_it_again_changes_nothing_but_leaves_a_record(
    engine: Engine, originals: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(originals, tmp_path, "--project", "PRJ-001", capsys=capsys)
    code, out = _run(originals, tmp_path, "--project", "PRJ-001", capsys=capsys)
    assert code == 0 and out.count("already") == 2
    assert _count(engine, Document) == 2 and _count(engine, DocumentVersion) == 2
    with Session(engine) as session:
        actions = list(
            session.scalars(select(AuditEvent.action).where(AuditEvent.actor_id == script.ACTOR))
        )
    assert sorted(actions) == ["DOCUMENT_ALREADY_INGESTED"] * 2 + ["DOCUMENT_INGESTED"] * 2


def test_a_dry_run_changes_nothing(
    engine: Engine, originals: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(originals, tmp_path, "--dry-run", capsys=capsys)
    assert code == 0 and "would add" in out
    assert _count(engine, Document) == 0
    assert not (tmp_path / "files").exists()


def test_a_broken_file_is_reported_and_the_others_still_go_through(
    engine: Engine, originals: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (originals / "ICH-Q9R1.pdf").write_bytes(b"%PDF-1.4\nbroken\n%%EOF")
    code, out = _run(originals, tmp_path, capsys=capsys)
    assert code == 1
    assert any(line.startswith("ICH-Q9R1") and "REFUSED" in line for line in out.splitlines())
    assert _count(engine, Document) == 2  # the two good ones, not the broken one
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_id == script.ACTOR)
            )
            == 2
        )


def test_only_limits_which_documents_are_ingested(
    engine: Engine, originals: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(originals, tmp_path, "--only", "FDA-PV-2011", capsys=capsys)
    assert code == 0 and "ICH-Q3DR2" not in out
    assert _count(engine, Document) == 1
