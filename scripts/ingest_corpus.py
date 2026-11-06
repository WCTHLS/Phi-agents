#!/usr/bin/env python3
"""Ingest the corpus PDFs listed in data/sources/corpus_manifest.csv (S2-03).

Plain language: for each approved document this script keeps an unchangeable copy of the PDF,
records its fingerprint (SHA-256), reads the text page by page, and writes the database rows. It
uses the same code as the upload endpoint, so the same rules apply: a document that is not
*eligible* in the manifest (ADR-002) is never ingested, and running the script twice changes
nothing the second time.

Usage (from the repository root, after `alembic upgrade head` and the seed script):
    python scripts/ingest_corpus.py
    python scripts/ingest_corpus.py --only ICH-Q3DR2 FDA-PV-2011 --project PRJ-001
    python scripts/ingest_corpus.py --dry-run

A document is only searched for a project it is linked to. Without --project nothing is linked;
scripts/assign_documents.py then divides the documents between the sample projects.

The PDFs are expected at data/corpus/originals/<doc_id>.pdf (see scripts/fetch_corpus.py).
Originals and extracted text go to STORAGE_ROOT (default data/storage). Each file is saved in one
database transaction together with an audit event whose actor is "system:ingest-script".
Exit code 0 means every document that was attempted was ingested or was already there.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma_ai.core.config import settings
from pharma_ai.features.documents.ingestion import (
    DocumentIngestionService,
    IngestionError,
    IngestionResult,
)
from pharma_ai.infrastructure.db.models import AuditEvent
from pharma_ai.infrastructure.db.seed import DEFAULT_MANIFEST
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory
from pharma_ai.infrastructure.storage import LocalFolderStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORIGINALS = REPO_ROOT / "data" / "corpus" / "originals"
ACTOR = "system:ingest-script"


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def audit_row(result: IngestionResult, projects: Sequence[str]) -> AuditEvent:
    """The audit event for one ingested file (saved in the same transaction as the document)."""
    return AuditEvent(
        actor_id=ACTOR,
        actor_role="system",
        actor_roles=["system"],
        action="DOCUMENT_ALREADY_INGESTED" if result.already_ingested else "DOCUMENT_INGESTED",
        resource_type="document_version",
        resource_id=str(result.version_id),
        resource_version=str(result.version_number),
        project_id=projects[0] if len(projects) == 1 else None,
        outcome="SUCCESS",
        details={
            "source_id": result.source_id,
            "sha256": result.sha256,
            "byte_size": result.byte_size,
            "page_count": result.page_count,
            "text_layer": result.text_layer,
            "extraction_status": result.extraction_status,
            "extraction_method": result.extraction_method,
            "project_ids": list(projects),
        },
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--originals", type=Path, default=DEFAULT_ORIGINALS, help="folder of PDFs")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--project", action="append", default=[], help="project that may search them")
    ap.add_argument("--only", nargs="*", default=[], help="ingest only these document ids")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--storage-root", default=None, help="defaults to STORAGE_ROOT")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    args = ap.parse_args(argv)

    rows = [r for r in read_manifest(args.manifest) if not args.only or r["doc_id"] in args.only]
    if not rows:
        print("No matching documents in the manifest.")
        return 1

    engine = make_engine(args.database_url)
    factory = make_session_factory(engine)
    store = LocalFolderStore(args.storage_root or settings.storage_root)
    max_bytes = settings.max_upload_mb * 1024**2
    failures = 0
    print(f"{'document':20} {'result':10} {'pages':>5} {'text':>7} {'extraction':>10}  note")
    for row in rows:
        doc_id = row["doc_id"]
        pdf = args.originals / f"{doc_id}.pdf"
        if row.get("eligible", "yes") != "yes":
            print(f"{doc_id:20} {'held':10} {'':>5} {'':>7} {'':>10}  not eligible: not ingested")
            continue
        if not pdf.exists():
            print(f"{doc_id:20} {'missing':10} {'':>5} {'':>7} {'':>10}  no file at {pdf}")
            continue
        if args.dry_run:
            print(
                f"{doc_id:20} {'would add':10} {'':>5} {'':>7} {'':>10}  {pdf.stat().st_size} bytes"
            )
            continue
        with factory() as session:
            try:
                result = DocumentIngestionService(session, store, max_bytes=max_bytes).ingest(
                    source_id=doc_id, data=pdf.read_bytes(), project_ids=args.project
                )
                session.add(audit_row(result, args.project))
                session.commit()
            except IngestionError as exc:
                session.rollback()
                failures += 1
                print(f"{doc_id:20} {'REFUSED':10} {'':>5} {'':>7} {'':>10}  {exc}")
                continue
        label = "already" if result.already_ingested else f"v{result.version_number} added"
        note = "; ".join(result.extraction_notes)
        print(
            f"{doc_id:20} {label:10} {result.page_count or 0:5} {result.text_layer or '-':>7} "
            f"{result.extraction_status or '-':>10}  {note}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
