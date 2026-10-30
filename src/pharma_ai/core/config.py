"""Environment-backed application settings."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Pharma AI Development Assistant"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql://pharma_ai:change_me_local_only@localhost:5432/pharma_ai"
    tika_url: str = "http://localhost:9998"

    # Where the audit trail is stored (S1-11). "memory" is for tests and database-less local runs
    # and is refused unless ENVIRONMENT is local or test.
    audit_backend: Literal["database", "memory"] = "database"

    # Authentication (S1-09). "oidc" verifies RS256 tokens from the identity provider's JWKS.
    # "dev" verifies HS256 tokens signed with AUTH_DEV_SECRET and is refused outside local/test.
    auth_mode: Literal["oidc", "dev"] = "oidc"
    auth_dev_secret: str | None = None
    # Issuer must match the "iss" claim exactly (the URL the browser used to reach the IdP).
    oidc_issuer_url: str = "http://localhost:8080/realms/pharma"
    # Optional: where the API fetches signing keys, if different from the issuer
    # (e.g. inside containers). Defaults to the Keycloak certs URL under the issuer.
    oidc_jwks_url: str | None = None
    # Optional: expected "aud" claim. Audience is not checked when unset.
    oidc_audience: str | None = None
    oidc_client_id: str = "pharma-ui"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
