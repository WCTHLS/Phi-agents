"""Tests for indexing a document version into embedded passages (S2-06, ADR-008).

Runs against a real PostgreSQL + pgvector database (see test_db_integration.py) with a stand-in
embedding model, so no model is downloaded. Each test runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from fake_embedder import FailingEmbedder, FakeEmbedder
from pdf_factory import make_pdf
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.features.documents.indexing import (
    ChunkSettingsInvalid,
    DocumentIndexingService,
    ExtractedTextMissing,
    VersionNotFound,
)
from pharma_ai.features.documents.ingestion import DocumentIngestionService, IngestionResult
from pharma_ai.infrastructure.db.models import EMBEDDING_DIM, Chunk
from pharma_ai.infrastructure.db.repositories import ChunkRepository
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url
from pharma_ai.infrastructure.storage import LocalFolderStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

ELIGIBLE = "ICH-Q3DR2"
PAGES = [
    "1. INTRODUCTION\n\nCadmium has an oral permitted daily exposure of five micrograms per day.",
    "2. SCOPE\n\nThe guideline covers new drug products containing purified proteins and peptides.",
    "3. RISK ASSESSMENT\n\nFormulation scientists assess every excipient for elemental impurities.",
]
SMALL = {"max_tokens": 20, "overlap_tokens": 0}  # one section per chunk, whatever the word count


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


def _ingest(session: Session, store: LocalFolderStore, pages=PAGES) -> IngestionResult:
    service = DocumentIngestionService(session, store, max_bytes=5_000_000)
    return service.ingest(source_id=ELIGIBLE, data=make_pdf(list(pages)), project_ids=["PRJ-001"])


def _indexer(session: Session, store: LocalFolderStore, embedder=None, **kwargs):
    return DocumentIndexingService(session, store, embedder or FakeEmbedder(), **(SMALL | kwargs))


def _chunks(session: Session, version_id: uuid.UUID) -> list[Chunk]:
    return list(
        session.scalars(
            select(Chunk).where(Chunk.version_id == version_id).order_by(Chunk.chunk_index)
        )
    )


# --- the normal path -------------------------------------------------------------------------


def test_a_version_is_cut_embedded_and_stored(session: Session, store: LocalFolderStore) -> None:
    ingested = _ingest(session, store)
    result = _indexer(session, store).index_version(ingested.version_id)

    assert result.status == "indexed" and result.previous_chunk_count == 0
    assert (result.source_id, result.version_number) == (ELIGIBLE, 1)
    assert result.embedding_model == "fake-bag-of-words"
    rows = _chunks(session, ingested.version_id)
    assert len(rows) == result.chunk_count == 3
    assert [r.chunk_index for r in rows] == [0, 1, 2]
    assert [(r.page_start, r.page_end) for r in rows] == [(1, 1), (2, 2), (3, 3)]
    assert [r.section for r in rows] == ["1. INTRODUCTION", "2. SCOPE", "3. RISK ASSESSMENT"]
    assert "Cadmium has an oral permitted daily exposure" in rows[0].text
    for row in rows:
        assert row.embedding_model == "fake-bag-of-words"
        assert len(row.embedding) == EMBEDDING_DIM
        assert row.token_count == len(row.text.split()) and row.chunk_type == "paragraph"


def test_all_passages_are_embedded_in_one_call_for_the_model_to_batch(
    session: Session, store: LocalFolderStore
) -> None:
    embedder = FakeEmbedder()
    _indexer(session, store, embedder).index_version(_ingest(session, store).version_id)
    assert len(embedder.calls) == 1 and len(embedder.calls[0]) == 3


def test_indexing_twice_with_the_same_model_changes_nothing(
    session: Session, store: LocalFolderStore
) -> None:
    ingested = _ingest(session, store)
    service = _indexer(session, store)
    service.index_version(ingested.version_id)
    before = [r.chunk_id for r in _chunks(session, ingested.version_id)]

    embedder = FakeEmbedder()
    again = _indexer(session, store, embedder).index_version(ingested.version_id)

    assert again.status == "already_indexed" and again.chunk_count == 3
    assert [r.chunk_id for r in _chunks(session, ingested.version_id)] == before
    assert embedder.calls == []  # not even a call to the model


def test_force_replaces_the_passages(session: Session, store: LocalFolderStore) -> None:
    ingested = _ingest(session, store)
    _indexer(session, store).index_version(ingested.version_id)
    before = {r.chunk_id for r in _chunks(session, ingested.version_id)}

    again = _indexer(session, store).index_version(ingested.version_id, force=True)

    assert again.status == "reindexed" and again.previous_chunk_count == 3
    after = {r.chunk_id for r in _chunks(session, ingested.version_id)}
    assert len(after) == 3 and not (before & after)


def test_passages_from_another_model_are_replaced_never_mixed(
    session: Session, store: LocalFolderStore
) -> None:
    ingested = _ingest(session, store)
    _indexer(session, store, FakeEmbedder("old-model")).index_version(ingested.version_id)

    result = _indexer(session, store, FakeEmbedder("new-model")).index_version(ingested.version_id)

    assert result.status == "reindexed"
    assert {r.embedding_model for r in _chunks(session, ingested.version_id)} == {"new-model"}


# --- things that go wrong ----------------------------------------------------------------------


def test_a_scan_without_text_is_skipped_and_reported(
    session: Session, store: LocalFolderStore
) -> None:
    ingested = _ingest(session, store, pages=[None, None])
    result = _indexer(session, store).index_version(ingested.version_id)

    assert result.status == "skipped" and result.chunk_count == 0
    assert result.reason and "OCR" in result.reason
    assert _chunks(session, ingested.version_id) == []


def test_a_failing_model_leaves_the_existing_passages_alone(
    session: Session, store: LocalFolderStore
) -> None:
    ingested = _ingest(session, store)
    _indexer(session, store).index_version(ingested.version_id)
    before = [r.chunk_id for r in _chunks(session, ingested.version_id)]

    with pytest.raises(RuntimeError, match="fell over"):
        _indexer(session, store, FailingEmbedder()).index_version(ingested.version_id, force=True)

    assert [r.chunk_id for r in _chunks(session, ingested.version_id)] == before


def test_an_unknown_version_is_refused(session: Session, store: LocalFolderStore) -> None:
    with pytest.raises(VersionNotFound):
        _indexer(session, store).index_version(uuid.uuid4())


def test_missing_extracted_text_is_reported_not_ignored(
    session: Session, store: LocalFolderStore
) -> None:
    ingested = _ingest(session, store)
    (store.root / ingested.extracted_key).unlink()
    with pytest.raises(ExtractedTextMissing):
        _indexer(session, store).index_version(ingested.version_id)


def test_a_chunk_size_the_model_cannot_read_in_full_is_refused(
    session: Session, store: LocalFolderStore
) -> None:
    with pytest.raises(ChunkSettingsInvalid, match="reads in full"):
        DocumentIndexingService(
            session, store, FakeEmbedder(max_input_tokens=512), max_tokens=511, overlap_tokens=0
        )
    # 510 plus the model's two markers is exactly its limit.
    DocumentIndexingService(
        session, store, FakeEmbedder(max_input_tokens=512), max_tokens=510, overlap_tokens=0
    )


# --- the point of it all: the right passage is found ---------------------------------------------


def test_a_question_finds_the_passage_that_answers_it_with_everything_a_citation_needs(
    session: Session, store: LocalFolderStore
) -> None:
    ingested = _ingest(session, store)
    embedder = FakeEmbedder()
    _indexer(session, store, embedder).index_version(ingested.version_id)

    question = embedder.embed(["what is the oral exposure of cadmium per day"])[0]
    hits = ChunkRepository(session).search_nearest(question, ["PRJ-001"], limit=3)

    best = hits[0]
    assert (best.source_id, best.version_number, best.sha256) == (ELIGIBLE, 1, ingested.sha256)
    assert (best.page_start, best.page_end, best.section) == (1, 1, "1. INTRODUCTION")
    assert "Cadmium has an oral permitted daily exposure" in best.text
    assert best.distance < hits[1].distance
    # A project that was not granted the document gets nothing.
    assert ChunkRepository(session).search_nearest(question, ["PRJ-002"], limit=3) == []
