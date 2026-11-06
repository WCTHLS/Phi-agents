"""Authentication endpoints (S1-09).

Includes a local development endpoint to mint signed tokens for testing without Keycloak.
Strictly disabled when ENVIRONMENT is not 'local' or 'test', or when AUTH_MODE is not 'dev'.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from pharma_ai.core.config import settings
from pharma_ai.infrastructure.security.auth import DEV_ALLOWED_ENVIRONMENTS, mint_dev_token
from pharma_ai.infrastructure.security.dev_users import DEV_USERS

auth_router = APIRouter()


class DevTokenRequest(BaseModel):
    # A named development user (see dev_users.py). When given, the server takes the role, id, email
    # and projects from its own table and the fields below are ignored.
    user: str | None = None
    role: str = "scientist"
    sub: str | None = None
    email: str | None = None
    projects: list[str] = Field(default_factory=lambda: ["PRJ-001", "PRJ-002"])


class DevTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    sub: str
    email: str
    projects: list[str]
    name: str | None = None


@auth_router.post(
    "/dev-token",
    response_model=DevTokenResponse,
    summary="Mint development token (local/dev only)",
    description=(
        'Returns a signed HS256 development JWT. Send {"user": "thorne"} to sign in as a named '
        "development user (the server decides the role and projects), or a role and projects. "
        "Strictly available only in local and test environments when AUTH_MODE=dev."
    ),
)
def create_dev_token(payload: DevTokenRequest) -> DevTokenResponse:
    if settings.auth_mode != "dev" or settings.environment not in DEV_ALLOWED_ENVIRONMENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev token endpoint is disabled outside local/test development.",
        )
    name: str | None = None
    if payload.user is not None:
        person = DEV_USERS.get(payload.user)
        if person is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown development user. Known users: {', '.join(sorted(DEV_USERS))}",
            )
        sub, email, name = person.sub, person.email, person.name
        role, projects = person.role, list(person.projects)
    else:
        sub = payload.sub or f"dev-{payload.role}-001"
        email = payload.email or f"{sub}@pharma.local"
        role, projects = payload.role, payload.projects
    try:
        token = mint_dev_token(settings, sub=sub, email=email, roles=[role], projects=projects)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return DevTokenResponse(
        access_token=token, role=role, sub=sub, email=email, projects=projects, name=name
    )
