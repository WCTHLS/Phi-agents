#!/usr/bin/env python3
"""Measure how well search finds the evidence for the golden questions (S2-06 baseline, S2-07).

Plain language: each golden question names the document and the words that must appear on the page
that answers it (the same rule as evals/verify_questions.py). This script embeds every question, runs
the search that the application uses, and checks whether the returned passages come from the right
document and cover the right page. It needs documents that are already ingested and indexed
(scripts/ingest_corpus.py, then scripts/index_chunks.py).

What it measures, and what it does not:
  * "passage hit@k": among the top k results there is a passage from the expected document whose
    page range includes a page where all the question's patterns appear. A question that names two
    documents needs a hit for each.
  * "document hit@k": the expected document(s) appear among the top k, on any page.
  * It does NOT say whether an answer would be scientifically right, and it is only as good as the
    questions. The draft golden questions are machine-checked, not scientist-approved, and 16
    questions is too few to tell two close methods apart: a change of one question is noise.
  * Questions whose own patterns match no page ("unscoreable"), questions about documents that are not
    ingested ("skipped") and gap questions (the right answer is "not covered") are listed, not scored.

Usage (from the repository root):
    python evals/retrieval_check.py --project PRJ-001
    python evals/retrieval_check.py --save evals/results/baseline.json
    python evals/retrieval_check.py --compare evals/results/baseline.json
    python evals/retrieval_check.py --query-prefix "Represent this sentence for searching relevant passages: "

--save writes the per-question results as JSON; --compare prints which questions gained or lost a hit
against an earlier saved run, so a change to chunking, models or search can be judged before and after.
Exit code 0 unless the database or model cannot be used. Nothing in the database is changed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from sqlalchemy import select
from sqlalchemy.orm import Session
from verify_questions import norm, parse_spec

from pharma_ai.core.config import settings
from pharma_ai.infrastructure.db.models import Document, DocumentVersion
from pharma_ai.infrastructure.db.repositories import ChunkHit, ChunkRepository
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory
from pharma_ai.infrastructure.embeddings import Embedder, EmbedderUnavailable
from pharma_ai.infrastructure.embeddings.local_model import SentenceTransformerEmbedder
from pharma_ai.infrastructure.storage import LocalFolderStore, ObjectNotFoundError

CUTOFFS = (1, 3, 5)
DEFAULT_QUESTIONS = HERE / "golden_questions.csv"

# A retriever takes (session, question vector, project ids, how many) and returns ranked hits.
# S2-07 adds its hybrid search here, so it can be measured the same way as the plain vector search.
Retriever = Callable[[Session, Sequence[float], Sequence[str], int], list[ChunkHit]]


def dense(
    session: Session, vector: Sequence[float], projects: Sequence[str], limit: int
) -> list[ChunkHit]:
    return ChunkRepository(session).search_nearest(vector, projects, limit=limit)


RETRIEVERS: dict[str, Retriever] = {"dense": dense}


@dataclass(frozen=True)
class Question:
    q_id: str
    text: str
    kind: str
    spec: dict[str, list[str]]  # document id -> patterns that must all appear on one page


@dataclass
class Outcome:
    q_id: str
    status: str  # "scored", "unscoreable" or "skipped"
    reason: str = ""
    passage: dict[int, bool] = field(default_factory=dict)  # cutoff -> hit
    document: dict[int, bool] = field(default_factory=dict)
    best: str = ""  # the top result, for reading

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "passage": {str(k): v for k, v in self.passage.items()},
            "document": {str(k): v for k, v in self.document.items()},
            "best": self.best,
        }


def load_questions(path: Path) -> list[Question]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        Question(
            q_id=row["q_id"],
            text=row["question"],
            kind=row.get("type", ""),
            spec={doc: patterns for doc, patterns in parse_spec(row["evidence_spec"])},
        )
        for row in rows
    ]


def matching_pages(pages: Sequence[dict[str, Any]], patterns: Sequence[str]) -> set[int]:
    """Pages of one document where every pattern appears (same rule as verify_questions.py)."""
    import re

    return {
        int(page["page"])
        for page in pages
        if all(re.search(p, norm(page["text"]), re.IGNORECASE) for p in patterns)
    }


def score(
    hits: Sequence[ChunkHit], evidence: dict[str, set[int]]
) -> tuple[dict[int, bool], dict[int, bool]]:
    """Passage and document hits at each cutoff. Every expected document must be satisfied."""
    passage, document = {}, {}
    for cutoff in CUTOFFS:
        top = hits[:cutoff]
        document[cutoff] = all(any(h.source_id == doc for h in top) for doc in evidence)
        passage[cutoff] = all(
            any(
                h.source_id == doc and any(h.page_start <= p <= h.page_end for p in pages)
                for h in top
            )
            for doc, pages in evidence.items()
        )
    return passage, document


def describe(hit: ChunkHit | None) -> str:
    if hit is None:
        return "(no result)"
    return f"{hit.source_id} p{hit.page_start}-{hit.page_end} d={hit.distance:.3f}"


def load_pages(
    session: Session, store: LocalFolderStore, source_id: str
) -> list[dict[str, Any]] | None:
    """Stored page text of the newest version of a document, or None if it is not ingested."""
    key = session.scalar(
        select(DocumentVersion.extracted_key)
        .join(Document, Document.document_id == DocumentVersion.document_id)
        .where(Document.source_id == source_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    if not key:
        return None
    try:
        return json.loads(store.get(key))["pages"]
    except ObjectNotFoundError:
        return None


def evaluate(
    questions: Sequence[Question],
    session: Session,
    store: LocalFolderStore,
    embedder: Embedder,
    retriever: Retriever,
    projects: Sequence[str],
    query_prefix: str = "",
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    to_search: list[tuple[Question, dict[str, set[int]]]] = []
    for q in questions:
        if q.kind == "gap" or "ANY" in q.spec:
            outcomes.append(
                Outcome(q.q_id, "skipped", "gap question: the right answer is 'not covered'")
            )
            continue
        pages_by_doc = {doc: load_pages(session, store, doc) for doc in q.spec}
        missing = [doc for doc, pages in pages_by_doc.items() if pages is None]
        if missing:
            outcomes.append(Outcome(q.q_id, "skipped", f"not ingested: {', '.join(missing)}"))
            continue
        evidence = {
            doc: matching_pages(pages or [], q.spec[doc]) for doc, pages in pages_by_doc.items()
        }
        empty = [doc for doc, pages in evidence.items() if not pages]
        if empty:
            outcomes.append(
                Outcome(
                    q.q_id,
                    "unscoreable",
                    f"the question's own patterns match no page in {', '.join(empty)}",
                )
            )
            continue
        to_search.append((q, evidence))

    vectors = embedder.embed([query_prefix + q.text for q, _ in to_search])
    for (q, evidence), vector in zip(to_search, vectors, strict=True):
        hits = retriever(session, vector, projects, max(CUTOFFS))
        passage, document = score(hits, evidence)
        outcomes.append(
            Outcome(
                q.q_id,
                "scored",
                passage=passage,
                document=document,
                best=describe(hits[0] if hits else None),
            )
        )
    order = {q.q_id: i for i, q in enumerate(questions)}
    return sorted(outcomes, key=lambda o: order[o.q_id])


def flags(values: dict[int, bool]) -> str:
    return " ".join(("Y" if values.get(c) else "-") for c in CUTOFFS)


def summarise(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    scored = [o for o in outcomes if o.status == "scored"]
    return {
        "scored": len(scored),
        "unscoreable": [o.q_id for o in outcomes if o.status == "unscoreable"],
        "skipped": [o.q_id for o in outcomes if o.status == "skipped"],
        "passage": {str(c): sum(o.passage[c] for o in scored) for c in CUTOFFS},
        "document": {str(c): sum(o.document[c] for o in scored) for c in CUTOFFS},
    }


def print_report(outcomes: Sequence[Outcome]) -> None:
    print(f"{'question':9} {'passage 1 3 5':14} {'document 1 3 5':15} best result / note")
    for o in outcomes:
        if o.status == "scored":
            print(f"{o.q_id:9} {flags(o.passage):14} {flags(o.document):15} {o.best}")
        else:
            print(f"{o.q_id:9} {o.status.upper():30} {o.reason}")
    s = summarise(outcomes)
    n = s["scored"]
    print(
        f"\nScored {n} question(s); {len(s['unscoreable'])} unscoreable, {len(s['skipped'])} skipped (not in the totals)."
    )
    for label, key in (("passage", "passage"), ("document", "document")):
        counts = ", ".join(f"top {c}: {s[key][str(c)]}/{n}" for c in CUTOFFS)
        print(f"  right {label} -> {counts}")


def print_comparison(before: dict[str, Any], outcomes: Sequence[Outcome]) -> None:
    print("\nCompared with the saved run (passage hit@5 and @1):")
    changed = False
    for o in outcomes:
        old = before.get("questions", {}).get(o.q_id)
        if not old or old["status"] != "scored" or o.status != "scored":
            continue
        for cutoff in (5, 1):
            was, now = old["passage"].get(str(cutoff)), o.passage.get(cutoff)
            if was != now:
                changed = True
                print(f"  {o.q_id}: top {cutoff} {'GAINED' if now else 'LOST'}")
    if not changed:
        print("  no question changed")
    old_summary, new_summary = before.get("summary", {}), summarise(outcomes)
    for cutoff in (1, 5):
        print(
            f"  passage top {cutoff}: {old_summary.get('passage', {}).get(str(cutoff), '?')} -> "
            f"{new_summary['passage'][str(cutoff)]}  (of {new_summary['scored']})"
        )


def main(
    argv: list[str] | None = None,
    *,
    embedder_factory: Callable[..., Embedder] = SentenceTransformerEmbedder,
) -> int:
    """``embedder_factory`` is for tests, which pass a stand-in so no model is downloaded."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument(
        "--project", action="append", default=[], help="project searched (default PRJ-001)"
    )
    ap.add_argument("--retriever", choices=sorted(RETRIEVERS), default="dense")
    ap.add_argument("--model", default=None, help="defaults to EMBEDDING_MODEL")
    ap.add_argument("--query-prefix", default="", help="text put in front of each question")
    ap.add_argument("--save", type=Path, default=None, help="write the results to this JSON file")
    ap.add_argument("--compare", type=Path, default=None, help="compare with a saved JSON file")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--storage-root", default=None, help="defaults to STORAGE_ROOT")
    args = ap.parse_args(argv)

    projects = args.project or ["PRJ-001"]
    model_name = args.model or settings.embedding_model
    questions = load_questions(args.questions)
    try:
        embedder = embedder_factory(model_name, batch_size=settings.embedding_batch_size)
    except EmbedderUnavailable as exc:
        print(f"Cannot load the embedding model: {exc}")
        return 2
    store = LocalFolderStore(args.storage_root or settings.storage_root)
    factory = make_session_factory(make_engine(args.database_url))
    with factory() as session:
        outcomes = evaluate(
            questions,
            session,
            store,
            embedder,
            RETRIEVERS[args.retriever],
            projects,
            args.query_prefix,
        )

    print(
        f"model {model_name}, retriever {args.retriever}, projects {', '.join(projects)}, "
        f"query prefix {args.query_prefix!r}\n"
    )
    print_report(outcomes)
    if args.compare:
        print_comparison(json.loads(args.compare.read_text(encoding="utf-8")), outcomes)
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "settings": {
                "model": model_name,
                "retriever": args.retriever,
                "projects": projects,
                "query_prefix": args.query_prefix,
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
            },
            "summary": summarise(outcomes),
            "questions": {o.q_id: o.to_json() for o in outcomes},
        }
        args.save.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nSaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
