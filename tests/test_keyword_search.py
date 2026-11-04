"""Tests for keyword search over chunks (S2-07), against a real PostgreSQL + pgvector database.

Keyword search must follow exactly the same visibility rules as the vector search: the caller's
projects, an eligible source, the named embedding model and the newest version. The word-handling
tests need no database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.infrastructure.db.models import EMBEDDING_DIM, Chunk, Document
from pharma_ai.infrastructure.db.repositories import (
    MAX_KEYWORD_TERMS,
    ChunkInput,
    ChunkRepository,
    DocumentRepository,
    keyword_terms,
)
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

MODEL = "test-model"
NOW = datetime(2026, 11, 3, tzinfo=UTC)
Q3D, Q3C, M9 = "ICH-Q3DR2", "ICH-Q3CR9", "ICH-M9"  # eligible sources in the seed data


def vector(position: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[position] = 1.0
    return v


# --- the words of a question (no database) ------------------------------------------------------


def test_a_question_becomes_plain_lower_case_words() -> None:
    assert keyword_terms("What is the oral PDE for Lead (Pb) in ICH Q3D?") == [
        "what", "is", "the", "oral", "pde", "for", "lead", "pb", "in", "ich", "q3d",
    ]  # fmt: skip


def test_guideline_codes_and_numbers_survive() -> None:
    assert keyword_terms("ICH M13A, Q3C(R9) and 26.5 ng/day") == [
        "ich", "m13a", "q3c", "r9", "and", "26", "ng", "day",
    ]  # fmt: skip


def test_words_are_not_repeated_and_single_characters_and_or_are_dropped() -> None:
    assert keyword_terms("a lead OR lead or Lead x 5 mg") == ["lead", "mg"]


def test_punctuation_that_could_be_search_syntax_is_removed() -> None:
    assert keyword_terms("""lead & mercury | !arsenic (cadmium) :* <-> "quoted" 'x'; -- \\""") == [
        "lead", "mercury", "arsenic", "cadmium", "quoted",
    ]  # fmt: skip


def test_an_over_long_question_is_cut_to_a_fixed_number_of_words() -> None:
    words = keyword_terms(" ".join(f"word{n}" for n in range(500)))
    assert len(words) == MAX_KEYWORD_TERMS and words[0] == "word0"


def test_nothing_usable_gives_no_words() -> None:
    assert keyword_terms("") == [] and keyword_terms("?! & | ( ) a") == []


# --- the search ---------------------------------------------------------------------------------


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


def add_document(
    session: Session,
    source_id: str,
    project: str | None,
    texts: list[str],
    *,
    sections: list[str | None] | None = None,
    model: str | None = MODEL,
    version_number: int = 1,
    document: Document | None = None,
    store_in_reverse: bool = False,
) -> Document:
    """A document with one chunk per text, visible to ``project`` (None = not granted)."""
    docs = DocumentRepository(session)
    if document is None:
        document = docs.register_document(source_id)
        if project:
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
    chunks = [
        ChunkInput(
            chunk_index=i,
            page_start=i + 1,
            page_end=i + 1,
            text=body,
            section=(sections or [None] * len(texts))[i],
            embedding=vector(i) if model else None,
            embedding_model=model,
        )
        for i, body in enumerate(texts)
    ]
    ChunkRepository(session).add_chunks(
        version.version_id, list(reversed(chunks)) if store_in_reverse else chunks
    )
    return document


def search(session: Session, question: str, projects: list[str] | None = None, **kw):
    return ChunkRepository(session).search_keywords(
        question, projects if projects is not None else ["PRJ-001"], embedding_model=MODEL, **kw
    )


@needs_db
def test_a_passage_with_the_term_is_found_and_one_without_it_is_not(session: Session) -> None:
    add_document(
        session,
        Q3D,
        "PRJ-001",
        ["The oral PDE for lead is five micrograms.", "Residual solvents are classified by risk."],
    )
    hits = search(session, "lead")
    assert [h.text for h in hits] == ["The oral PDE for lead is five micrograms."]
    assert hits[0].source_id == Q3D and (hits[0].page_start, hits[0].page_end) == (1, 1)
    assert hits[0].keyword_score and hits[0].keyword_score > 0
    assert hits[0].distance is None  # this hit did not come from the vector search


@needs_db
def test_a_hit_carries_everything_a_citation_needs(session: Session) -> None:
    add_document(session, Q3D, "PRJ-001", ["lead is limited"], sections=["4.2 Solvents"])
    hit = search(session, "lead")[0]
    assert (hit.version_label, hit.version_number, hit.section) == ("Revision 1", 1, "4.2 Solvents")
    assert hit.attribution and "ICH" in hit.attribution and len(hit.sha256) == 64


@needs_db
def test_any_word_of_the_question_can_match_and_more_matches_rank_higher(session: Session) -> None:
    add_document(
        session,
        Q3D,
        "PRJ-001",
        [
            "Only mercury is mentioned here.",
            "Lead and mercury and arsenic are all named in this passage.",
            "Nothing relevant at all.",
        ],
    )
    hits = search(session, "What are the limits for lead, mercury and arsenic?")
    assert [h.page_start for h in hits] == [2, 1]  # the passage with all three words comes first


@needs_db
def test_words_are_matched_by_their_stems(session: Session) -> None:
    add_document(session, Q3D, "PRJ-001", ["The limit applies to each element."])
    assert len(search(session, "limits")) == 1
    assert len(search(session, "applying elements")) == 1


@needs_db
def test_the_section_heading_is_searchable_too(session: Session) -> None:
    add_document(
        session, Q3D, "PRJ-001", ["This passage never names it."], sections=["Appendix 3: Lead"]
    )
    assert len(search(session, "lead")) == 1


@needs_db
@pytest.mark.parametrize("question", ["", "   ", "?!", "a", "the of and", "or or or"])
def test_a_question_with_nothing_to_search_for_finds_nothing(
    session: Session, question: str
) -> None:
    add_document(session, Q3D, "PRJ-001", ["The lead limit."])
    assert search(session, question) == []


@needs_db
@pytest.mark.parametrize(
    "question",
    [
        "'; drop table chunks; --",
        'lead" OR "1"="1',
        "lead & !mercury | (arsenic) :* <-> x",
        "lead\\",
        "\x00lead",
        "lead " * 500,
    ],
)
def test_hostile_input_neither_fails_nor_changes_anything(session: Session, question: str) -> None:
    add_document(session, Q3D, "PRJ-001", ["The lead limit."])
    search(session, question)  # an error here fails the test
    assert session.scalar(select(func.count()).select_from(Chunk)) == 1


@needs_db
def test_only_the_callers_projects_are_searched(session: Session) -> None:
    add_document(session, Q3D, "PRJ-001", ["lead in project one"])
    add_document(session, Q3C, "PRJ-002", ["lead in project two"])
    assert [h.source_id for h in search(session, "lead", ["PRJ-001"])] == [Q3D]
    assert [h.source_id for h in search(session, "lead", ["PRJ-002"])] == [Q3C]
    assert {h.source_id for h in search(session, "lead", ["PRJ-001", "PRJ-002"])} == {Q3D, Q3C}
    assert search(session, "lead", []) == [] and search(session, "lead", ["PRJ-999"]) == []


@needs_db
def test_a_document_not_granted_to_any_project_is_never_returned(session: Session) -> None:
    add_document(session, Q3D, None, ["lead in an ungranted document"])
    assert search(session, "lead", ["PRJ-001", "PRJ-002"]) == []


@needs_db
def test_eligibility_is_checked_even_if_the_foreign_key_were_bypassed(session: Session) -> None:
    """Same defence in depth as the vector search: the source is made ineligible behind the
    database's back (needs a superuser, as in the throwaway test database)."""
    add_document(session, Q3D, "PRJ-001", ["lead here"])
    add_document(session, Q3C, "PRJ-001", ["lead there"])
    session.execute(text("alter table sources disable trigger all"))
    session.execute(
        text("update sources set eligible = false, tier = 'hold' where source_id = :s"), {"s": Q3D}
    )
    assert [h.source_id for h in search(session, "lead")] == [Q3C]


