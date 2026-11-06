#!/usr/bin/env python3
"""Measure how well the whole answer pipeline does on the golden questions (D27).

Plain language: ``retrieval_check.py`` asks "does the search find the right page?". This asks the
next question: given what the search found, does the answer (the quotes the model picked) contain
the right evidence, does it stay silent when it should, and are its quotes really in the passages?
It runs each golden question through the real search, the configured answer writer (for the
language model: LLM_PROVIDER=groq in .env) and the same checks the API applies, and scores the
result with the golden questions' own evidence rules (the same patterns ``retrieval_check.py`` uses).

Who is asked what:
  * answerable asks: a golden question asked in the project that holds its document;
  * negative asks: the same question asked in the OTHER project (where its document cannot be seen),
    and the "gap" questions (the right answer is "not covered") asked in both projects.

Definitions (a question's "evidence pages" are the pages of its document where all its patterns
appear, as in retrieval_check.py):
  * answered       the answer was not withheld.
  * on evidence    at least one verified quote sits on an evidence page of the expected document.
  * contains       every pattern of the question appears in the quotes taken from the expected document
                   (stricter: the quotes themselves hold the answer).
  * correct answer an answerable ask that was answered and has a quote on evidence.
  * answer precision  correct answers / all answers given (answerable and negative asks together).
  * answer recall     correct answers / answerable asks.
  * quote precision   verified quotes that are on evidence / all verified quotes, over answerable asks.
  * verbatim rate     quotes the model offered that were found word for word / quotes it offered.
  * abstention        negative asks that were withheld / negative asks.
Rates come with a 95% Wilson interval, because the sets are small. A call to the model that failed
(for example a rate limit) is asked again after a pause and, if it keeps failing, left out and counted.

What this does NOT show: whether an answer is scientifically right (the evidence rules are patterns
written by the project, not scientist-approved), or how the model behaves on questions outside
this set. With the language model on, each ask sends the question and three public passages to the
provider, so use it only with the public corpus.

Usage (from the repository root, database migrated, documents ingested and indexed):
    python evals/answer_check.py --save evals/results/answers.json
    python evals/answer_check.py --limit 6 --delay 5        # a quick trial
Nothing in the database is changed (the pipeline is called directly: no run is stored or audited).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import retrieval_check as rc
from sqlalchemy.orm import Session

from pharma_ai.core.config import settings
from pharma_ai.features.research.answer_workflow import answer_question
from pharma_ai.features.research.answer_writer import AnswerWriter, normalise
from pharma_ai.features.research.citation_pages import refine_citation_pages
from pharma_ai.features.research.search import PassageSearch
from pharma_ai.features.research.writer_factory import get_answer_writer
from pharma_ai.infrastructure.db.seed import SAMPLE_PROJECT_DOCUMENTS
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory
from pharma_ai.infrastructure.embeddings import EmbedderUnavailable
from pharma_ai.infrastructure.embeddings.local_model import SentenceTransformerEmbedder
from pharma_ai.infrastructure.storage import LocalFolderStore

OWNER = {doc: project for project, docs in SAMPLE_PROJECT_DOCUMENTS.items() for doc in docs}
OTHER = {"PRJ-001": "PRJ-002", "PRJ-002": "PRJ-001"}
FALLBACK_MARK = "could not be used"  # the writer's note when the model failed and word matching ran


# --- the measurements (no database, no model) ---------------------------------------------------


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson interval for a proportion; (0, 0) when there is nothing to count."""
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(successes: int, total: int) -> str:
    if total == 0:
        return "n/a"
    low, high = wilson(successes, total)
    return f"{successes}/{total} = {successes / total:.0%} (95% CI {low:.0%}-{high:.0%})"


@dataclass
class Ask:
    """One question asked in one project, and everything needed to score it."""

    q_id: str
    kind: str  # the golden question's type, e.g. "table value"
    role: str  # "answerable", "cross" or "gap"
    project: str
    question: str
    answered: bool = False
    withheld: bool = True
    retried: bool = False
    model_failed: bool = False
    seconds: float = 0.0
    retrieved: list[str] = field(default_factory=list)  # "DOC pS-E" of the passages the model saw
    retrieval_hit: bool = False  # an expected evidence page is inside the passages the model saw
    offered: int = 0  # quotes the model offered (verified + removed)
    removed: int = 0
    quotes: list[dict[str, Any]] = field(default_factory=list)  # verified: source, pages, text
    on_evidence: bool = False
    contains: bool = False
    relevant_quotes: int = 0
    notes: list[str] = field(default_factory=list)


