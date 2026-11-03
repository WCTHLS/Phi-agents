"""Tests for document ingestion (S2-03, ADR-007) against a real PostgreSQL + pgvector database.

Run with TEST_DATABASE_URL set to an empty throwaway database (see test_db_integration.py).
Each test runs inside a transaction that is rolled back; stored files go to a temporary folder.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from pdf_factory import make_pdf
from pypdf import PdfReader, PdfWriter
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.features.documents.ingestion import (
    DocumentIngestionService,
    EmptyFile,
    FileTooLarge,
    NotAPdf,
    ProjectNotFound,
    SourceNotEligible,
    SourceNotFound,
    UnreadablePdf,
)
from pharma_ai.infrastructure.db.models import (
    Document,
    DocumentVersion,
    ProjectDocument,
)
from pharma_ai.infrastructure.db.repositories import DocumentRepository
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url
from pharma_ai.infrastructure.storage import LocalFolderStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

ELIGIBLE = "ICH-Q3DR2"  # a core document in the source manifest
HELD = "ICH-Q8R2"  # held: the PDF has no legal notice (ADR-002)

PAGES = [
    "Permitted daily exposure for elemental impurities\nCadmium oral 5 ug/day",
    "Class 1 elements: arsenic, cadmium, lead and mercury",
    "Risk assessment of the drug product and its components",
]


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
    finally:  # clean up even if seeding fails, or the open transaction blocks later tests
        sess.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def store(tmp_path: Path) -> LocalFolderStore:
    return LocalFolderStore(tmp_path / "files")


def _service(session: Session, store: LocalFolderStore, max_bytes: int = 5_000_000):
    return DocumentIngestionService(session, store, max_bytes=max_bytes)


def _stored_files(store: LocalFolderStore) -> list[str]:
    if not store.root.exists():
        return []
    return sorted(
        p.relative_to(store.root).as_posix() for p in store.root.rglob("*") if p.is_file()
    )


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- the normal path -------------------------------------------------------------------------


def test_a_document_is_stored_fingerprinted_read_and_recorded(
    session: Session, store: LocalFolderStore
) -> None:
    pdf = make_pdf(PAGES)
    result = _service(session, store).ingest(source_id=ELIGIBLE, data=pdf)

    sha = hashlib.sha256(pdf).hexdigest()
    assert (result.source_id, result.version_number, result.already_ingested) == (
        ELIGIBLE,
        1,
        False,
    )
    assert result.sha256 == sha and result.byte_size == len(pdf)
    assert (result.page_count, result.text_layer) == (3, "yes")
    assert (result.extraction_method, result.extraction_status) == ("pypdf", "ok")
    assert result.object_key == f"originals/{ELIGIBLE}/{sha}.pdf"

    # The original is kept byte for byte, and the page text is stored beside it.
    assert store.get(result.object_key) == pdf
    pages = json.loads(store.get(result.extracted_key))
    assert [p["page"] for p in pages["pages"]] == [1, 2, 3]
    assert "Cadmium oral 5 ug/day" in pages["pages"][0]["text"]

    # And the database knows about it.
    version = session.get(DocumentVersion, result.version_id)
    assert version is not None and version.sha256 == sha and version.text_layer == "yes"
    assert version.extraction_status == "ok" and version.extraction_notes == []
    document = session.get(Document, result.document_id)
    assert document is not None and document.source_id == ELIGIBLE and document.eligible


def test_the_version_label_comes_from_the_source_unless_one_is_given(
    session: Session, store: LocalFolderStore
) -> None:
    service = _service(session, store)
    first = service.ingest(source_id=ELIGIBLE, data=make_pdf(PAGES))
    assert first.version_label == "Q3D(R2)"
    second = service.ingest(
        source_id=ELIGIBLE, data=make_pdf(PAGES + ["extra"]), version_label="R3"
    )
    assert second.version_label == "R3"


def test_the_same_file_twice_changes_nothing(session: Session, store: LocalFolderStore) -> None:
    pdf = make_pdf(PAGES)
    service = _service(session, store)
    first = service.ingest(source_id=ELIGIBLE, data=pdf)
    files = _stored_files(store)
    second = service.ingest(source_id=ELIGIBLE, data=pdf)

    assert second.already_ingested is True
    assert second.version_id == first.version_id and second.version_number == 1
    assert _count(session, DocumentVersion) == 1
    assert _stored_files(store) == files


def test_a_changed_file_becomes_a_new_version_and_the_old_one_is_kept(
    session: Session, store: LocalFolderStore
) -> None:
    service = _service(session, store)
    first_pdf, second_pdf = make_pdf(PAGES), make_pdf(PAGES[:2] + ["a revised third page"])
    first = service.ingest(source_id=ELIGIBLE, data=first_pdf)
    second = service.ingest(source_id=ELIGIBLE, data=second_pdf)

    assert (first.version_number, second.version_number) == (1, 2)
    assert first.document_id == second.document_id  # one document per source, many versions
    assert first.sha256 != second.sha256 and first.object_key != second.object_key
    assert store.get(first.object_key) == first_pdf
    assert store.get(second.object_key) == second_pdf
    assert _count(session, Document) == 1 and _count(session, DocumentVersion) == 2


# --- the licence rule ------------------------------------------------------------------------


def test_a_held_source_is_refused_and_nothing_is_stored(
    session: Session, store: LocalFolderStore
) -> None:
    with pytest.raises(SourceNotEligible, match="no legal notice"):
        _service(session, store).ingest(source_id=HELD, data=make_pdf(PAGES))
    assert _stored_files(store) == []
    assert _count(session, Document) == 0


def test_the_database_itself_also_refuses_a_held_source(session: Session) -> None:
    # Even if this service were bypassed, the licence rule (ADR-002) holds in the database.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), session.begin_nested():
        DocumentRepository(session).register_document(HELD)


def test_an_unknown_source_is_refused(session: Session, store: LocalFolderStore) -> None:
    with pytest.raises(SourceNotFound):
        _service(session, store).ingest(source_id="NO-SUCH-SOURCE", data=make_pdf(PAGES))
    assert _stored_files(store) == []


# --- files that must be refused, leaving no trace ---------------------------------------------


def _encrypted_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(PAGES))))
    writer.encrypt("a-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "data, error",
    [
        (b"", EmptyFile),
        (b"<html>this is a web page</html>", NotAPdf),
        (b"%PDF-1.4\nthis is not really a pdf\n%%EOF", UnreadablePdf),
        (_encrypted_pdf(), UnreadablePdf),
    ],
    ids=["empty", "not-a-pdf", "damaged", "password-protected"],
)
def test_a_bad_file_is_refused_and_leaves_nothing_behind(
    session: Session, store: LocalFolderStore, data: bytes, error: type[Exception]
) -> None:
    with pytest.raises(error):
        _service(session, store).ingest(source_id=ELIGIBLE, data=data)
    assert _stored_files(store) == []
    assert _count(session, Document) == 0 and _count(session, DocumentVersion) == 0


def test_a_file_over_the_size_limit_is_refused(session: Session, store: LocalFolderStore) -> None:
    pdf = make_pdf(PAGES)
    with pytest.raises(FileTooLarge):
        _service(session, store, max_bytes=len(pdf) - 1).ingest(source_id=ELIGIBLE, data=pdf)
    assert _stored_files(store) == []
    # Exactly at the limit is fine.
    assert _service(session, store, max_bytes=len(pdf)).ingest(source_id=ELIGIBLE, data=pdf)


# --- scans: stored, but honestly marked as not searchable ------------------------------------


def test_a_scanned_document_is_kept_but_marked_as_having_no_text(
    session: Session, store: LocalFolderStore
) -> None:
    result = _service(session, store).ingest(source_id=ELIGIBLE, data=make_pdf([None, None]))
    assert (result.text_layer, result.extraction_status) == ("no", "failed")
    assert any("OCR" in note for note in result.extraction_notes)
    assert store.exists(result.object_key)  # the original is still kept
    version = session.get(DocumentVersion, result.version_id)
    assert version is not None and version.extraction_notes  # and the reason is recorded


# --- project access --------------------------------------------------------------------------


def test_a_document_is_linked_to_the_projects_it_is_for(
    session: Session, store: LocalFolderStore
) -> None:
    service = _service(session, store)
    pdf = make_pdf(PAGES)
    first = service.ingest(source_id=ELIGIBLE, data=pdf, project_ids=["PRJ-001"])
    assert first.project_ids == ("PRJ-001",)
    # Ingesting the same file for another project adds that link and nothing else.
    again = service.ingest(source_id=ELIGIBLE, data=pdf, project_ids=["PRJ-001", "PRJ-002"])
    assert again.already_ingested is True
    linked = set(
        session.scalars(
            select(ProjectDocument.project_id).where(
                ProjectDocument.document_id == first.document_id
            )
        )
    )
    assert linked == {"PRJ-001", "PRJ-002"}
    assert _count(session, DocumentVersion) == 1


def test_an_unknown_project_is_refused_before_anything_is_stored(
    session: Session, store: LocalFolderStore
) -> None:
    with pytest.raises(ProjectNotFound, match="PRJ-404"):
        _service(session, store).ingest(
            source_id=ELIGIBLE, data=make_pdf(PAGES), project_ids=["PRJ-001", "PRJ-404"]
        )
    assert _stored_files(store) == []
    assert _count(session, Document) == 0


# --- failing half way ------------------------------------------------------------------------


def test_a_failure_after_the_files_are_stored_leaves_no_rows_and_a_retry_works(
    session: Session, store: LocalFolderStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(PAGES)
    real_add_version = DocumentRepository.add_version

    def explode(self, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(DocumentRepository, "add_version", explode)
    with pytest.raises(RuntimeError), session.begin_nested():
        _service(session, store).ingest(source_id=ELIGIBLE, data=pdf)
    assert _count(session, Document) == 0 and _count(session, DocumentVersion) == 0
    files_after_failure = _stored_files(store)
    assert files_after_failure  # the files were written first, by design

    monkeypatch.setattr(DocumentRepository, "add_version", real_add_version)
    result = _service(session, store).ingest(source_id=ELIGIBLE, data=pdf)
    assert result.version_number == 1 and not result.already_ingested
    assert _stored_files(store) == files_after_failure  # same names, same bytes: nothing doubled
    assert _count(session, DocumentVersion) == 1
