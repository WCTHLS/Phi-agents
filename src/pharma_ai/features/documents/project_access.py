"""Which projects may search which documents (decision log D22).

Plain language: a document is only searched for a project it is *linked* to (the
``project_documents`` table). Ingestion links a document to the projects it is given; this module
changes those links afterwards, so the same set of documents can be divided between projects.

The rule it applies: for each document named in the assignment, the projects named in the
assignment may use it if they list it and may not if they do not. Documents the assignment does
not name, and projects it does not name, are never touched, so a document an administrator
uploaded to some other project keeps its access. Nothing is committed here: the caller saves the
changes together with their audit events, so access cannot change without a record.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.models import Document, Project, ProjectDocument, Source


class AssignmentError(Exception):
    """The assignment names a project or a source that does not exist."""


@dataclass(frozen=True)
class AccessChange:
    action: Literal["grant", "revoke"]
    project_id: str
    source_id: str
    document_id: uuid.UUID


@dataclass(frozen=True)
class AccessPlan:
    changes: tuple[AccessChange, ...]
    not_ingested: tuple[str, ...]  # listed sources that have no stored document yet: skipped


def plan_assignment(session: Session, assignment: Mapping[str, Sequence[str]]) -> AccessPlan:
    """Work out which links to add and remove. Changes nothing."""
    projects = sorted(assignment)
    known_projects = set(
        session.scalars(select(Project.project_id).where(Project.project_id.in_(projects)))
    )
    if missing := [p for p in projects if p not in known_projects]:
        raise AssignmentError(f"unknown project: {', '.join(missing)}")

    wanted: dict[str, set[str]] = {}  # source id -> the projects that list it
    for project, sources in assignment.items():
        for source_id in sources:
            wanted.setdefault(source_id, set()).add(project)
    known_sources = set(
        session.scalars(select(Source.source_id).where(Source.source_id.in_(list(wanted))))
    )
    if unknown := sorted(set(wanted) - known_sources):
        raise AssignmentError(f"unknown source: {', '.join(unknown)}")

    documents = {
        d.source_id: d.document_id
        for d in session.scalars(select(Document).where(Document.source_id.in_(list(wanted))))
    }
    linked = set(
        session.execute(
            select(ProjectDocument.project_id, ProjectDocument.document_id).where(
                ProjectDocument.project_id.in_(projects),
                ProjectDocument.document_id.in_(list(documents.values())),
            )
        ).all()
    )

    changes: list[AccessChange] = []
    not_ingested: list[str] = []
    for source_id in sorted(wanted):
        document_id = documents.get(source_id)
        if document_id is None:
            not_ingested.append(source_id)
            continue
        for project in projects:
            has = (project, document_id) in linked
            should = project in wanted[source_id]
            if should and not has:
                changes.append(AccessChange("grant", project, source_id, document_id))
            elif has and not should:
                changes.append(AccessChange("revoke", project, source_id, document_id))
    return AccessPlan(tuple(changes), tuple(not_ingested))


def apply_changes(session: Session, changes: Sequence[AccessChange]) -> None:
    """Add and remove the links. The caller commits, after writing the audit events."""
    for change in changes:
        if change.action == "grant":
            session.add(
                ProjectDocument(project_id=change.project_id, document_id=change.document_id)
            )
        else:
            session.execute(
                delete(ProjectDocument).where(
                    ProjectDocument.project_id == change.project_id,
                    ProjectDocument.document_id == change.document_id,
                )
            )
    session.flush()
