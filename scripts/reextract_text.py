#!/usr/bin/env python3
"""Read the stored originals again with the current extractor and keep the new text (S2-03).

Plain language: ingestion reads each PDF's text once. When the extractor improves (for example,
superscripts such as 10⁻⁵ are now kept instead of reading as 10-5, and bullets drawn with the
Symbol font read "•" instead of showing as boxes), the documents already stored
still hold the old text. This script reads every stored original again and, where the text came out
differently, stores the new text under a new name and points the version at it. The original file
and the old text are never changed or deleted.

Usage (from the repository root):
    python scripts/reextract_text.py --dry-run    # list what would change, change nothing
    python scripts/reextract_text.py
    python scripts/reextract_text.py --only ICH-M7R2 FDA-NITRO-2024

Then cut and embed the passages again from the new text, for the documents it lists:
    python scripts/index_chunks.py --force --only <documents>

Each changed version is saved in one database transaction together with an audit event whose actor
is "system:reextract-script". Exit code 0 means every version was read again (changed or not).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from pharma_ai.core.config import settings
from pharma_ai.features.documents.ingestion import (
    DocumentIngestionService,
    IngestionError,
    ReextractionResult,
)
from pharma_ai.infrastructure.db.models import AuditEvent, Document, DocumentVersion
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory
from pharma_ai.infrastructure.storage import LocalFolderStore

ACTOR = "system:reextract-script"


def audit_row(result: ReextractionResult) -> AuditEvent:
    """The audit event for one re-read version (saved in the same transaction as the change)."""
    return AuditEvent(
        actor_id=ACTOR,
        actor_role="system",
        actor_roles=["system"],
        action="DOCUMENT_TEXT_REEXTRACTED",
        resource_type="document_version",
        resource_id=str(result.version_id),
        resource_version=str(result.version_number),
        outcome="SUCCESS",
        details={
            "source_id": result.source_id,
            "sha256": result.sha256,
            "previous_extracted_key": result.previous_key,
            "extracted_key": result.extracted_key,
            "changed_pages": list(result.changed_pages),
            "extraction_method": result.extraction_method,
            "extraction_status": result.extraction_status,
        },
    )


def _pages(pages: tuple[int, ...]) -> str:
    shown = ", ".join(str(p) for p in pages[:12])
    return shown + (f" and {len(pages) - 12} more" if len(pages) > 12 else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", nargs="*", default=[], help="read only these document ids")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--storage-root", default=None, help="defaults to STORAGE_ROOT")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = ap.parse_args(argv)

    engine = make_engine(args.database_url)
    factory = make_session_factory(engine)
    store = LocalFolderStore(args.storage_root or settings.storage_root)

    with factory() as session:
        stmt = (
            select(DocumentVersion.version_id, Document.source_id, DocumentVersion.version_number)
            .join(Document, Document.document_id == DocumentVersion.document_id)
            .order_by(Document.source_id, DocumentVersion.version_number)
        )
        if args.only:
            stmt = stmt.where(Document.source_id.in_(args.only))
        versions = [tuple(row) for row in session.execute(stmt)]
    if not versions:
        print("No ingested documents match. Run scripts/ingest_corpus.py first.")
        return 1

    print(f"{'document':20} {'version':>7} {'result':10}  pages whose text changed")
    failures = 0
    changed_documents: list[str] = []
    for version_id, source_id, number in versions:
        with factory() as session:
            try:
                service = DocumentIngestionService(
                    session, store, max_bytes=settings.max_upload_mb * 1024**2
                )
                result = service.reextract(version_id, dry_run=args.dry_run)
                if result.changed and not args.dry_run:
                    session.add(audit_row(result))
                    session.commit()
                else:
                    session.rollback()
            except IngestionError as exc:
                session.rollback()
                failures += 1
                print(f"{source_id:20} {number:>7} {'REFUSED':10}  {exc}")
                continue
        if result.changed:
            changed_documents.append(source_id)
            label = "would change" if args.dry_run else "changed"
        else:
            label = "unchanged"
        print(f"{source_id:20} {number:>7} {label:10}  {_pages(result.changed_pages)}")

    if args.dry_run:
        print("\nDry run: nothing was stored or changed.")
    elif changed_documents:
        print(
            "\nThe passages still hold the old text. Cut and embed them again:\n"
            f"    python scripts/index_chunks.py --force --only {' '.join(changed_documents)}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
