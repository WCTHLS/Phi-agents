"""Chunks: a keyword-search column and index (S2-07).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03

Plain-language notes:
- Search by meaning (the embeddings) is good at paraphrases but can miss an exact term such as a
  guideline code ("M13A") or a substance name. Keyword search finds those. PostgreSQL has this
  built in: a *tsvector* is a chunk's words, reduced to their stems and indexed.
- ``search_vector`` is a *generated* column: the database fills it from ``section`` and ``text``
  whenever a chunk is written, so it can never disagree with the text, and no application code or
  re-indexing is needed. Existing chunks get it when the column is added.
- ``ix_chunks_search_vector`` is a GIN index, the standard index for this kind of search.
- Nothing is changed in the licence rule, the audit table or the access tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept here, not imported, so this migration keeps meaning the same thing if the model changes.
SEARCH_EXPRESSION = "to_tsvector('english', coalesce(section, '') || ' ' || text)"


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_EXPRESSION, persisted=True),
            nullable=True,
        ),
    )
    op.create_index("ix_chunks_search_vector", "chunks", ["search_vector"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_search_vector", table_name="chunks")
    op.drop_column("chunks", "search_vector")