def quotes_on_evidence(
    quotes: Sequence[dict[str, Any]], evidence: dict[str, set[int]]
) -> tuple[int, bool]:
    """How many quotes sit on an evidence page of an expected document, and whether any do."""
    relevant = sum(
        1
        for q in quotes
        if any(
            q["source"] == doc and any(q["page_start"] <= p <= q["page_end"] for p in pages)
            for doc, pages in evidence.items()
        )
    )
    return relevant, relevant > 0


def quotes_contain(quotes: Sequence[dict[str, Any]], spec: dict[str, list[str]]) -> bool:
    """Every pattern of every expected document appears in that document's quotes together."""
    if not spec:
        return False
    for doc, patterns in spec.items():
        text = rc.norm(" ".join(normalise(q["text"]) for q in quotes if q["source"] == doc))
        if not text or not all(re.search(p, text, re.IGNORECASE) for p in patterns):
            return False
    return True


def summarise(asks: Sequence[Ask]) -> dict[str, Any]:
    """Every number in the report, from the scored asks."""
    usable = [a for a in asks if not a.model_failed]
    pos = [a for a in usable if a.role == "answerable"]
    neg = [a for a in usable if a.role in ("cross", "gap")]
    correct = [a for a in pos if a.answered and a.on_evidence]
    answers_given = [a for a in usable if a.answered]
    verified = sum(len(a.quotes) for a in usable)
    offered = sum(a.offered for a in usable)
    relevant = sum(a.relevant_quotes for a in pos)
    pos_quotes = sum(len(a.quotes) for a in pos)
    seen = [a for a in pos if a.retrieval_hit]
    retried = [a for a in usable if a.retried]
    seconds = sorted(a.seconds for a in usable)
    precision = len(correct) / len(answers_given) if answers_given else 0.0
    recall = len(correct) / len(pos) if pos else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    kinds: dict[str, tuple[int, int]] = {}
    for a in pos:
        got, total = kinds.get(a.kind, (0, 0))
        kinds[a.kind] = (got + (1 if a in correct else 0), total + 1)
    return {
        "asks": len(asks),
        "model_failed": sum(a.model_failed for a in asks),
        "answerable": len(pos),
        "negative": len(neg),
        "answered": sum(a.answered for a in pos),
        "correct": len(correct),
        "on_evidence": sum(a.on_evidence for a in pos),
        "contains": sum(a.contains for a in pos),
        "retrieval_hit": len(seen),
        "correct_given_retrieval": sum(1 for a in seen if a in correct),
        "answered_given_no_retrieval": sum(1 for a in pos if not a.retrieval_hit and a.answered),
        "no_retrieval": sum(1 for a in pos if not a.retrieval_hit),
        "negatives_withheld": sum(1 for a in neg if a.withheld),
        "cross_withheld": sum(1 for a in neg if a.role == "cross" and a.withheld),
        "cross": sum(1 for a in neg if a.role == "cross"),
        "gap_withheld": sum(1 for a in neg if a.role == "gap" and a.withheld),
        "gap": sum(1 for a in neg if a.role == "gap"),
        "answers_given": len(answers_given),
        "answer_precision": precision,
        "answer_recall": recall,
        "answer_f1": f1,
        "quotes_verified": verified,
        "quotes_offered": offered,
        "quotes_relevant": relevant,
        "quotes_on_answerable": pos_quotes,
        "retried": len(retried),
        "retry_recovered": sum(1 for a in retried if a.answered),
        "median_seconds": statistics.median(seconds) if seconds else 0.0,
        "p95_seconds": seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))] if seconds else 0.0,
        "by_kind": kinds,
    }


