"""Tests for evals/retrieval_check.py: the scoring rules, and a full run on a tiny indexed corpus.

The scoring tests need no database. The run tests use a real PostgreSQL + pgvector database and a
stand-in embedding model (see test_indexing.py); the script only reads, so each test is rolled back.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from fake_embedder import FakeEmbedder
from pdf_factory import make_pdf
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_db_integration import _alembic_config

from pharma_ai.features.documents.indexing import DocumentIndexingService
from pharma_ai.features.documents.ingestion import DocumentIngestionService
from pharma_ai.infrastructure.db.repositories import ChunkHit
from pharma_ai.infrastructure.db.seed import seed_all
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url
from pharma_ai.infrastructure.storage import LocalFolderStore

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "retrieval_check", REPO_ROOT / "evals" / "retrieval_check.py"
)
script = importlib.util.module_from_spec(_spec)
sys.modules["retrieval_check"] = script  # dataclasses look the module up by name
_spec.loader.exec_module(script)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)


def hit(source: str, start: int, end: int | None = None, distance: float = 0.1) -> ChunkHit:
    return ChunkHit(
        chunk_id=uuid.uuid4(),
        source_id=source,
        version_id=uuid.uuid4(),
        version_label=None,
        version_number=1,
        sha256="0" * 64,
        page_start=start,
        page_end=end or start,
        section=None,
        text="x",
        attribution=None,
        distance=distance,
    )


# --- scoring (no database) --------------------------------------------------------------------


def test_a_passage_on_an_evidence_page_scores_at_every_cutoff_it_reaches() -> None:
    hits = [hit("A", 9), hit("A", 7), hit("B", 2), hit("A", 4), hit("A", 1)]
    passage, document = script.score(hits, {"A": {4}})
    assert passage == {1: False, 3: False, 5: True}
    assert document == {1: True, 3: True, 5: True}


def test_a_page_range_counts_if_it_includes_an_evidence_page() -> None:
    passage, _ = script.score([hit("A", 3, 6)], {"A": {5}})
    assert passage[1] is True
    passage, _ = script.score([hit("A", 3, 4)], {"A": {5}})
    assert passage[1] is False


def test_the_right_page_of_the_wrong_document_does_not_count() -> None:
    passage, document = script.score([hit("B", 5)], {"A": {5}})
    assert passage[5] is False and document[5] is False


def test_a_question_naming_two_documents_needs_both() -> None:
    evidence = {"A": {1}, "B": {2}}
    assert script.score([hit("A", 1)], evidence)[0][5] is False
    assert script.score([hit("A", 1), hit("B", 2)], evidence)[0] == {1: False, 3: True, 5: True}
    # The right documents on the wrong pages count for documents only.
    passage, document = script.score([hit("A", 8), hit("B", 8)], evidence)
    assert passage[5] is False and document[5] is True
    # One document alone is not enough, for passages or for documents.
    assert script.score([hit("A", 1)], evidence)[1][5] is False


def test_no_results_scores_nothing() -> None:
    assert script.score([], {"A": {1}}) == (
        {1: False, 3: False, 5: False},
        {1: False, 3: False, 5: False},
    )


def test_pages_match_only_when_every_pattern_is_on_the_page() -> None:
    pages = [
        {"page": 1, "text": "Permitted   daily exposure for Lead"},
        {"page": 2, "text": "Permitted daily exposure only"},
        {"page": 3, "text": "lead only"},
    ]
    assert script.matching_pages(pages, ["permitted daily exposure", r"\blead\b"]) == {1}
    assert script.matching_pages(pages, ["nothing like this"]) == set()


def test_the_real_golden_questions_load() -> None:
    questions = script.load_questions(script.DEFAULT_QUESTIONS)
    assert len(questions) == 33
    assert any(q.kind == "gap" for q in questions)
    assert all(q.spec for q in questions)


# --- a full run on a tiny corpus ----------------------------------------------------------------

PAGES = {
    "ICH-Q3DR2": [
        "1. INTRODUCTION\n\nCadmium has an oral permitted daily exposure of five micrograms per day.",
        "2. SCOPE\n\nThe guideline covers new drug products containing purified proteins.",
    ],
    "FDA-PV-2011": [
        "I. INTRODUCTION\n\nProcess validation has three stages: design, qualification and verification.",
    ],
}
QUESTIONS = """q_id,question,type,expected_behaviour,evidence_spec,draft_answer_from_memory,required_caveat,review_status,scientist_review
GQ-A,What is the oral exposure of cadmium per day,table value,answer,ICH-Q3DR2::cadmium&&permitted daily exposure,,,draft,
GQ-B,How many stages does process validation have,definition,answer,FDA-PV-2011::three stages,,,draft,
GQ-C,Which documents cover exposure and validation,code trap,answer,ICH-Q3DR2::permitted daily exposure;;FDA-PV-2011::three stages,,,draft,
GQ-D,A question whose patterns match nothing,table value,answer,ICH-Q3DR2::no such words anywhere,,,draft,
GQ-E,A question about a document that is not ingested,definition,answer,ICH-M9::highly soluble,,,draft,
GQ-F,What is the zone IV storage condition,gap,not covered,ANY::zone IV,,,draft,
"""


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
        ingestion = DocumentIngestionService(session, store, max_bytes=5_000_000)
        indexing = DocumentIndexingService(
            session,
            store,
            FakeEmbedder(script.settings.embedding_model),
            max_tokens=20,
            overlap_tokens=0,
        )
        for source_id, pages in PAGES.items():
            result = ingestion.ingest(
                source_id=source_id, data=make_pdf(pages), project_ids=["PRJ-001"]
            )
            indexing.index_version(result.version_id)
        session.commit()
    yield eng
    eng.dispose()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
def questions_file(tmp_path: Path) -> Path:
    path = tmp_path / "questions.csv"
    path.write_text(QUESTIONS, encoding="utf-8")
    return path


def run(
    tmp_path: Path, questions_file: Path, *extra: str, embedder: FakeEmbedder | None = None
) -> tuple[int, str]:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = script.main(
            [
                "--questions",
                str(questions_file),
                "--storage-root",
                str(tmp_path / "files"),
                "--database-url",
                TEST_DATABASE_URL,
                *extra,
            ],
            embedder_factory=lambda name, **kw: embedder or FakeEmbedder(name),
        )
    return code, out.getvalue()


@needs_db
def test_a_run_scores_what_can_be_scored_and_lists_the_rest(
    engine: Engine, tmp_path: Path, questions_file: Path
) -> None:
    code, out = run(tmp_path, questions_file)
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("GQ-")}

    assert code == 0
    assert "Y" in lines["GQ-A"] and "ICH-Q3DR2 p1-1" in lines["GQ-A"]
    assert "FDA-PV-2011" in lines["GQ-B"]
    assert "UNSCOREABLE" in lines["GQ-D"] and "ICH-Q3DR2" in lines["GQ-D"]
    assert "SKIPPED" in lines["GQ-E"] and "not ingested: ICH-M9" in lines["GQ-E"]
    assert "SKIPPED" in lines["GQ-F"] and "gap question" in lines["GQ-F"]
    assert "Scored 3 question(s); 1 unscoreable, 2 skipped" in out


@needs_db
def test_the_search_can_be_wrong_and_the_report_says_so(
    engine: Engine, tmp_path: Path, questions_file: Path
) -> None:
    # Ask the cadmium question but name the process-validation document as the expected one.
    questions_file.write_text(
        QUESTIONS.replace(
            "ICH-Q3DR2::cadmium&&permitted daily exposure", "FDA-PV-2011::three stages", 1
        ),
        encoding="utf-8",
    )
    saved = tmp_path / "out" / "run.json"
    run(tmp_path, questions_file, "--save", str(saved))
    result = json.loads(saved.read_text(encoding="utf-8"))
    assert result["questions"]["GQ-A"]["passage"]["1"] is False
    assert result["questions"]["GQ-A"]["passage"]["5"] is True  # it is still found, just not first


@needs_db
def test_results_are_saved_and_a_later_run_is_compared_with_them(
    engine: Engine, tmp_path: Path, questions_file: Path
) -> None:
    saved = tmp_path / "results" / "baseline.json"
    run(tmp_path, questions_file, "--save", str(saved))
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["settings"]["retriever"] == "dense" and data["summary"]["scored"] == 3
    assert data["settings"]["model"] == script.settings.embedding_model
    assert data["summary"]["unscoreable"] == ["GQ-D"] and data["summary"]["skipped"] == [
        "GQ-E",
        "GQ-F",
    ]

    _, same = run(tmp_path, questions_file, "--compare", str(saved))
    assert "no question changed" in same

    # Pretend the earlier run missed GQ-A at rank 1: the new run should report it as gained.
    data["questions"]["GQ-A"]["passage"]["1"] = False
    data["summary"]["passage"]["1"] -= 1
    saved.write_text(json.dumps(data), encoding="utf-8")
    _, changed = run(tmp_path, questions_file, "--compare", str(saved))
    assert "GQ-A: top 1 GAINED" in changed


@needs_db
def test_a_model_that_cannot_be_loaded_is_reported(
    engine: Engine, tmp_path: Path, questions_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from pharma_ai.infrastructure.embeddings import EmbedderUnavailable

    def broken(name: str, **kw: object) -> None:
        raise EmbedderUnavailable("sentence-transformers is not installed")

    code = script.main(
        [
            "--questions",
            str(questions_file),
            "--storage-root",
            str(tmp_path / "files"),
            "--database-url",
            TEST_DATABASE_URL,
        ],
        embedder_factory=broken,
    )
    assert code == 2 and "Cannot load the embedding model" in capsys.readouterr().out


@needs_db
def test_the_query_prefix_goes_in_front_of_each_question_and_is_saved(
    engine: Engine, tmp_path: Path, questions_file: Path
) -> None:
    embedder = FakeEmbedder(script.settings.embedding_model)
    saved = tmp_path / "run.json"
    run(
        tmp_path,
        questions_file,
        "--query-prefix",
        "Find: ",
        "--save",
        str(saved),
        embedder=embedder,
    )
    assert embedder.calls and all(text.startswith("Find: ") for text in embedder.calls[0])
    assert len(embedder.calls[0]) == 3  # only the scoreable questions are embedded
    assert json.loads(saved.read_text(encoding="utf-8"))["settings"]["query_prefix"] == "Find: "


@needs_db
def test_evidence_pages_come_from_the_newest_version_of_a_document(
    engine: Engine, tmp_path: Path
) -> None:
    store = LocalFolderStore(tmp_path / "files")
    with Session(engine) as session:
        before = script.load_pages(session, store, "ICH-Q3DR2")
        assert before is not None and len(before) == 2
        newer = DocumentIngestionService(session, store, max_bytes=5_000_000).ingest(
            source_id="ICH-Q3DR2", data=make_pdf(["Revised text", "More", "Third page"])
        )
        session.commit()
        after = script.load_pages(session, store, "ICH-Q3DR2")
    assert newer.version_number == 2
    assert after is not None and len(after) == 3 and "Revised text" in after[0]["text"]
    with Session(engine) as session:
        assert script.load_pages(session, store, "ICH-M9") is None  # never ingested


@needs_db
def test_passages_made_by_another_model_are_not_searched_and_the_report_says_why(
    engine: Engine, tmp_path: Path, questions_file: Path
) -> None:
    code, out = run(tmp_path, questions_file, embedder=FakeEmbedder("some-other-model"))
    assert code == 0
    assert "no results at all" in out.lower()
    assert "some-other-model" in out
    assert "right passage -> top 1: 0/3" in out
