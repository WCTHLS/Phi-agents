"""Tests for the index endpoint and the rules around it (decision log D28).

An upload stores a PDF and its text; it is not searchable until it is indexed. These tests cover the
index endpoint, the register's view of what is searchable, the rule that an unindexed new version does
not hide the older indexed one, and the warning for a PDF that does not look like its register entry.

Needs TEST_DATABASE_URL (an empty throwaway database; see test_db_integration.py).
"""

from __future__ import annotations

import pytest
from db_fixtures import Q3D, add_document, events, headers, needs_db
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from pdf_factory import make_pdf
from sqlalchemy.orm import Session

from pharma_ai.features.documents.title_check import title_warning
from pharma_ai.infrastructure.db.repositories import ChunkRepository
from pharma_ai.infrastructure.storage import LocalFolderStore

pytestmark = needs_db

ADMIN = headers(["admin"], ["PRJ-001", "PRJ-002"], sub="index-admin")
TITLE = "Guideline for Elemental Impurities"  # the register title of ICH-Q3DR2
GOOD_PAGES = [f"{TITLE}. Cadmium oral permitted daily exposure", "Class 1 elements and risk"]
OTHER_PAGES = ["Sampling plans for tablets", "Weighing and mixing of granules"]


def upload(api: TestClient, pages: list[str], source: str = Q3D, **params):
    return api.put(
        f"/api/v1/sources/{source}/versions",
        content=make_pdf(pages),
        params={"project_id": "PRJ-001", **params},
        headers=ADMIN,
    )


def index(api: TestClient, number: int, source: str = Q3D, caller: dict | None = None):
    return api.post(f"/api/v1/sources/{source}/versions/{number}/index", headers=caller or ADMIN)


def register_row(api: TestClient, source: str = Q3D) -> dict:
    rows = api.get("/api/v1/sources", headers=ADMIN).json()
    return next(row for row in rows if row["source_id"] == source)


def test_an_uploaded_version_is_not_searchable_until_it_is_indexed(api: TestClient) -> None:
    created = upload(api, GOOD_PAGES)
    assert created.status_code == 201
    assert created.json()["passages"] == 0
    row = register_row(api)
    assert (row["latest_version"], row["latest_passages"], row["searchable_version"]) == (
        1,
        0,
        None,
    )

    done = index(api, 1)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "indexed" and body["passages"] >= 1 and body["version_number"] == 1
    row = register_row(api)
    assert (row["latest_version"], row["searchable_version"]) == (1, 1)
    assert row["latest_passages"] == body["passages"]


def test_indexing_is_recorded_once_and_a_second_call_changes_nothing(api: TestClient) -> None:
    upload(api, GOOD_PAGES)
    first, second = index(api, 1), index(api, 1)
    assert (first.json()["status"], second.json()["status"]) == ("indexed", "already_indexed")
    assert second.json()["passages"] == first.json()["passages"]
    recorded = events(api, action="DOCUMENT_INDEXED")
    assert [e["actor_subject"] for e in recorded] == ["index-admin"]
    assert recorded[0]["details"]["chunk_count"] == first.json()["passages"]


def test_the_register_and_upload_say_how_many_passages_a_version_has(api: TestClient) -> None:
    upload(api, GOOD_PAGES)
    index(api, 1)
    again = upload(api, GOOD_PAGES)  # the same file: nothing new, but it is already indexed
    assert again.status_code == 200 and again.json()["already_ingested"] is True
    assert again.json()["passages"] >= 1


@pytest.mark.parametrize("roles", [["scientist"], ["reviewer"], ["qa"]])
def test_only_an_administrator_can_index(api: TestClient, roles: list[str]) -> None:
    upload(api, GOOD_PAGES)
    refused = index(api, 1, caller=headers(roles, ["PRJ-001"], sub="not-an-admin"))
    assert refused.status_code == 403
    assert register_row(api)["searchable_version"] is None


def test_indexing_needs_a_token(api: TestClient) -> None:
    assert api.post(f"/api/v1/sources/{Q3D}/versions/1/index").status_code == 401


def test_a_version_that_was_never_stored_is_a_404_and_is_recorded(api: TestClient) -> None:
    missing = index(api, 7)
    assert missing.status_code == 404 and "No version 7" in missing.json()["detail"]
    refused = events(api, action="DOCUMENT_INDEX_REFUSED")
    assert [e["details"]["reason"] for e in refused] == ["version_not_found"]


def test_a_new_version_does_not_hide_the_older_one_until_it_is_indexed(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    upload(api, GOOD_PAGES)
    index(api, 1)
    second = upload(api, GOOD_PAGES + ["A third page of text about cadmium limits"])
    assert second.status_code == 201 and second.json()["version_number"] == 2

    def searchable_versions() -> set[int]:
        hits = ChunkRepository(session).search_nearest(
            embedder.embed(["cadmium"])[0],
            ["PRJ-001"],
            embedding_model=embedder.model_name,
            limit=20,
        )
        return {h.version_number for h in hits}

    assert searchable_versions() == {1}  # version 2 is stored but has no passages yet
    row = register_row(api)
    assert (row["latest_version"], row["latest_passages"], row["searchable_version"]) == (2, 0, 1)

    assert index(api, 2).json()["status"] == "indexed"
    assert searchable_versions() == {2}  # now the newest indexed version replaces version 1


def test_an_unindexed_upload_does_not_hide_a_different_documents_passages(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, "FDA-PV-2011", "PRJ-001", ["Process validation text"])
    upload(api, GOOD_PAGES)  # stored, not indexed
    hits = ChunkRepository(session).search_nearest(
        embedder.embed(["validation"])[0],
        ["PRJ-001"],
        embedding_model=embedder.model_name,
        limit=20,
    )
    assert {h.source_id for h in hits} == {"FDA-PV-2011"}


def test_a_pdf_that_does_not_look_like_its_register_entry_gets_a_warning(api: TestClient) -> None:
    wrong = upload(api, OTHER_PAGES)
    assert wrong.status_code == 201  # advice only: the upload is not refused
    (warning,) = wrong.json()["warnings"]
    assert TITLE in warning and "right file" in warning


def test_a_pdf_that_matches_its_register_entry_has_no_warning(api: TestClient) -> None:
    assert upload(api, GOOD_PAGES).json()["warnings"] == []


def test_a_scan_with_no_text_gets_no_title_warning() -> None:
    assert title_warning(TITLE, ["", ""]) is None
    assert title_warning(TITLE, []) is None


def test_the_title_check_looks_only_at_the_first_pages() -> None:
    late = ["intro", "more intro", "still nothing", f"{TITLE} appears on page four only"]
    assert title_warning(TITLE, late) is not None
    assert title_warning(TITLE, late[:3] + [""]) is not None
    assert title_warning(TITLE, [f"{TITLE} on page one"] + late[:3]) is None


def test_a_title_with_fewer_than_two_distinctive_words_is_not_checked() -> None:
    assert title_warning("Quality Risk Management Guidance", ["nothing relevant"]) is not None
    assert (
        title_warning("Guidance on Tablets", ["nothing relevant"]) is None
    )  # one word: too little


def test_indexing_a_stored_version_whose_text_was_lost_is_refused(
    api: TestClient, store: LocalFolderStore
) -> None:
    upload(api, GOOD_PAGES)
    for path in (store.root / "extracted").rglob("*.json"):
        path.unlink()
    refused = index(api, 1)
    assert refused.status_code == 409
    assert [e["details"]["reason"] for e in events(api, action="DOCUMENT_INDEX_REFUSED")] == [
        "extracted_text_missing"
    ]
