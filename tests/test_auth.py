"""Authentication and authorization tests for the API (S1-09, groundwork for S1-13)."""

import pytest
from fastapi.testclient import TestClient

from pharma_ai.core.config import Settings
from pharma_ai.infrastructure.security.auth import AuthConfigurationError, TokenVerifier
from pharma_ai.main import app

client = TestClient(app)

PROTECTED_GETS = [
    "/api/v1/research/sources",
    "/api/v1/projects",
    "/api/v1/projects/PRJ-001",
]


# --- Public endpoints stay open -------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/health", "/ready"])
def test_public_endpoints_do_not_require_a_token(path: str) -> None:
    assert client.get(path).status_code == 200


# --- 401: missing or invalid credentials ----------------------------------------------


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_missing_token_returns_401(path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_research_query_without_token_returns_401() -> None:
    response = client.post("/api/v1/research/query", json={"query": "NDMA limit"})
    assert response.status_code == 401


def test_non_bearer_scheme_returns_401() -> None:
    response = client.get("/api/v1/projects", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401


def test_garbage_token_returns_401() -> None:
    response = client.get("/api/v1/projects", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_token_signed_with_wrong_key_returns_401(make_token) -> None:
    token = make_token(secret="some-other-key-that-is-long-enough-000000")
    response = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_expired_token_returns_401(make_token) -> None:
    token = make_token(expires_in=-60)
    response = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_token_from_wrong_issuer_returns_401(make_token) -> None:
    token = make_token(issuer="http://evil.example/realms/pharma")
    response = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# --- 200: valid token -------------------------------------------------------------------


def test_valid_token_can_call_research_sources(auth_headers) -> None:
    response = client.get("/api/v1/research/sources", headers=auth_headers)
    assert response.status_code == 200


def test_project_list_only_contains_callers_projects(auth_headers) -> None:
    response = client.get("/api/v1/projects", headers=auth_headers)
    assert response.status_code == 200
    assert [p["project_id"] for p in response.json()] == ["PRJ-001"]


def test_member_can_read_own_project(auth_headers) -> None:
    response = client.get("/api/v1/projects/PRJ-001", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["project_id"] == "PRJ-001"


# --- 403: authenticated but not authorized -------------------------------------------


def test_cross_project_access_returns_403(auth_headers) -> None:
    response = client.get("/api/v1/projects/PRJ-002", headers=auth_headers)
    assert response.status_code == 403


def test_unknown_project_returns_403_not_404_for_non_member(auth_headers) -> None:
    """Non-members must not learn whether a project ID exists."""
    response = client.get("/api/v1/projects/PRJ-999", headers=auth_headers)
    assert response.status_code == 403


def test_admin_role_does_not_grant_project_data_access(make_token) -> None:
    token = make_token(sub="admin-001", roles=["admin"], projects=[])
    response = client.get("/api/v1/projects/PRJ-001", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


# --- Unsafe configuration is refused --------------------------------------------------


def test_dev_mode_is_refused_outside_local_and_test() -> None:
    cfg = Settings(environment="production", auth_mode="dev", auth_dev_secret="x" * 40)
    with pytest.raises(AuthConfigurationError):
        TokenVerifier(cfg)


def test_dev_mode_requires_a_secret() -> None:
    cfg = Settings(environment="local", auth_mode="dev", auth_dev_secret=None)
    with pytest.raises(AuthConfigurationError):
        TokenVerifier(cfg)
