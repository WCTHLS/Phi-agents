"""Tests for scripts/reextract_text.py, against a real PostgreSQL + pgvector database.

The script commits, so these tests rebuild the (throwaway) database before and after running.
The documents are ingested the way text was read before superscripts were kept.
"""

from __future__ import annotations

import importlib.util
import io
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from pdf_factory import make_pdf, make_pdf_from_streams
from pypdf import PdfReader
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.features.documents.extraction import (
    ExtractionResult,
    PageText,
    classify,
    clean_text,
)
from pharma_ai.features.documents.ingestion import DocumentIngestionService
from pharma_ai.infrastructure.db.models import AuditEvent, DocumentVersion
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url
from pharma_ai.infrastructure.storage import LocalFolderStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "reextract_text", REPO_ROOT / "scripts" / "reextract_text.py"
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

EXPONENT_PAGE = (
    b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (a theoretical 10) Tj /F1 8 Tf 1 0 0 1 154.1 704.6 Tm "
    b"(-) Tj 1 0 0 1 156.8 704.6 Tm (5) Tj /F1 12 Tf 1 0 0 1 161.3 700 Tm "
    b"( excess lifetime risk of cancer) Tj ET"
)


def _old_extractor(data: bytes) -> ExtractionResult:
    reader = PdfReader(io.BytesIO(data))
    pages = [PageText(n, clean_text(p.extract_text())) for n, p in enumerate(reader.pages, 1)]
    return classify("pypdf", pages, [])


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
        service = DocumentIngestionService(
            session, store, max_bytes=5_000_000, extractor=_old_extractor
        )
        service.ingest(source_id="ICH-M7R2", data=make_pdf_from_streams([EXPONENT_PAGE]))
        service.ingest(source_id="FDA-PV-2011", data=make_pdf(["Process validation stages."]))
        session.commit()
    yield eng
    eng.dispose()
    command.downgrade(cfg, "base")  # leave the database as clean as the other test modules expect
    command.upgrade(cfg, "head")


def _run(tmp_path: Path, *extra: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = script.main(
        ["--storage-root", str(tmp_path / "files"), "--database-url", TEST_DATABASE_URL, *extra]
    )
    return code, capsys.readouterr().out


def _audit(engine: Engine) -> list[AuditEvent]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.action == "DOCUMENT_TEXT_REEXTRACTED")
            )
        )


def _keys(engine: Engine) -> list[str | None]:
    with Session(engine) as session:
        return list(
            session.scalars(select(DocumentVersion.extracted_key).order_by(DocumentVersion.sha256))
        )


def test_a_dry_run_lists_the_change_and_changes_nothing(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keys = _keys(engine)
    code, out = _run(tmp_path, "--dry-run", capsys=capsys)
    assert code == 0
    assert "ICH-M7R2" in out and "would change" in out
    assert "FDA-PV-2011" in out and "unchanged" in out
    assert "nothing was stored or changed" in out
    assert _keys(engine) == keys and _audit(engine) == []


def test_a_real_run_points_changed_versions_at_the_new_text_with_an_audit_event(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keys = _keys(engine)
    code, out = _run(tmp_path, capsys=capsys)
    assert code == 0
    assert "python scripts/index_chunks.py --force --only ICH-M7R2" in out
    events = _audit(engine)
    assert len(events) == 1  # only the version whose text changed
    event = events[0]
    assert event.actor_id == "system:reextract-script" and event.outcome == "SUCCESS"
    assert event.details["source_id"] == "ICH-M7R2"
    assert event.details["changed_pages"] == [1]
    assert event.details["previous_extracted_key"] in keys
    assert event.details["extracted_key"] in _keys(engine)
    assert sorted(k for k in _keys(engine) if k not in keys) == [event.details["extracted_key"]]

    # A second run finds nothing new and records nothing more.
    code, out = _run(tmp_path, capsys=capsys)
    assert code == 0 and "index_chunks" not in out
    assert len(_audit(engine)) == 1
