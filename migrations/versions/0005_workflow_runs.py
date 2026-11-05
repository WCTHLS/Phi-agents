"""Workflow runs: stored questions, answers and their review status (S2-09).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04

Plain-language notes:
- A *workflow run* is one question a scientist asked of a project's documents, with the answer the
  system gave, the passages it quoted, and where the answer stands in human review.
- Until now nothing about an answer was kept on the server, so the review screen could not be
  checked against anything. With this table the review endpoint can confirm that the run exists,
  belongs to the project, and record the reviewer's decision as a status change.
- The question and answer text are stored here, protected by the project membership checks. The
  audit trail still keeps only a fingerprint of the question.
- Unlike ``audit_events`` this table is ordinary: ``status`` is updated when a reviewer decides.
  The history of decisions is in the audit trail, written in the same transaction as each update.
- The check on ``decision_with_status`` keeps the three decision columns consistent: an answer is
  pending exactly when nobody has decided it yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
EMPTY_LIST = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("asked_by", sa.String(128), nullable=False),
        sa.Column("asked_by_email", sa.String(320)),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("question_sha256", sa.String(64), nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("answer_mode", sa.String(128), nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False),
        sa.Column("coverage", sa.Float, nullable=False),
        sa.Column("evidence_gaps", postgresql.JSONB, server_default=EMPTY_LIST, nullable=False),
        sa.Column("citations", postgresql.JSONB, server_default=EMPTY_LIST, nullable=False),
        sa.Column("retrieved", postgresql.JSONB, server_default=EMPTY_LIST, nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            server_default=sa.text("'PENDING_EXPERT_REVIEW'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("decided_by", sa.String(128)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_workflow_runs")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name=op.f("fk_workflow_runs_project_id_projects"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_EXPERT_REVIEW', 'APPROVED', 'REJECTED', 'EVIDENCE_REQUESTED')",
            name=op.f("ck_workflow_runs_status_values"),
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 1", name=op.f("ck_workflow_runs_coverage_range")
        ),
        sa.CheckConstraint(
            "(status = 'PENDING_EXPERT_REVIEW') = (decided_at IS NULL) "
            "AND (decided_at IS NULL) = (decided_by IS NULL)",
            name=op.f("ck_workflow_runs_decision_with_status"),
        ),
    )
    op.create_index(
        "ix_workflow_runs_project_id_created_at", "workflow_runs", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_project_id_created_at", table_name="workflow_runs")
    op.drop_table("workflow_runs")
