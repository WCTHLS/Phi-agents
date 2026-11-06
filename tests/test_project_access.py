"""Tests for dividing documents between projects (decision log D22).

The first group needs no database. The rest need TEST_DATABASE_URL (an empty throwaway database,
see test_db_integration.py); each test runs in a transaction that is rolled back.
"""

from __future__ import annotations

import pytest
from db_fixtures import NITRO, PV, Q3D, TEST_DATABASE_URL, add_document
from fake_embedder import FakeEmbedder
from sqlalchemy.orm import Session

from pharma_ai.features.documents.project_access import (
    AssignmentError,
    apply_changes,
    plan_assignment,
)
from pharma_ai.infrastructure.db.repositories import ChunkRepository, DocumentRepository
from pharma_ai.infrastructure.db.seed import (
    DEFAULT_MANIFEST,
    SAMPLE_PROJECT_DOCUMENTS,
    SAMPLE_PROJECTS,
    read_manifest,
)

needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run database tests"
)

M9 = "ICH-M9"


# --- the assignment itself (no database) ---------------------------------------------------------


def test_the_sample_assignment_gives_every_document_to_exactly_one_project() -> None:
    listed = [source for sources in SAMPLE_PROJECT_DOCUMENTS.values() for source in sources]
    assert len(listed) == len(set(listed)) == 13  # none twice; the 13 documents of the corpus
    assert set(SAMPLE_PROJECT_DOCUMENTS) == {p["project_id"] for p in SAMPLE_PROJECTS}
    assert all(len(sources) >= 5 for sources in SAMPLE_PROJECT_DOCUMENTS.values())


def test_every_assigned_document_is_an_eligible_source_in_the_manifest() -> None:
    eligible = {row["source_id"] for row in read_manifest(DEFAULT_MANIFEST) if row["eligible"]}
    listed = {s for sources in SAMPLE_PROJECT_DOCUMENTS.values() for s in sources}
    assert listed <= eligible, sorted(listed - eligible)


# --- planning and applying (database) -----------------------------------------------------------


def _link(session: Session, source_id: str, *projects: str) -> None:
    docs = DocumentRepository(session)
    document = docs.register_document(source_id)
    for project in projects:
        docs.grant_project_access(document.document_id, project)


def _visible(session: Session, project: str) -> list[str]:
    return [d.source_id for d in DocumentRepository(session).list_for_project(project, [project])]


@needs_db
def test_a_document_in_both_projects_is_taken_from_the_one_that_does_not_list_it(
    session: Session,
) -> None:
    _link(session, Q3D, "PRJ-001", "PRJ-002")
    _link(session, M9, "PRJ-001", "PRJ-002")
    plan = plan_assignment(session, {"PRJ-001": [Q3D], "PRJ-002": [M9]})
    assert sorted((c.action, c.project_id, c.source_id) for c in plan.changes) == [
        ("revoke", "PRJ-001", M9),
        ("revoke", "PRJ-002", Q3D),
    ]
    apply_changes(session, plan.changes)
    assert _visible(session, "PRJ-001") == [Q3D]
    assert _visible(session, "PRJ-002") == [M9]


@needs_db
def test_a_document_nobody_can_see_is_given_to_its_project(session: Session) -> None:
    _link(session, Q3D)  # ingested without a project
    plan = plan_assignment(session, {"PRJ-001": [Q3D], "PRJ-002": []})
    assert [(c.action, c.project_id) for c in plan.changes] == [("grant", "PRJ-001")]
    apply_changes(session, plan.changes)
    assert _visible(session, "PRJ-001") == [Q3D] and _visible(session, "PRJ-002") == []


@needs_db
def test_running_it_again_changes_nothing(session: Session) -> None:
    _link(session, Q3D, "PRJ-001", "PRJ-002")
    assignment = {"PRJ-001": [Q3D], "PRJ-002": []}
    apply_changes(session, plan_assignment(session, assignment).changes)
    assert plan_assignment(session, assignment).changes == ()


@needs_db
def test_documents_and_projects_the_assignment_does_not_name_are_left_alone(
    session: Session,
) -> None:
    _link(session, Q3D, "PRJ-001", "PRJ-002")
    _link(session, PV, "PRJ-001", "PRJ-002")  # not named: both projects keep it
    apply_changes(session, plan_assignment(session, {"PRJ-001": [Q3D]}).changes)
    # Only PRJ-001 was named, so PRJ-002's access to Q3D is untouched too.
    assert sorted(_visible(session, "PRJ-001")) == sorted([Q3D, PV])
    assert sorted(_visible(session, "PRJ-002")) == sorted([Q3D, PV])


@needs_db
def test_a_document_listed_by_both_projects_stays_in_both(session: Session) -> None:
    _link(session, PV, "PRJ-001", "PRJ-002")
    plan = plan_assignment(session, {"PRJ-001": [PV], "PRJ-002": [PV]})
    assert plan.changes == ()


@needs_db
def test_a_listed_document_that_was_never_ingested_is_reported_and_skipped(
    session: Session,
) -> None:
    _link(session, Q3D, "PRJ-001", "PRJ-002")
    plan = plan_assignment(session, {"PRJ-001": [Q3D, NITRO], "PRJ-002": []})
    assert plan.not_ingested == (NITRO,)
    assert [(c.action, c.project_id, c.source_id) for c in plan.changes] == [
        ("revoke", "PRJ-002", Q3D)
    ]


@needs_db
@pytest.mark.parametrize(
    "assignment, message",
    [
        ({"PRJ-009": [Q3D]}, "unknown project: PRJ-009"),
        ({"PRJ-001": ["NOT-A-SOURCE"]}, "unknown source: NOT-A-SOURCE"),
    ],
)
def test_an_assignment_naming_something_that_does_not_exist_is_refused(
    session: Session, assignment: dict[str, list[str]], message: str
) -> None:
    with pytest.raises(AssignmentError, match=message):
        plan_assignment(session, assignment)


@needs_db
def test_after_the_split_a_project_can_no_longer_find_the_other_projects_passages(
    session: Session, embedder: FakeEmbedder
) -> None:
    document = add_document(session, embedder, Q3D, "PRJ-001", ["cadmium oral exposure limit"])
    DocumentRepository(session).grant_project_access(document.document_id, "PRJ-002")
    vector = embedder.embed(["cadmium oral exposure limit"])[0]
    chunks = ChunkRepository(session)

    def found(project: str) -> int:
        hits = chunks.search_nearest(vector, [project], embedding_model=embedder.model_name)
        return len(hits)

    assert found("PRJ-001") == 1 and found("PRJ-002") == 1  # both could search it
    apply_changes(session, plan_assignment(session, {"PRJ-001": [Q3D], "PRJ-002": []}).changes)
    assert found("PRJ-001") == 1 and found("PRJ-002") == 0