@needs_db
def test_passages_from_another_model_or_with_no_embedding_are_not_returned(
    session: Session,
) -> None:
    add_document(session, Q3D, "PRJ-001", ["lead from the current model"])
    add_document(session, Q3C, "PRJ-001", ["lead from another model"], model="other-model")
    add_document(session, M9, "PRJ-001", ["lead from a passage not embedded yet"], model=None)
    assert [h.source_id for h in search(session, "lead")] == [Q3D]


@needs_db
def test_only_the_newest_version_is_searched_unless_asked(session: Session) -> None:
    document = add_document(session, Q3D, "PRJ-001", ["lead in the old revision"])
    add_document(
        session, Q3D, None, ["lead in the new revision"], version_number=2, document=document
    )
    assert [(h.version_number, h.text) for h in search(session, "lead")] == [
        (2, "lead in the new revision")
    ]
    both = search(session, "lead", latest_only=False)
    assert sorted(h.version_number for h in both) == [1, 2]


@needs_db
def test_the_limit_is_applied_and_equal_scores_come_back_in_a_fixed_order(session: Session) -> None:
    # Same length and same word, so the scores are equal. They are stored last position first, so
    # only an explicit tie-break (document, then position) gives 1, 2, 3 and not 4, 3, 2.
    add_document(
        session,
        Q3D,
        "PRJ-001",
        ["lead aaa", "lead bbb", "lead ccc", "lead ddd"],
        store_in_reverse=True,
    )
    first = search(session, "lead", limit=3)
    assert len(first) == 3
    assert len({h.keyword_score for h in first}) == 1  # the scores really are equal
    assert [h.chunk_id for h in first] == [h.chunk_id for h in search(session, "lead", limit=3)]
    assert [h.page_start for h in first] == [1, 2, 3]


@needs_db
def test_the_database_fills_in_the_search_words_itself(session: Session) -> None:
    add_document(session, Q3D, "PRJ-001", ["Cadmium limits"], sections=["Heading words"])
    stored = session.scalar(select(Chunk.search_vector))
    assert stored is not None
    assert all(stem in str(stored) for stem in ("cadmium", "limit", "head", "word"))


@needs_db
def test_a_vector_hit_has_no_keyword_score(session: Session) -> None:
    add_document(session, Q3D, "PRJ-001", ["lead"])
    hit = ChunkRepository(session).search_nearest(vector(0), ["PRJ-001"], embedding_model=MODEL)[0]
    assert hit.distance is not None and hit.keyword_score is None
