"""Environment-backed application settings."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Pharma AI Development Assistant"
    environment: str = "local"
    log_level: str = "INFO"
    # "json" (one JSON object per line, for servers and log tools) or "text" (readable, for local
    # work). Both redact secrets and personal data; see core/redaction.py.
    log_format: Literal["json", "text"] = "json"
    database_url: str = "postgresql://pharma_ai:change_me_local_only@localhost:5432/pharma_ai"
    tika_url: str = "http://localhost:9998"

    # Document ingestion (S2-03, ADR-007). Originals and extracted text are kept in this folder.
    # Relative paths are relative to where the API is started (the repository root).
    storage_root: str = "data/storage"
    # Largest file the upload endpoint accepts, in megabytes.
    max_upload_mb: int = 50
    # "pypdf" reads the PDF's own text layer in-process. "tika" asks the Tika server and falls
    # back to pypdf if Tika cannot be reached.
    extraction_engine: Literal["pypdf", "tika"] = "pypdf"

    # Chunking and embeddings (S2-06, ADR-008). The model must give 384-number vectors (the size of
    # the database column); changing it means the stored passages are embedded again.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 32
    # Load the model in the background when the API starts, so the first search does not wait for
    # it (the first load took minutes on a laptop). The API answers other requests meanwhile.
    embedding_warmup: bool = True
    # Passages are cut to at most this many tokens, repeating the last `overlap` tokens at the start
    # of the next one (ADR-003). The model reads 512 tokens including two markers, so keep it below.
    chunk_size: int = 500
    chunk_overlap: int = 100

    # Language model for answers (S2-09, ADR-010). "not_configured": no model, answers are made by
    # matching the question's words (nothing leaves this machine). "groq": a hosted open-weight
    # model on Groq picks the sentences to quote; the question and the retrieved passages are sent
    # to Groq. For development on public documents only (decision log D19). These names already
    # existed in .env.example as placeholders. The key is a secret: set it in .env only.
    llm_provider: Literal["not_configured", "groq"] = "not_configured"
    # "not_configured" means the provider's default model (groq: DEFAULT_GROQ_MODEL in the factory).
    llm_model: str = "not_configured"
    groq_api_key: SecretStr | None = None
    # Must be https on api.groq.com, so the key cannot be sent anywhere else by a typo.
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_seconds: float = 30.0

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
