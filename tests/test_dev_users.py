"""Tests for the named development users, which exist so project access can be tested (D23).

The first group needs no database. The second asks real questions and records real decisions as
those users, against a real database (TEST_DATABASE_URL, an empty throwaway database).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from db_fixtures import NITRO, PV, add_document, events, needs_db
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.models import ROLE_KEYS
from pharma_ai.infrastructure.db.seed import SAMPLE_PROJECTS
from pharma_ai.infrastructure.security.dev_users import DEV_USERS
from pharma_ai.main import app

OIDC_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "auth" / "oidc.ts"
PROJECTS = ["PRJ-001", "PRJ-002"]

# Who may open which project: the whole point of having several users.
ACCESS = [
    ("thorne", {"PRJ-001"}),
    ("nair", {"PRJ-002"}),
    ("webb", {"PRJ-001", "PRJ-002"}),
    ("lind", set()),
    ("vance", {"PRJ-001"}),
    ("menon", {"PRJ-002"}),
    ("admin", {"PRJ-001", "PRJ-002"}),
]


def sign_in(client: TestClient, user: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/dev-token", json={"user": user})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- the table and the endpoint (no database) ---------------------------------------------------


def test_the_users_are_distinct_and_use_known_roles_and_projects() -> None:
    users = list(DEV_USERS.values())
    for field in ("key", "sub", "email", "name"):
        values = [getattr(u, field) for u in users]
        assert len(values) == len(set(values)), f"two users share a {field}"
    assert {u.role for u in users} <= set(ROLE_KEYS)
    known = {p["project_id"] for p in SAMPLE_PROJECTS}
    assert all(set(u.projects) <= known for u in users)
    assert {key: set(u.projects) for key, u in DEV_USERS.items()} == dict(ACCESS)


def test_the_users_cover_member_non_member_and_per_project_reviewers() -> None:
    # One-project, both-project and no-project scientists, and a reviewer for each project.
    scientists = [u for u in DEV_USERS.values() if u.role == "scientist"]
    assert {len(u.projects) for u in scientists} == {0, 1, 2}
    assert {p for u in DEV_USERS.values() if u.role == "reviewer" for p in u.projects} == set(
        PROJECTS
    )


@pytest.mark.parametrize("user, projects", ACCESS)
def test_each_user_sees_exactly_their_own_projects(
    client: TestClient, user: str, projects: set[str]
) -> None:
    headers = sign_in(client, user)
    listed = client.get("/api/v1/projects", headers=headers).json()
    assert {p["project_id"] for p in listed} == projects
    for project in PROJECTS:
        expected = 200 if project in projects else 403
        assert client.get(f"/api/v1/projects/{project}", headers=headers).status_code == expected


def test_the_token_carries_the_users_role_and_stable_id(client: TestClient) -> None:
    body = client.post("/api/v1/auth/dev-token", json={"user": "menon"}).json()
    assert (body["role"], body["sub"], body["projects"]) == ("reviewer", "dev-menon", ["PRJ-002"])
    assert body["name"] == "Dr. Ravi Menon" and body["email"] == "r.menon@pharma.local"


def test_a_request_cannot_give_a_named_user_more_than_the_table_does(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/dev-token",
        json={"user": "thorne", "role": "admin", "projects": PROJECTS, "sub": "someone-else"},
    )
    body = response.json()
    assert (body["role"], body["sub"], body["projects"]) == ("scientist", "dev-thorne", ["PRJ-001"])
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/v1/projects/PRJ-002", headers=headers).status_code == 403


def test_an_unknown_user_is_refused_and_the_known_ones_are_listed(client: TestClient) -> None:
    response = client.post("/api/v1/auth/dev-token", json={"user": "nobody"})
    assert response.status_code == 404
    assert all(key in response.json()["detail"] for key in DEV_USERS)


def test_asking_for_a_role_and_projects_still_works_as_before(client: TestClient) -> None:
    body = client.post(
        "/api/v1/auth/dev-token", json={"role": "scientist", "projects": ["PRJ-001"]}
    ).json()
    assert (body["role"], body["sub"], body["name"]) == ("scientist", "dev-scientist-001", None)


def test_the_sign_in_screen_lists_the_same_people_as_the_server() -> None:
    source = OIDC_TS.read_text(encoding="utf-8")
    found = re.findall(
        r'key:\s*"(\w+)",\s*name:\s*"([^"]+)",\s*email:\s*"([^"]+)",\s*role:\s*"(\w+)",\s*'
        r"projects:\s*\[([^\]]*)\]",
        source,
    )
    shown = {
        key: (name, email, role, tuple(re.findall(r'"([^"]+)"', projects)))
        for key, name, email, role, projects in found
    }
    served = {k: (u.name, u.email, u.role, u.projects) for k, u in DEV_USERS.items()}
    assert shown == served


# --- asking and reviewing as those users (database) ---------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@needs_db
def test_a_member_lists_their_documents_and_a_non_member_is_refused_and_recorded(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", ["NDMA is limited to 96 ng/day."])
    add_document(session, embedder, PV, "PRJ-002", ["Process validation has three stages."])
    nair = sign_in(api, "nair")

    mine = api.get("/api/v1/projects/PRJ-002/documents", headers=nair)
    assert mine.status_code == 200 and [d["source_id"] for d in mine.json()] == [PV]
    other = api.get("/api/v1/projects/PRJ-001/documents", headers=nair)
    assert other.status_code == 403 and NITRO not in other.text

    denied = events(api, action="ACCESS_DENIED", actor="dev-nair")
    assert [(e["project_id"], e["outcome"]) for e in denied] == [("PRJ-001", "DENIED")]


@needs_db
def test_a_user_in_no_project_is_refused_everywhere(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(session, embedder, NITRO, "PRJ-001", ["NDMA is limited to 96 ng/day."])
    lind = sign_in(api, "lind")
    for project in PROJECTS:
        assert api.get(f"/api/v1/projects/{project}/documents", headers=lind).status_code == 403
        asked = api.post(
            "/api/v1/research/query",
            json={"query": "NDMA limit", "project_id": project},
            headers=lind,
        )
        assert asked.status_code == 403


@needs_db
def test_only_the_reviewer_of_the_projects_own_answer_can_decide_on_it(
    api: TestClient, session: Session, embedder: FakeEmbedder
) -> None:
    add_document(
        session, embedder, NITRO, "PRJ-001", ["The acceptable intake for NDMA is 96 ng/day."]
    )
    asked = api.post(
        "/api/v1/research/query",
        json={"query": "What is the acceptable intake for NDMA?", "project_id": "PRJ-001"},
        headers=sign_in(api, "thorne"),
    )
    assert asked.status_code == 200
    run = asked.json()["workflow_run_id"]
    decision = {
        "workflow_run_id": run,
        "project_id": "PRJ-001",
        "decision": "APPROVED",
        "rationale": "Checked against the FDA appendix.",
    }
    url = "/api/v1/research/reviews"

    assert api.post(url, json=decision, headers=sign_in(api, "menon")).status_code == 403  # PRJ-002
    assert (
        api.post(url, json=decision, headers=sign_in(api, "thorne")).status_code == 403
    )  # scientist
    assert api.post(url, json=decision, headers=sign_in(api, "admin")).status_code == 403  # admin
    assert api.post(url, json=decision, headers=sign_in(api, "vance")).status_code == 201

    decided = events(api, action="ANSWER_REVIEW_APPROVED")
    assert [(e["actor_subject"], e["project_id"]) for e in decided] == [("dev-vance", "PRJ-001")]
