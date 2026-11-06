#!/usr/bin/env python3
"""Divide the ingested documents between the sample projects (decision log D22).

Plain language: a project can only search the documents linked to it. This script links each
document to the project that should use it and removes the link from the other, following
``SAMPLE_PROJECT_DOCUMENTS`` in ``src/pharma_ai/infrastructure/db/seed.py``. PRJ-001 (nitrosamine
risk) gets the impurity and risk documents; PRJ-002 (bioavailability) gets the bioavailability,
dissolution and process documents. After it runs, a question in one project is never answered
from the other's documents. The passages themselves are not touched, so nothing needs indexing.

Usage (from the repository root, after `alembic upgrade head`, the seed script and ingestion):
    python scripts/assign_documents.py --dry-run     # list the changes, change nothing
    python scripts/assign_documents.py

Only the documents named in the assignment are touched. Each change is saved in one database
transaction together with an audit event (DOCUMENT_PROJECT_ACCESS_GRANTED or
DOCUMENT_PROJECT_ACCESS_REVOKED) whose actor is "system:assign-script". Running it again changes
nothing. Exit code 0 means the links match the assignment (documents not yet ingested are listed
and skipped).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma_ai.features.documents.project_access import (
    AccessChange,
    AssignmentError,
    apply_changes,
    plan_assignment,
)
from pharma_ai.infrastructure.db.models import AuditEvent
from pharma_ai.infrastructure.db.seed import SAMPLE_PROJECT_DOCUMENTS
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory

ACTOR = "system:assign-script"


def audit_row(change: AccessChange) -> AuditEvent:
    """The audit event for one changed link (saved in the same transaction as the change)."""
    granted = change.action == "grant"
    return AuditEvent(
        actor_id=ACTOR,
        actor_role="system",
        actor_roles=["system"],
        action="DOCUMENT_PROJECT_ACCESS_GRANTED" if granted else "DOCUMENT_PROJECT_ACCESS_REVOKED",
        resource_type="document",
        resource_id=str(change.document_id),
        project_id=change.project_id,
        outcome="SUCCESS",
        details={"source_id": change.source_id, "project_id": change.project_id},
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    ap.add_argument("--dry-run", action="store_true", help="show the changes, change nothing")
    args = ap.parse_args(argv)

    factory = make_session_factory(make_engine(args.database_url))
    with factory() as session:
        try:
            plan = plan_assignment(session, SAMPLE_PROJECT_DOCUMENTS)
        except AssignmentError as exc:
            print(f"Cannot apply the assignment: {exc}")
            return 1
        if not args.dry_run and plan.changes:
            apply_changes(session, plan.changes)
            session.add_all(audit_row(change) for change in plan.changes)
            session.commit()

    if plan.changes:
        label = {"grant": "add", "revoke": "remove"}
        print(f"{'document':20} {'project':8} change")
        for change in plan.changes:
            print(f"{change.source_id:20} {change.project_id:8} {label[change.action]}")
        count = len(plan.changes)
        if args.dry_run:
            print(f"\n{count} link(s) would change. Nothing was changed.")
        else:
            print(f"\n{count} link(s) changed.")
    else:
        print("The links already match the assignment. Nothing to do.")
    if plan.not_ingested:
        print(f"Not ingested yet, skipped: {', '.join(plan.not_ingested)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
