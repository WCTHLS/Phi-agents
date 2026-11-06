"""Tests for hybrid search: merging the vector and keyword rankings (S2-07).

The merging rules need no database. The end-to-end tests use a real PostgreSQL + pgvector database
and a stand-in embedding model that returns a chosen vector, so it is clear which search found what.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.features.research.search import (
    CANDIDATE_DEPTH,
    RRF_K,
    PassageSearch,
    fuse,
    hybrid_search,
)
from pharma_ai.infrastructure.db.models import EMBEDDING_DIM
from pharma_ai.infrastructure.db.repositories import (
    ChunkHit,
    ChunkInput,
    ChunkRepository,
    DocumentRepository,
)
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url
from pharma_ai.infrastructure.embeddings import Embedder

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)


def hit(name: str, *, distance: float | None = None, keyword: float | None = None) -> ChunkHit:
    """A passage whose chunk id is derived from its name, so the same name is the same passage."""
    return ChunkHit(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        source_id=name,
        version_id=uuid.uuid4(),
        version_label=None,
        version_number=1,
        sha256="0" * 64,
        page_start=1,
        page_end=1,
        section=None,
        text=name,
        attribution=None,
        distance=distance,
        keyword_score=keyword,
    )


def names(fused) -> list[str]:
    return [f.hit.source_id for f in fused]


# --- merging two rankings (no database) ---------------------------------------------------------


class _TwoLists:
    """Stands in for the repository: each search returns the first ``limit`` of a fixed list."""

    def __init__(self, vector: list[ChunkHit], keyword: list[ChunkHit]) -> None:
        self._vector, self._keyword = vector, keyword

    def __call__(self, _session: object) -> _TwoLists:
        return self

    def search_nearest(self, _vector, _projects, *, limit: int, **_kw) -> list[ChunkHit]:
        return self._vector[:limit]

    def search_keywords(self, _question, _projects, *, limit: int, **_kw) -> list[ChunkHit]:
        return self._keyword[:limit]


def test_a_passage_strong_in_one_search_and_25th_in_the_other_is_still_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The shape of a real miss: the Class 1 PDE table was 4th by meaning and 25th by keywords (28th here).
    # With only 20 keyword candidates it earned nothing from that list and fell out of the top 3.
    vector = [hit(n, distance=0.1 + i / 100) for i, n in enumerate(["V1", "V2", "V3", "TABLE"])]
    keyword = [hit("F0", keyword=9.0), hit("V1", keyword=8.5), hit("V2", keyword=8.4)]
    keyword += [hit(f"F{i}", keyword=8.0 - i / 10) for i in range(1, 25)]  # ranks 4 to 27
    keyword.append(hit("TABLE", keyword=1.0))  # rank 28: beyond 20, within 50
    fake = _TwoLists(vector, keyword)
    monkeypatch.setattr("pharma_ai.features.research.search.ChunkRepository", fake)

    def top3(**kw):
        found = hybrid_search(
            None, question="q", vector=[0.0], projects=["P"], embedding_model="m", limit=3, **kw
        )
        return names(found)

    assert "TABLE" in top3()  # the default depth
    assert "TABLE" not in top3(depth=20)  # what used to happen


def test_a_passage_near_the_top_of_both_lists_beats_one_that_tops_a_single_list() -> None:
    vector = [hit("A", distance=0.1), hit("B", distance=0.2), hit("C", distance=0.3)]
    keyword = [hit("D", keyword=9.0), hit("B", keyword=8.0), hit("E", keyword=7.0)]
    fused = fuse(vector, keyword, limit=10)
    assert names(fused)[0] == "B"  # second in both lists
    assert set(names(fused)) == {"A", "B", "C", "D", "E"}  # a one-list passage still gets in


def test_the_score_is_the_sum_of_one_over_k_plus_rank_for_each_list() -> None:
    fused = fuse([hit("A"), hit("B")], [hit("B"), hit("C")], limit=10)
    by_name = {f.hit.source_id: f for f in fused}
    assert by_name["A"].score == pytest.approx(1 / (RRF_K + 1))
    assert by_name["B"].score == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert by_name["C"].score == pytest.approx(1 / (RRF_K + 2))
    assert (by_name["B"].vector_rank, by_name["B"].keyword_rank) == (2, 1)
    assert (by_name["A"].vector_rank, by_name["A"].keyword_rank) == (1, None)
    assert (by_name["C"].vector_rank, by_name["C"].keyword_rank) == (None, 2)


def test_a_passage_found_by_both_keeps_both_scores() -> None:
    fused = fuse([hit("A", distance=0.25)], [hit("A", keyword=3.5)], limit=5)
    assert len(fused) == 1
    assert fused[0].hit.distance == 0.25 and fused[0].hit.keyword_score == 3.5


def test_a_passage_found_by_one_search_keeps_only_that_score() -> None:
    fused = fuse([hit("A", distance=0.25)], [hit("B", keyword=3.5)], limit=5)
    scores = {f.hit.source_id: (f.hit.distance, f.hit.keyword_score) for f in fused}
    assert scores == {"A": (0.25, None), "B": (None, 3.5)}


def test_the_limit_is_applied_after_merging() -> None:
    vector = [hit(f"V{n}") for n in range(10)]
    keyword = [hit(f"K{n}") for n in range(10)]
    assert len(fuse(vector, keyword, limit=4)) == 4
    assert names(fuse(vector, keyword, limit=1)) == ["V0"]  # equal totals: the vector list's first


def test_equal_scores_are_ordered_the_same_way_every_time() -> None:
    vector = [hit("V1"), hit("V2")]
    keyword = [hit("K1"), hit("K2")]
    first = names(fuse(vector, keyword, limit=10))
    assert first == names(fuse(vector, keyword, limit=10))
    assert first == names(fuse(list(vector), list(keyword), limit=10))
    # Rank 1 in either list comes before rank 2 in either list.
    assert set(first[:2]) == {"V1", "K1"} and set(first[2:]) == {"V2", "K2"}


def test_one_empty_list_gives_the_other_in_its_own_order() -> None:
    assert names(fuse([hit("A"), hit("B")], [], limit=5)) == ["A", "B"]
    assert names(fuse([], [hit("X"), hit("Y")], limit=5)) == ["X", "Y"]
    assert fuse([], [], limit=5) == []


def test_a_passage_repeated_in_one_list_counts_by_its_best_place() -> None:
    fused = fuse([hit("A"), hit("B"), hit("A")], [], limit=5)
    assert names(fused) == ["A", "B"]


# --- end to end ---------------------------------------------------------------------------------

NOW = datetime(2026, 11, 3, tzinfo=UTC)
MODEL = "test-model"


def vector(position: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[position] = 1.0
    return v


class FixedEmbedder(Embedder):
    """Embeds every question as the same chosen vector, so the vector search is predictable."""

    def __init__(self, position: int, name: str = MODEL) -> None:
        self._position, self._name = position, name
        self.calls: list[list[str]] = []

    model_name = property(lambda self: self._name)
    dimension = property(lambda self: EMBEDDING_DIM)
    max_input_tokens = property(lambda self: 512)

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [vector(self._position) for _ in texts]


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
    session: Session, source_id: str, project: str, passages: list[tuple[str, int]]
) -> None:
    """A document whose chunks are (text, embedding position)."""
    docs = DocumentRepository(session)
    document = docs.register_document(source_id)
    docs.grant_project_access(document.document_id, project)
    version = docs.add_version(
        document_id=document.document_id,
        version_number=1,
        version_label="Revision 1",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        object_key=f"originals/{source_id}.pdf",
        byte_size=100,
        retrieved_at=NOW,
    )
    ChunkRepository(session).add_chunks(
        version.version_id,
        [
            ChunkInput(
                chunk_index=i,
                page_start=i + 1,
                page_end=i + 1,
                text=body,
                embedding=vector(position),
                embedding_model=MODEL,
            )
            for i, (body, position) in enumerate(passages)
        ],
    )


def corpus(session: Session) -> None:
    add_document(
        session,
        "ICH-Q3DR2",
        "PRJ-001",
        [
            ("Nothing about the question words at all.", 0),  # nearest in meaning, no keyword
            ("The cadmium level is stated here.", 7),  # one question word, far in meaning
            ("Cadmium limit again, and close in meaning too.", 1),  # both words, nearest in meaning
            ("Irrelevant filler text.", 9),
        ],
    )


@needs_db
def test_keywords_bring_in_a_passage_the_vector_search_ranks_too_low(session: Session) -> None:
    add_document(
        session,
        "ICH-Q3DR2",
        "PRJ-001",
        [
            ("Filler one.", 0),
            ("Filler two.", 0),
            ("Filler three.", 0),
            ("The cadmium limit is stated here.", 7),  # far from the question in meaning
        ],
    )
    common = {"vector": vector(0), "projects": ["PRJ-001"], "embedding_model": MODEL}
    by_meaning = ChunkRepository(session).search_nearest(
        vector(0), ["PRJ-001"], embedding_model=MODEL, limit=3
    )
    assert all("cadmium" not in h.text for h in by_meaning)  # vector search alone misses it

    fused = hybrid_search(session, question="cadmium limit", limit=3, depth=3, **common)
    target = [f for f in fused if "cadmium" in f.hit.text]
    assert len(target) == 1
    assert target[0].vector_rank is None and target[0].keyword_rank == 1


@needs_db
def test_a_passage_only_the_vector_search_finds_is_kept(session: Session) -> None:
    corpus(session)
    fused = hybrid_search(
        session,
        question="cadmium limit",
        vector=vector(0),
        projects=["PRJ-001"],
        embedding_model=MODEL,
        limit=10,
    )
    nearest = next(f for f in fused if f.hit.text.startswith("Nothing about"))
    assert nearest.vector_rank == 1 and nearest.keyword_rank is None


@needs_db
def test_a_passage_good_at_both_comes_first(session: Session) -> None:
    corpus(session)
    # The question vector is nearest to passage 0, then 1 ("again ... close in meaning").
    fused = hybrid_search(
        session,
        question="cadmium limit",
        vector=vector(1),
        projects=["PRJ-001"],
        embedding_model=MODEL,
        limit=3,
    )
    assert fused[0].hit.text == "Cadmium limit again, and close in meaning too."
    assert fused[0].vector_rank == 1 and fused[0].keyword_rank is not None


@needs_db
def test_the_visibility_rules_apply_to_both_searches(session: Session) -> None:
    corpus(session)
    add_document(session, "ICH-Q3CR9", "PRJ-002", [("Cadmium in another project, exact match.", 0)])
    kwargs = {"question": "cadmium", "vector": vector(0), "embedding_model": MODEL, "limit": 10}
    one = hybrid_search(session, projects=["PRJ-001"], **kwargs)
    assert {f.hit.source_id for f in one} == {"ICH-Q3DR2"}
    two = hybrid_search(session, projects=["PRJ-002"], **kwargs)
    assert {f.hit.source_id for f in two} == {"ICH-Q3CR9"}
    assert hybrid_search(session, projects=[], **kwargs) == []
    assert hybrid_search(session, projects=["PRJ-999"], **kwargs) == []
    assert hybrid_search(session, projects=["PRJ-001"], **{**kwargs, "embedding_model": "x"}) == []


@needs_db
def test_a_question_with_no_words_or_a_zero_limit_returns_nothing(session: Session) -> None:
    corpus(session)
    common = {"vector": vector(0), "projects": ["PRJ-001"], "embedding_model": MODEL}
    assert hybrid_search(session, question="   ", limit=5, **common) == []
    # A negative limit must not be read as "all but the last".
    assert hybrid_search(session, question="cadmium", limit=0, **common) == []
    assert hybrid_search(session, question="cadmium", limit=-1, **common) == []


@needs_db
def test_the_search_embeds_the_question_with_the_model_it_names_and_returns_citable_passages(
    session: Session,
) -> None:
    corpus(session)
    embedder = FixedEmbedder(position=1)
    results = PassageSearch(session, embedder).search("cadmium limit", ["PRJ-001"], limit=2)
    assert embedder.calls == [["cadmium limit"]]
    top = results[0].hit
    assert top.text == "Cadmium limit again, and close in meaning too."
    assert (top.source_id, top.version_label, top.page_start, len(top.sha256)) == (
        "ICH-Q3DR2",
        "Revision 1",
        3,
        64,
    )
    assert top.attribution is not None  # the attribution travels with every passage (ADR-002)


@needs_db
def test_the_search_does_not_even_embed_when_there_is_nothing_to_do(session: Session) -> None:
    corpus(session)
    embedder = FixedEmbedder(position=1)
    search = PassageSearch(session, embedder)
    assert search.search("cadmium", []) == []
    assert search.search("   ", ["PRJ-001"]) == []
    assert embedder.calls == []


@needs_db
def test_passages_of_another_embedding_model_are_not_found(session: Session) -> None:
    corpus(session)
    results = PassageSearch(session, FixedEmbedder(1, name="some-other-model")).search(
        "cadmium", ["PRJ-001"]
    )
    assert results == []


def test_the_defaults_are_the_documented_ones() -> None:
    assert RRF_K == 60 and CANDIDATE_DEPTH == 50


@needs_db
def test_asking_for_more_results_than_the_candidate_depth_still_returns_them(
    session: Session,
) -> None:
    add_document(
        session, "ICH-Q3DR2", "PRJ-001", [(f"Passage {n} about cadmium.", n) for n in range(6)]
    )
    fused = hybrid_search(
        session,
        question="cadmium",
        vector=vector(0),
        projects=["PRJ-001"],
        embedding_model=MODEL,
        limit=5,
        depth=2,
    )
    assert len(fused) == 5  # each search still contributes at least `limit` passages


@needs_db
def test_only_the_newest_version_is_searched_by_both_searches_unless_asked(
    session: Session,
) -> None:
    docs = DocumentRepository(session)
    document = docs.register_document("ICH-Q3DR2")
    docs.grant_project_access(document.document_id, "PRJ-001")
    for number, (text, position) in enumerate(
        [("lead in the old revision", 0), ("lead in the new revision", 5)], start=1
    ):
        version = docs.add_version(
            document_id=document.document_id,
            version_number=number,
            version_label=f"Revision {number}",
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            object_key=f"originals/q3d-{number}.pdf",
            byte_size=100,
            retrieved_at=NOW,
        )
        ChunkRepository(session).add_chunks(
            version.version_id,
            [
                ChunkInput(
                    chunk_index=0,
                    page_start=1,
                    page_end=1,
                    text=text,
                    embedding=vector(position),
                    embedding_model=MODEL,
                )
            ],
        )
    # The old revision is the exact match for the vector and contains the keyword; the new one too.
    common = {
        "question": "lead",
        "vector": vector(0),
        "projects": ["PRJ-001"],
        "embedding_model": MODEL,
        "limit": 10,
    }
    newest = hybrid_search(session, **common)
    assert [(f.hit.version_number, f.hit.text) for f in newest] == [(2, "lead in the new revision")]
    everything = hybrid_search(session, latest_only=False, **common)
    assert sorted(f.hit.version_number for f in everything) == [1, 2]