def report(s: dict[str, Any]) -> str:
    header = (
        f"asks: {s['asks']} ({s['answerable']} answerable, {s['negative']} negative), "
        f"{s['model_failed']} left out because the model could not be reached"
    )
    lines = [
        header,
        "",
        "ANSWERABLE QUESTIONS (asked in the project that holds the document)",
        f"  retrieval: the evidence page is in the 3 passages the model saw : {rate(s['retrieval_hit'], s['answerable'])}",
        f"  answered (not withheld)                                        : {rate(s['answered'], s['answerable'])}",
        f"  a quote is on an evidence page (right place)                   : {rate(s['on_evidence'], s['answerable'])}",
        f"  the quotes contain every pattern (right content, strict)       : {rate(s['contains'], s['answerable'])}",
        f"  correct answers (answered + quote on evidence)                 : {rate(s['correct'], s['answerable'])}",
        f"    when the evidence was in view                                : {rate(s['correct_given_retrieval'], s['retrieval_hit'])}",
        f"    answered although the evidence was NOT in view               : {rate(s['answered_given_no_retrieval'], s['no_retrieval'])}",
        "",
        "NEGATIVE QUESTIONS (the right behaviour is to stay silent)",
        f"  withheld, same question in the other project                   : {rate(s['cross_withheld'], s['cross'])}",
        f"  withheld, gap questions                                        : {rate(s['gap_withheld'], s['gap'])}",
        f"  withheld, all negatives                                        : {rate(s['negatives_withheld'], s['negative'])}",
        "",
        "ANSWERS GIVEN, BOTH KINDS TOGETHER",
        f"  answer precision (correct / all answers given)                 : {rate(s['correct'], s['answers_given'])}",
        f"  answer recall    (correct / answerable)                        : {rate(s['correct'], s['answerable'])}",
        f"  answer F1                                                      : {s['answer_f1']:.2f}",
        "",
        "QUOTES",
        f"  verbatim rate (found word for word / offered by the model)     : {rate(s['quotes_verified'], s['quotes_offered'])}",
        f"  quote precision (on evidence / verified, answerable asks)      : {rate(s['quotes_relevant'], s['quotes_on_answerable'])}",
        "",
        f"RETRY: asked a second time {s['retried']} time(s); the second try gave an answer {s['retry_recovered']} time(s)",
        f"LATENCY per ask: median {s['median_seconds']:.1f}s, 95th percentile {s['p95_seconds']:.1f}s",
        "",
        "CORRECT ANSWERS BY QUESTION TYPE",
    ]
    for kind, (got, total) in sorted(s["by_kind"].items()):
        lines.append(f"  {kind or '(none)':16} {rate(got, total)}")
    return "\n".join(lines)


# --- running the pipeline (database, search, writer) --------------------------------------------


def run_ask(
    ask: Ask,
    spec: dict[str, list[str]],
    evidence: dict[str, set[int]],
    session: Session,
    store: LocalFolderStore,
    search: PassageSearch,
    writer: AnswerWriter,
) -> Ask:
    """Ask the real pipeline one question and score what comes back."""
    started = time.monotonic()
    found = search.search(ask.question, [ask.project], limit=3)
    answer = answer_question(ask.question, found, writer)
    answer = replace(answer, citations=refine_citation_pages(session, store, answer.citations))
    ask.seconds = time.monotonic() - started
    notes = answer.evidence_gaps
    ask.model_failed = writer.mode != "quotes-only" and answer.mode == "quotes-only"
    ask.retried = any("asked a second time" in n for n in notes)
    ask.retrieved = [f"{f.hit.source_id} p{f.hit.page_start}-{f.hit.page_end}" for f in found]
    if evidence:
        passage, _ = rc.score([f.hit for f in found], evidence)
        ask.retrieval_hit = passage[3]
    ask.withheld = answer.withheld
    ask.answered = not answer.withheld
    ask.removed = answer.rejected_quotes
    ask.quotes = [
        {
            "source": c["source_id"],
            "page_start": c["page_start"],
            "page_end": c["page_end"],
            "text": c["quoted_evidence"],
        }
        for c in answer.citations
    ]
    ask.offered = len(ask.quotes) + ask.removed
    ask.notes = [n for n in notes if "None of the retrieved passages" not in n]
    if ask.role == "answerable":
        ask.relevant_quotes, ask.on_evidence = quotes_on_evidence(ask.quotes, evidence)
        ask.contains = quotes_contain(ask.quotes, spec)
    return ask


