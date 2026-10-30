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


def make_engine(url: str | None = None) -> Engine:
    return create_engine(to_sqlalchemy_url(url or settings.database_url), pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
