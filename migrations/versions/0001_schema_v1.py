"""Schema v1: projects, access, source register, documents, chunks and audit events (S1-07).

Revision ID: 0001
Revises:
Create Date: 2026-10-29

Plain-language notes:
- This replaces the three hand-made tables in scripts/init-db.sql with a versioned schema.
  ``sources`` takes over from ``approved_sources``.
- The database itself refuses: a document for a source that is not eligible (ADR-002), a
  stored original with an empty or malformed SHA-256, and any change to an audit record.
- The pgvector extension is created here but never dropped on downgrade, because other
  objects in the same database may rely on it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 384
EMPTY_INPUT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

NOW = sa.text("now()")
NEW_UUID = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- people and projects -------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("role_key", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("role_key", name=op.f("pk_roles")),
    )
    op.bulk_insert(
        sa.table("roles", sa.column("role_key", sa.String), sa.column("description", sa.Text)),
        [
            {
                "role_key": "scientist",
                "description": "Asks questions, reviews evidence and verifies extracted values",
            },
            {"role_key": "reviewer", "description": "Reviews and approves drafts and study plans"},
            {"role_key": "qa", "description": "Quality assurance reviewer"},
            {"role_key": "admin", "description": "Manages access and can read the audit log"},
        ],
    )

    op.create_table(
        "users",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), server_default=NEW_UUID, nullable=False
        ),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("email", sa.Text),
        sa.Column("display_name", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint("subject", name=op.f("uq_users_subject")),
    )

    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("therapeutic_area", sa.String(128), nullable=False),
        sa.Column("dosage_form", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_projects")),
    )

    op.create_table(
        "memberships",
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_key", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("project_id", "user_id", "role_key", name=op.f("pk_memberships")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name=op.f("fk_memberships_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_key"], ["roles.role_key"], name=op.f("fk_memberships_role_key_roles")
        ),
    )

    # --- the approved-source register and ingested documents -----------------------------
    op.create_table(
        "sources",
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("authority", sa.String(64), nullable=False),
        sa.Column("doc_type", sa.Text),
        sa.Column("version_label", sa.Text),
        sa.Column("issued", sa.Text),
        sa.Column("status_note", sa.Text),
        sa.Column(
            "use_cases",
            postgresql.ARRAY(sa.String),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("licence_status", sa.Text, nullable=False),
        sa.Column("reuse_basis", sa.Text),
        sa.Column("attribution", sa.Text),
        sa.Column("eligible", sa.Boolean, nullable=False),
        sa.Column("hold_reason", sa.Text),
        sa.Column("landing_page", sa.Text),
        sa.Column("pdf_url", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("source_id", name=op.f("pk_sources")),
        sa.UniqueConstraint("source_id", "eligible", name=op.f("uq_sources_source_id_eligible")),
        sa.CheckConstraint(
            "tier IN ('core', 'deferred', 'hold')", name=op.f("ck_sources_tier_values")
        ),
        sa.CheckConstraint(
            "eligible OR tier = 'hold'", name=op.f("ck_sources_ineligible_must_be_hold")
        ),
        sa.CheckConstraint(
            "NOT eligible OR (attribution IS NOT NULL AND attribution <> '')",
            name=op.f("ck_sources_eligible_needs_attribution"),
        ),
    )

    op.create_table(
        "documents",
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), server_default=NEW_UUID, nullable=False
        ),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("eligible", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_documents")),
        sa.UniqueConstraint("source_id", name=op.f("uq_documents_source_id")),
        sa.ForeignKeyConstraint(
            ["source_id", "eligible"],
            ["sources.source_id", "sources.eligible"],
            name=op.f("fk_documents_source_id_eligible_sources"),
        ),
        sa.CheckConstraint("eligible", name=op.f("ck_documents_source_must_be_eligible")),
    )

    op.create_table(
        "document_versions",
        sa.Column(
            "version_id", postgresql.UUID(as_uuid=True), server_default=NEW_UUID, nullable=False
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("version_label", sa.Text),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("page_count", sa.Integer),
        sa.Column("text_layer", sa.String(16)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("version_id", name=op.f("pk_document_versions")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.document_id"],
            name=op.f("fk_document_versions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name=op.f("uq_document_versions_document_id_version_number"),
        ),
        sa.UniqueConstraint(
            "document_id", "sha256", name=op.f("uq_document_versions_document_id_sha256")
        ),
        sa.CheckConstraint(
            "version_number >= 1", name=op.f("ck_document_versions_version_number_positive")
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_versions_sha256_format")
        ),
        sa.CheckConstraint(
            f"sha256 <> '{EMPTY_INPUT_SHA256}'",
            name=op.f("ck_document_versions_sha256_not_empty_input"),
        ),
        sa.CheckConstraint("byte_size > 0", name=op.f("ck_document_versions_byte_size_positive")),
        sa.CheckConstraint(
            "text_layer IS NULL OR text_layer IN ('yes', 'partial', 'no')",
            name=op.f("ck_document_versions_text_layer_values"),
        ),
    )

    op.create_table(
        "project_documents",
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("project_id", "document_id", name=op.f("pk_project_documents")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name=op.f("fk_project_documents_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.document_id"],
            name=op.f("fk_project_documents_document_id_documents"),
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "chunks",
        sa.Column(
            "chunk_id", postgresql.UUID(as_uuid=True), server_default=NEW_UUID, nullable=False
        ),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("page_start", sa.Integer, nullable=False),
        sa.Column("page_end", sa.Integer, nullable=False),
        sa.Column("section", sa.Text),
        sa.Column(
            "chunk_type", sa.String(16), server_default=sa.text("'paragraph'"), nullable=False
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer),
        sa.Column("char_start", sa.Integer),
        sa.Column("char_end", sa.Integer),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("embedding_model", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("chunk_id", name=op.f("pk_chunks")),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.version_id"],
            name=op.f("fk_chunks_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "version_id", "chunk_index", name=op.f("uq_chunks_version_id_chunk_index")
        ),
        sa.CheckConstraint("page_start >= 1", name=op.f("ck_chunks_page_start_positive")),
        sa.CheckConstraint("page_end >= page_start", name=op.f("ck_chunks_page_range")),
        sa.CheckConstraint(
            "chunk_type IN ('paragraph', 'heading', 'table', 'caption', 'list')",
            name=op.f("ck_chunks_chunk_type_values"),
        ),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)",
            name=op.f("ck_chunks_embedding_with_model"),
        ),
        sa.CheckConstraint(
            "char_start IS NULL OR char_end IS NULL OR char_end >= char_start",
            name=op.f("ck_chunks_char_range"),
        ),
    )
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # --- audit trail ---------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column(
            "event_id", postgresql.UUID(as_uuid=True), server_default=NEW_UUID, nullable=False
        ),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64)),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("outcome", sa.String(32), server_default=sa.text("'SUCCESS'"), nullable=False),
        sa.Column(
            "details", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index(
        "ix_audit_events_project_id_created_at", "audit_events", ["project_id", "created_at"]
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])

    # Audit records can be added but never changed, deleted or truncated. (A database owner can
    # still drop the triggers; a restricted application role is a deployment step.)
    op.execute(
        """
        CREATE FUNCTION audit_events_block_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_change()
        """
    )

    # A stored original is never edited: its fingerprint, location and size are fixed.
    op.execute(
        """
        CREATE FUNCTION document_versions_block_original_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.sha256 IS DISTINCT FROM OLD.sha256
               OR NEW.object_key IS DISTINCT FROM OLD.object_key
               OR NEW.byte_size IS DISTINCT FROM OLD.byte_size THEN
                RAISE EXCEPTION
                    'document_versions: the stored original cannot be changed; add a new version'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_versions_original_is_fixed
        BEFORE UPDATE ON document_versions
        FOR EACH ROW EXECUTE FUNCTION document_versions_block_original_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS document_versions_original_is_fixed ON document_versions")
    op.execute("DROP FUNCTION IF EXISTS document_versions_block_original_change()")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_change()")

    op.drop_table("audit_events")
    op.drop_table("chunks")
    op.drop_table("project_documents")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("sources")
    op.drop_table("memberships")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("roles")
