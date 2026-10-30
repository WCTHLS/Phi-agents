"""Load development data into the database.

The approved-source register (``sources``) is filled from ``data/sources/corpus_manifest.csv``,
so the database and the manifest cannot disagree about which documents are eligible (ADR-002).
No document hashes are seeded: a real SHA-256 exists only once a file has been ingested.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.models import ROLE_KEYS, AuditEvent, Project, Role, Source

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MANIFEST = REPO_ROOT / "data" / "sources" / "corpus_manifest.csv"

ROLE_DESCRIPTIONS = {
    "scientist": "Asks questions, reviews evidence and verifies extracted values",
    "reviewer": "Reviews and approves drafts and study plans",
    "qa": "Quality assurance reviewer",
    "admin": "Manages access and can read the audit log",
}

# Sample projects used by the API's stand-in data and the UI demo.
SAMPLE_PROJECTS = (
    {
        "project_id": "PRJ-001",
        "name": "Metformin ER Nitrosamine Risk Mitigation",
        "therapeutic_area": "Metabolic Disorders",
        "dosage_form": "Extended-Release Tablet",
    },
    {
        "project_id": "PRJ-002",
        "name": "BCS Class II Oral Bioavailability Enhancement",
        "therapeutic_area": "Oncology",
        "dosage_form": "Immediate-Release Capsule",
    },
)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def read_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    """Read the manifest and map each row to the columns of the ``sources`` table.

    ``eligible`` is true only for the exact value ``yes`` (anything else, including a blank or a
    misspelling, counts as not eligible, as ADR-002 requires).
    """
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            use_cases = [u.strip() for u in (r.get("use_cases") or "").split(";") if u.strip()]
            rows.append(
                {
                    "source_id": r["doc_id"].strip(),
                    "title": r["title"].strip(),
                    "authority": r["authority"].strip(),
                    "doc_type": _clean(r.get("doc_type")),
                    "version_label": _clean(r.get("version")),
                    "issued": _clean(r.get("issued")),
                    "status_note": _clean(r.get("status")),
                    "use_cases": use_cases,
                    "licence_status": r["licence_status"].strip(),
                    "reuse_basis": _clean(r.get("reuse_basis")),
                    "attribution": _clean(r.get("attribution")),
                    "eligible": (r.get("eligible") or "").strip().lower() == "yes",
                    "hold_reason": _clean(r.get("hold_reason")),
                    "landing_page": _clean(r.get("landing_page")),
                    "pdf_url": _clean(r.get("pdf_url")),
                    "notes": _clean(r.get("flags")),
                    "tier": r["tier"].strip(),
                }
            )
    return rows


def seed_roles(session: Session) -> None:
    for key in ROLE_KEYS:
        stmt = insert(Role).values(role_key=key, description=ROLE_DESCRIPTIONS[key])
        session.execute(stmt.on_conflict_do_nothing(index_elements=["role_key"]))


def seed_projects(session: Session) -> None:
    for project in SAMPLE_PROJECTS:
        session.execute(
            insert(Project).values(**project).on_conflict_do_nothing(index_elements=["project_id"])
        )


def seed_sources(session: Session, rows: list[dict[str, Any]]) -> int:
    """Insert or update every manifest row. Safe to run repeatedly."""
    for row in rows:
        stmt = insert(Source).values(**row)
        update = {k: v for k, v in row.items() if k != "source_id"}
        session.execute(stmt.on_conflict_do_update(index_elements=["source_id"], set_=update))
    return len(rows)


def seed_all(session: Session, manifest: Path = DEFAULT_MANIFEST) -> dict[str, int]:
    seed_roles(session)
    seed_projects(session)
    count = seed_sources(session, read_manifest(manifest))
    counts = {"roles": len(ROLE_KEYS), "projects": len(SAMPLE_PROJECTS), "sources": count}
    # A real record that this data was loaded (replaces the invented sample history).
    session.add(
        AuditEvent(
            actor_id="system:seed-script",
            actor_role="system",
            actor_roles=["system"],
            action="DATABASE_SEEDED",
            resource_type="database",
            outcome="SUCCESS",
            details={**counts, "manifest": manifest.name},
        )
    )
    session.commit()
    return counts
