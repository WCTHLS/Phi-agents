"""Tests for scripts/index_chunks.py (S2-06), against a real PostgreSQL + pgvector database.

The script commits, so these tests rebuild the (throwaway) database before and after running. A
stand-in embedding model is passed in, so no model is downloaded.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fake_embedder import FakeEmbedder
from pdf_factory import make_pdf
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.features.documents.ingestion import DocumentIngestionService
from pharma_ai.infrastructure.db.models import AuditEvent, Chunk
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url
from pharma_ai.infrastructure.embeddings import EmbedderUnavailable
from pharma_ai.infrastructure.storage import LocalFolderStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "index_chunks", REPO_ROOT / "scripts" / "index_chunks.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
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
        store = LocalFolderStore(tmp_path / "files")
        service = DocumentIngestionService(session, store, max_bytes=5_000_000)
        for source_id, pages in (
            (
                "ICH-Q3DR2",
                ["1. SCOPE\n\nElemental impurities are limited.", "2. LIMITS\n\nSee table."],
            ),
            ("FDA-PV-2011", ["I. INTRODUCTION\n\nProcess validation has three stages."]),
            ("ICH-Q9R1", [None, None]),  # a scan: no text layer
        ):
            service.ingest(source_id=source_id, data=make_pdf(pages), project_ids=["PRJ-001"])
        session.commit()
    yield eng
    eng.dispose()
    command.downgrade(cfg, "base")  # leave the database as clean as the other test modules expect
    command.upgrade(cfg, "head")


def _run(
    tmp_path: Path, *extra: str, capsys: pytest.CaptureFixture[str], factory: Any = FakeEmbedder
) -> tuple[int, str]:
    code = script.main(
        ["--storage-root", str(tmp_path / "files"), "--database-url", TEST_DATABASE_URL, *extra],
        embedder_factory=lambda name, **kw: factory(name),
    )
    return code, capsys.readouterr().out


def _chunk_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Chunk)) or 0


def _events(engine: Engine) -> list[AuditEvent]:
    with Session(engine) as session:
        return list(session.scalars(select(AuditEvent).where(AuditEvent.actor_id == script.ACTOR)))


def test_the_script_indexes_every_version_and_leaves_a_record(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, capsys=capsys)
    lines = out.splitlines()

    assert code == 0
    assert any(line.startswith("ICH-Q3DR2") and "indexed" in line for line in lines)
    assert any(line.startswith("FDA-PV-2011") and "indexed" in line for line in lines)
    # The scan is reported, not hidden, and does not make the run fail.
    assert any(
        line.startswith("ICH-Q9R1") and "skipped" in line and "OCR" in line for line in lines
    )
    assert _chunk_count(engine) == 3  # two sections in the first document, one in the second

    events = _events(engine)
    assert len(events) == 2 and {e.action for e in events} == {
        "DOCUMENT_INDEXED"
    }  # none for the scan
    assert all(e.outcome == "SUCCESS" and e.resource_type == "document_version" for e in events)
    details = {e.details["source_id"]: e.details for e in events}
    assert details["ICH-Q3DR2"]["chunk_count"] == 2
    assert details["ICH-Q3DR2"]["embedding_model"] == script.settings.embedding_model
    assert (details["ICH-Q3DR2"]["max_tokens"], details["ICH-Q3DR2"]["overlap_tokens"]) == (
        500,
        100,
    )


def test_running_it_again_changes_nothing_and_adds_no_audit_events(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, capsys=capsys)
    code, out = _run(tmp_path, capsys=capsys)
    assert code == 0 and out.count("already indexed") == 2
    assert _chunk_count(engine) == 3 and len(_events(engine)) == 2


def test_force_cuts_and_embeds_again_and_records_it(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, capsys=capsys)
    code, out = _run(tmp_path, "--force", capsys=capsys)
    assert code == 0 and out.count("reindexed") == 2
    assert _chunk_count(engine) == 3 and len(_events(engine)) == 4


def test_only_limits_which_documents_are_indexed(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, "--only", "FDA-PV-2011", capsys=capsys)
    assert code == 0 and "ICH-Q3DR2" not in out
    assert _chunk_count(engine) == 1


def test_a_dry_run_changes_nothing_and_never_loads_the_model(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def must_not_be_called(name: str) -> None:
        raise AssertionError("the model was loaded during a dry run")

    code, out = _run(tmp_path, "--dry-run", capsys=capsys, factory=must_not_be_called)
    assert code == 0 and "Nothing was changed" in out and "ICH-Q3DR2" in out
    assert _chunk_count(engine) == 0 and _events(engine) == []


def test_a_model_that_cannot_be_loaded_is_reported_clearly(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(name: str) -> None:
        raise EmbedderUnavailable("sentence-transformers is not installed")

    code, out = _run(tmp_path, capsys=capsys, factory=broken)
    assert code == 2 and "Cannot load the embedding model" in out
    assert _chunk_count(engine) == 0


def test_nothing_ingested_is_reported(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, "--only", "ICH-M9", capsys=capsys)
    assert code == 1 and "ingest" in out.lower()
