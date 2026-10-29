"""Environment-backed application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Pharma AI Development Assistant"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql://pharma_ai:change_me_local_only@localhost:5432/pharma_ai"
    tika_url: str = "http://localhost:9998"
    oidc_issuer_url: str = "http://localhost:8080/realms/pharma"
    oidc_client_id: str = "pharma-ui"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
