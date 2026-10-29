"""Bearer-token authentication and authorization for the API (S1-09).

Two verification modes, selected by ``AUTH_MODE``:

- ``oidc`` (default): RS256 access tokens from the configured OIDC provider (Keycloak or
  Microsoft Entra ID), verified against the provider's published signing keys (JWKS).
- ``dev``: HS256 tokens signed with ``AUTH_DEV_SECRET``. For local development and automated
  tests only; refused when ``ENVIRONMENT`` is not ``local`` or ``test``.

Every verified token must carry ``sub``, ``iss``, ``iat`` and ``exp``, and its issuer must match
``OIDC_ISSUER_URL``. Roles are read from Keycloak ``realm_access.roles`` and the Entra ID
``roles`` claim; project membership is read from the ``projects`` claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pharma_ai.core.config import Settings, settings

DEV_ALLOWED_ENVIRONMENTS = frozenset({"local", "test"})
REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss"]

_bearer_scheme = HTTPBearer(auto_error=False)


class AuthConfigurationError(RuntimeError):
    """Raised when authentication settings are unsafe or incomplete."""


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, derived from verified token claims."""

    subject: str
    email: str | None
    roles: frozenset[str]
    project_ids: frozenset[str]

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def can_access_project(self, project_id: str) -> bool:
        return project_id in self.project_ids


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class TokenVerifier:
    """Verifies bearer tokens according to the configured authentication mode."""

    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._jwks_client: jwt.PyJWKClient | None = None

        if cfg.auth_mode == "dev":
            if cfg.environment not in DEV_ALLOWED_ENVIRONMENTS:
                raise AuthConfigurationError(
                    f"AUTH_MODE=dev is not allowed when ENVIRONMENT={cfg.environment!r}; "
                    "use AUTH_MODE=oidc"
                )
            if not cfg.auth_dev_secret:
                raise AuthConfigurationError("AUTH_MODE=dev requires AUTH_DEV_SECRET to be set")
        else:
            issuer = cfg.oidc_issuer_url.rstrip("/")
            jwks_url = cfg.oidc_jwks_url or f"{issuer}/protocol/openid-connect/certs"
            self._jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)

    def verify(self, token: str) -> dict[str, Any]:
        """Return verified claims, or raise 401 (bad token) / 503 (IdP unreachable)."""
        audience = self._cfg.oidc_audience
        options: dict[str, Any] = {"require": REQUIRED_CLAIMS}
        if audience is None:
            options["verify_aud"] = False

        try:
            if self._jwks_client is None:
                key: Any = self._cfg.auth_dev_secret
                algorithms = ["HS256"]
            else:
                key = self._jwks_client.get_signing_key_from_jwt(token).key
                algorithms = ["RS256"]

            return jwt.decode(
                token,
                key,
                algorithms=algorithms,
                issuer=self._cfg.oidc_issuer_url,
                audience=audience,
                options=options,
            )
        except jwt.PyJWKClientConnectionError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity provider unavailable",
            ) from exc
        except jwt.PyJWKClientError as exc:
            raise _unauthorized("Invalid token") from exc
        except jwt.InvalidTokenError as exc:
            raise _unauthorized("Invalid or expired token") from exc


@lru_cache(maxsize=1)
def get_token_verifier() -> TokenVerifier:
    """Process-wide verifier built from application settings."""
    return TokenVerifier(settings)


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Map verified token claims to a Principal."""
    realm_access = claims.get("realm_access") or {}
    roles = [*(realm_access.get("roles") or []), *(claims.get("roles") or [])]

    projects = claims.get("projects") or []
    if isinstance(projects, str):
        projects = [projects]

    return Principal(
        subject=str(claims["sub"]),
        email=claims.get("email"),
        roles=frozenset(str(role) for role in roles),
        project_ids=frozenset(str(project) for project in projects),
    )


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    """FastAPI dependency: require a valid bearer token and return the caller."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    claims = verifier.verify(credentials.credentials)
    return principal_from_claims(claims)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_role(role: str):
    """FastAPI dependency factory: require the caller to hold ``role``."""

    def _dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(role):
            raise _forbidden(f"Requires role: {role}")
        return principal

    return _dependency


def ensure_project_access(principal: Principal, project_id: str) -> None:
    """Object-level check: raise 403 unless the caller is a member of ``project_id``."""
    if not principal.can_access_project(project_id):
        raise _forbidden("Not authorized for this project")
