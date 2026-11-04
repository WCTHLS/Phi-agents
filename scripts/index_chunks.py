#!/usr/bin/env python3
"""Cut ingested documents into passages and embed them, so they can be searched (S2-06).

Plain language: ingestion (scripts/ingest_corpus.py) keeps each PDF and reads its text. This script
takes that text, cuts it into passages of at most 500 tokens (a token is a word or a piece of a
word), and turns every passage into 384 numbers with the embedding model. The database can then find
the passages closest in meaning to a question. It uses the same code a future upload step will use.

Usage (from the repository root, after `alembic upgrade head` and the ingest script):
    pip install -e ".[embeddings]"          # once: the embedding model needs sentence-transformers
    python scripts/index_chunks.py
    python scripts/index_chunks.py --only ICH-Q3DR2 FDA-PV-2011
    python scripts/index_chunks.py --force   # cut and embed again (for example after a setting change)
    python scripts/index_chunks.py --dry-run

The first run downloads the model's public weights from Hugging Face (about 130 MB for the default
model) into the local cache. No document text leaves this machine. Indexing the same version again
with the same model changes nothing; passages made by a different model are replaced. Each version
is saved in one database transaction together with an audit event whose actor is
"system:index-script". A version with no selectable text (a scan) is reported and skipped.
Exit code 0 means every version was indexed, was already indexed, or was skipped for lack of text.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select

from pharma_ai.core.config import settings
from pharma_ai.features.documents.indexing import (
    DocumentIndexingService,
    IndexingError,
    IndexingResult,
)
from pharma_ai.infrastructure.db.models import AuditEvent, Chunk, Document, DocumentVersion
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory
from pharma_ai.infrastructure.embeddings import Embedder, EmbedderUnavailable
from pharma_ai.infrastructure.embeddings.local_model import SentenceTransformerEmbedder
from pharma_ai.infrastructure.storage import LocalFolderStore

ACTOR = "system:index-script"


def audit_row(result: IndexingResult) -> AuditEvent:
    """The audit event for one indexed version (saved in the same transaction as its passages)."""
    return AuditEvent(
        actor_id=ACTOR,
        actor_role="system",
        actor_roles=["system"],
        action="DOCUMENT_INDEXED",
        resource_type="document_version",
        resource_id=str(result.version_id),
        resource_version=str(result.version_number),
        outcome="SUCCESS",
        details={
            "source_id": result.source_id,
            "sha256": result.sha256,
            "status": result.status,
            "chunk_count": result.chunk_count,
            "previous_chunk_count": result.previous_chunk_count,
            "embedding_model": result.embedding_model,
            "max_tokens": result.max_tokens,
            "overlap_tokens": result.overlap_tokens,
        },
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
    ap.add_argument("--only", nargs="*", default=[], help="index only these document ids")
    ap.add_argument("--force", action="store_true", help="cut and embed again even if indexed")
    ap.add_argument("--model", default=None, help="defaults to EMBEDDING_MODEL")
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--storage-root", default=None, help="defaults to STORAGE_ROOT")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    args = ap.parse_args(argv)

    engine = make_engine(args.database_url)
    factory = make_session_factory(engine)
    model_name = args.model or settings.embedding_model

    with factory() as session:
        stmt = (
            select(DocumentVersion.version_id, Document.source_id, DocumentVersion.version_number)
            .join(Document, Document.document_id == DocumentVersion.document_id)
            .order_by(Document.source_id, DocumentVersion.version_number)
        )
        if args.only:
            stmt = stmt.where(Document.source_id.in_(args.only))
        versions = [tuple(row) for row in session.execute(stmt)]
        counts = dict(
            session.execute(select(Chunk.version_id, func.count()).group_by(Chunk.version_id)).all()
        )
    if not versions:
        print("No ingested documents match. Run scripts/ingest_corpus.py first.")
        return 1

    if args.dry_run:
        print(f"{'document':20} {'version':>7} {'passages now':>13}")
        for version_id, source_id, number in versions:
            print(f"{source_id:20} {number:>7} {counts.get(version_id, 0):>13}")
        print(f"\nWould use the model {model_name}. Nothing was changed.")
        return 0

    started = time.monotonic()
    try:
        embedder = embedder_factory(model_name, batch_size=settings.embedding_batch_size)
    except EmbedderUnavailable as exc:
        print(f"Cannot load the embedding model: {exc}")
        return 2
    store = LocalFolderStore(args.storage_root or settings.storage_root)
    print(f"model {model_name}, loaded in {time.monotonic() - started:.1f}s\n")
    print(f"{'document':20} {'version':>7} {'result':16} {'passages':>8} {'seconds':>7}  note")

    failures = 0
    for version_id, source_id, number in versions:
        began = time.monotonic()
        with factory() as session:
            try:
                service = DocumentIndexingService(
                    session,
                    store,
                    embedder,
                    max_tokens=settings.chunk_size,
                    overlap_tokens=settings.chunk_overlap,
                )
                result = service.index_version(version_id, force=args.force)
                if result.status in ("indexed", "reindexed"):
                    session.add(audit_row(result))
                session.commit()
            except IndexingError as exc:
                session.rollback()
                failures += 1
                print(f"{source_id:20} {number:>7} {'REFUSED':16} {'':>8} {'':>7}  {exc}")
                continue
        label = result.status.replace("_", " ")
        print(
            f"{source_id:20} {number:>7} {label:16} {result.chunk_count:>8} "
            f"{time.monotonic() - began:>7.1f}  {result.reason or ''}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
