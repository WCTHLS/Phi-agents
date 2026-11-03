"""Document versions: how the text was extracted and how far to trust it (S2-03).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02

Plain-language notes:
- ``extraction_method`` says which engine read the text (``pypdf`` or ``tika``).
- ``extraction_status`` is ``ok`` (every page has text), ``partial`` (some pages have none, for
  example scans) or ``failed`` (no page has text). The sprint plan calls this *extraction
  uncertainty*: nobody should believe a scanned page was searched.
- ``extraction_notes`` lists the reasons in plain words.
- ``extracted_key`` is where the page-by-page text is stored (the object store, ADR-007).
- Nothing is updated, so the "original is fixed" trigger is untouched: these columns are written
  when the version row is first inserted, and the trigger only guards the fingerprint, the file
  location and the size.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("extraction_method", sa.String(16)))
    op.add_column("document_versions", sa.Column("extraction_status", sa.String(16)))
    op.add_column(
        "document_versions",
        sa.Column(
            "extraction_notes",
            postgresql.ARRAY(sa.Text),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.add_column("document_versions", sa.Column("extracted_key", sa.Text))
    op.create_check_constraint(
        op.f("ck_document_versions_extraction_status_values"),
        "document_versions",
        "extraction_status IS NULL OR extraction_status IN ('ok', 'partial', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_document_versions_extraction_status_values"), "document_versions", type_="check"
    )
    op.drop_column("document_versions", "extracted_key")
    op.drop_column("document_versions", "extraction_notes")
    op.drop_column("document_versions", "extraction_status")
    op.drop_column("document_versions", "extraction_method")
