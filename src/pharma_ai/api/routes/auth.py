"""Authentication endpoints (S1-09).

Includes a local development endpoint to mint signed tokens for testing without Keycloak.
Strictly disabled when ENVIRONMENT is not 'local' or 'test', or when AUTH_MODE is not 'dev'.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from pharma_ai.core.config import settings
from pharma_ai.infrastructure.security.auth import DEV_ALLOWED_ENVIRONMENTS
from scripts.dev_token import mint_dev_token

auth_router = APIRouter()


class DevTokenRequest(BaseModel):
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


@auth_router.post(
    "/dev-token",
    response_model=DevTokenResponse,
    summary="Mint development token (local/dev only)",
    description=(
        "Returns a signed HS256 development JWT for the requested role and projects. "
        "Strictly available only in local and test environments when AUTH_MODE=dev."
    ),
)
def create_dev_token(payload: DevTokenRequest) -> DevTokenResponse:
    if settings.auth_mode != "dev" or settings.environment not in DEV_ALLOWED_ENVIRONMENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev token endpoint is disabled outside local/test development.",
        )
    sub = payload.sub or f"dev-{payload.role}-001"
    email = payload.email or f"{sub}@pharma.local"
    try:
        token = mint_dev_token(
            settings,
            sub=sub,
            email=email,
            roles=[payload.role],
            projects=payload.projects,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return DevTokenResponse(
        access_token=token,
        role=payload.role,
        sub=sub,
        email=email,
        projects=payload.projects,
    )
