"""Engine and session helpers."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pharma_ai.core.config import settings


def to_sqlalchemy_url(url: str) -> str:
    """Return a URL that selects the psycopg 3 driver.

    The app setting ``DATABASE_URL`` uses the plain ``postgresql://`` form (also understood by
    psql and docker). SQLAlchemy needs ``postgresql+psycopg://`` to pick the psycopg 3 driver.
    """
    for plain in ("postgresql://", "postgres://"):
        if url.startswith(plain):
            return "postgresql+psycopg://" + url[len(plain) :]
    return url


# How long to wait for the database before giving up. Without a limit, a database host that stops
# answering can hold a request open for minutes (seen on Windows); the audit trail must fail
# closed quickly instead.
CONNECT_TIMEOUT_SECONDS = 5


def make_engine(url: str | None = None) -> Engine:
    return create_engine(
        to_sqlalchemy_url(url or settings.database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
