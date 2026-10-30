"""Shared test configuration.

Tests run with AUTH_MODE=dev so tokens can be minted locally (HS256) without a running
Keycloak. These variables are set before any application module is imported.
"""

import os
import time
from collections.abc import Callable

import jwt
import pytest

TEST_ISSUER = "http://localhost:8080/realms/pharma"
TEST_DEV_SECRET = "test-only-signing-key-not-a-real-secret-000000"

os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_MODE"] = "dev"
os.environ["AUTH_DEV_SECRET"] = TEST_DEV_SECRET
os.environ["OIDC_ISSUER_URL"] = TEST_ISSUER
os.environ["AUDIT_BACKEND"] = "memory"


def mint_token(
    *,
    sub: str = "user-scientist-001",
    roles: list[str] | None = None,
    projects: list[str] | None = None,
    issuer: str = TEST_ISSUER,
    secret: str = TEST_DEV_SECRET,
    expires_in: int = 300,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
        "email": f"{sub}@pharma.local",
        "realm_access": {"roles": roles if roles is not None else ["scientist"]},
        "projects": projects if projects is not None else ["PRJ-001"],
    }
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def fresh_audit_trail():
    """Every test starts with an empty in-memory audit trail."""
    from pharma_ai.infrastructure.audit.service import reset_audit_service

    reset_audit_service()
    yield
    reset_audit_service()


@pytest.fixture
def make_token() -> Callable[..., str]:
    return mint_token


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Headers for a scientist who is a member of PRJ-001 only."""
    return {"Authorization": f"Bearer {mint_token()}"}
