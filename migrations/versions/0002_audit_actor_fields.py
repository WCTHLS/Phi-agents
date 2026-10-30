"""Audit trail: who exactly, which version, and a fixed set of outcomes (S1-11).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30

Plain-language notes:
- ``actor_id`` keeps holding the login token's stable subject ID. The person's email goes in a new
  column, because an email can change while the subject ID does not.
- ``actor_roles`` lists every role the person held at the time (an unordered "first role" would
  record a different one each time for someone with several roles).
- ``resource_version`` records which version of the object was acted on (for example a document
  or report version), as the sprint plan asks: who, what, when, object, version, outcome.
- ``outcome`` is limited to SUCCESS, DENIED or FAILURE so the viewer's filters stay meaningful.
- The existing append-only triggers are untouched: adding columns does not update any row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("actor_email", sa.String(320)))
    op.add_column(
        "audit_events",
        sa.Column(
            "actor_roles",
            postgresql.ARRAY(sa.String),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.add_column("audit_events", sa.Column("resource_version", sa.String(64)))
    op.create_check_constraint(
        op.f("ck_audit_events_outcome_values"),
        "audit_events",
        "outcome IN ('SUCCESS', 'DENIED', 'FAILURE')",
    )
    # Reconstruction query: "everything that happened to this object, in order".
    op.create_index(
        "ix_audit_events_resource_type_resource_id_created_at",
        "audit_events",
        ["resource_type", "resource_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource_type_resource_id_created_at", table_name="audit_events")
    op.drop_constraint(op.f("ck_audit_events_outcome_values"), "audit_events", type_="check")
    op.drop_column("audit_events", "resource_version")
    op.drop_column("audit_events", "actor_roles")
    op.drop_column("audit_events", "actor_email")