def plan(
    questions: Sequence[rc.Question],
    session: Session,
    store: LocalFolderStore,
    cross_limit: int | None,
) -> list[tuple[Ask, dict[str, list[str]], dict[str, set[int]]]]:
    """The asks to make: each answerable question at home, the same one in the other project
    (up to ``cross_limit`` of them), and the gap questions in both projects."""
    work: list[tuple[Ask, dict[str, list[str]], dict[str, set[int]]]] = []
    cross: list[tuple[Ask, dict[str, list[str]], dict[str, set[int]]]] = []
    for q in questions:
        if q.kind == "gap" or "ANY" in q.spec:
            for project in OTHER:
                work.append((Ask(q.q_id, q.kind, "gap", project, q.text), {}, {}))
            continue
        pages = {doc: rc.load_pages(session, store, doc) for doc in q.spec}
        if any(p is None for p in pages.values()):
            continue  # a document that is not ingested: nothing to score
        evidence = {doc: rc.matching_pages(p or [], q.spec[doc]) for doc, p in pages.items()}
        owners = {OWNER[d] for d in q.spec if d in OWNER}
        if len(owners) != 1 or any(not e for e in evidence.values()):
            continue  # about two projects' documents, or its own patterns match no page
        home = owners.pop()
        work.append((Ask(q.q_id, q.kind, "answerable", home, q.text), q.spec, evidence))
        cross.append((Ask(q.q_id, q.kind, "cross", OTHER[home], q.text), {}, {}))
    return work + (cross if cross_limit is None else cross[:cross_limit])


def execute(
    work: Sequence[tuple[Ask, dict[str, list[str]], dict[str, set[int]]]],
    session: Session,
    store: LocalFolderStore,
    search: PassageSearch,
    writer: AnswerWriter,
    delay: float,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = print,
) -> list[Ask]:
    done: list[Ask] = []
    for number, (ask, spec, evidence) in enumerate(work, start=1):
        for attempt in range(3):  # a failed model call is retried after a longer pause
            sleep(delay if attempt == 0 else 60)
            ask = run_ask(ask, spec, evidence, session, store, search, writer)
            if not ask.model_failed:
                break
        label = (
            "model unreachable"
            if ask.model_failed
            else ("answered" if ask.answered else "withheld")
        )
        progress(
            f"[{number}/{len(work)}] {ask.q_id} {ask.role:10} {ask.project}  {label:8} {ask.seconds:4.1f}s"
        )
        done.append(ask)
    return done


def main(
    argv: list[str] | None = None,
    *,
    embedder_factory: Callable[..., Any] = SentenceTransformerEmbedder,
) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--questions", type=Path, default=rc.DEFAULT_QUESTIONS)
    ap.add_argument("--limit", type=int, default=None, help="only the first N asks (a quick trial)")
    ap.add_argument("--cross-limit", type=int, default=None, help="at most N other-project asks")
    ap.add_argument("--delay", type=float, default=22.0, help="seconds between model calls")
    ap.add_argument("--save", type=Path, default=None, help="write the per-ask results as JSON")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--storage-root", default=None, help="defaults to STORAGE_ROOT")
    args = ap.parse_args(argv)

    writer = get_answer_writer()
    print(f"answer writer: {writer.mode}")
    try:
        embedder = embedder_factory(
            settings.embedding_model, batch_size=settings.embedding_batch_size
        )
    except EmbedderUnavailable as exc:
        print(f"Cannot load the embedding model: {exc}")
        return 2
    store = LocalFolderStore(args.storage_root or settings.storage_root)
    factory = make_session_factory(make_engine(args.database_url))
    with factory() as session:
        work = plan(rc.load_questions(args.questions), session, store, args.cross_limit)
        if args.limit is not None:
            work = work[: args.limit]
        if not work:
            print("Nothing to ask: are the documents ingested and indexed?")
            return 1
        asks = execute(work, session, store, PassageSearch(session, embedder), writer, args.delay)
    summary = summarise(asks)
    print("\n" + report(summary))
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(
            json.dumps(
                {"writer": writer.mode, "summary": summary, "asks": [a.__dict__ for a in asks]},
                indent=1,
                default=list,
            ),
            encoding="utf-8",
        )
        print(f"\nSaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
