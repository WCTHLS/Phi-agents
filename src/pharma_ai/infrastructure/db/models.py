"""Database tables for schema v1 (sprint task S1-07).

Plain-language map of the tables:

- ``roles``, ``users``, ``memberships``: who can work on which project, and in what role.
- ``projects``: one development programme (for example one product being developed).
- ``sources``: the register of documents we may use (one row per entry in
  ``data/sources/corpus_manifest.csv``). It records the licence findings and whether a document
  is ``eligible`` (cleared for ingestion, see ADR-002).
- ``documents``: a source that has actually been ingested. The database refuses a document for
  a source that is not eligible, so the licence rule cannot be bypassed by application code.
- ``document_versions``: one stored original file (a PDF) with its SHA-256 fingerprint. Originals
  are never edited; a new revision is a new version.
- ``project_documents``: which projects may see which documents (the access rule for retrieval).
- ``chunks``: small searchable pieces of a document version, each with an embedding. An embedding
  is a list of 384 numbers that represents the meaning of the text, so that similar text can be
  found by comparing numbers (done by the pgvector extension).
- ``audit_events``: the append-only record of who did what and when. Rows cannot be changed or
  deleted (enforced by database triggers created in the migration).

The migration in ``migrations/versions/`` is the authoritative definition. A test compares it with
these models so the two cannot drift apart.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Embedding size. Matches ADR-003 (BAAI/bge-small-en-v1.5, 384 dimensions). Changing the model
# to one with a different size needs a new column or a re-embed migration.
EMBEDDING_DIM = 384

# SHA-256 of an empty input. A stored original with this fingerprint means the file was empty or
# the hash was never computed, so the database refuses it.
EMPTY_INPUT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

ROLE_KEYS = ("scientist", "reviewer", "qa", "admin")
SOURCE_TIERS = ("core", "deferred", "hold")
CHUNK_TYPES = ("paragraph", "heading", "table", "caption", "list")
TEXT_LAYER_VALUES = ("yes", "partial", "no")

# Fixed constraint names make migrations reversible and error messages readable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_NOW = text("now()")
_NEW_UUID = text("gen_random_uuid()")


class Role(Base):
    __tablename__ = "roles"

    role_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    # The "sub" claim of the login token: the identity provider's stable ID for the person.
    subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    __table_args__ = (UniqueConstraint("subject", name="uq_users_subject"),)


class Project(Base):
    __tablename__ = "projects"

    # Text IDs such as "PRJ-001": these are the values carried in login tokens.
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    therapeutic_area: Mapped[str] = mapped_column(String(128))
    dosage_form: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Membership(Base):
    """A person's role on a project. One person may hold several roles on one project."""

    __tablename__ = "memberships"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    role_key: Mapped[str] = mapped_column(ForeignKey("roles.role_key"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Source(Base):
    """One entry in the approved-source register (mirrors data/sources/corpus_manifest.csv)."""

    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(String(64))  # e.g. ICH, FDA
    doc_type: Mapped[str | None] = mapped_column(Text)
    version_label: Mapped[str | None] = mapped_column(Text)  # revision as printed on the document
    issued: Mapped[str | None] = mapped_column(Text)  # kept as text: the manifest uses "2024-01"
    status_note: Mapped[str | None] = mapped_column(Text)
    use_cases: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'::varchar[]")
    )
    licence_status: Mapped[str] = mapped_column(Text)
    reuse_basis: Mapped[str | None] = mapped_column(Text)
    # Text that must be shown next to any citation of this document (ADR-002).
    attribution: Mapped[str | None] = mapped_column(Text)
    # True only when the reuse terms were read from a primary source (ADR-002).
    eligible: Mapped[bool] = mapped_column(Boolean)
    hold_reason: Mapped[str | None] = mapped_column(Text)
    landing_page: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    # core = in the corpus; deferred = eligible but no correct file yet; hold = not eligible.
    tier: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    __table_args__ = (
        # Lets "documents" point at (source_id, eligible) so the database can check eligibility.
        UniqueConstraint("source_id", "eligible", name="uq_sources_source_id_eligible"),
        CheckConstraint("tier IN ('core', 'deferred', 'hold')", name="tier_values"),
        CheckConstraint("eligible OR tier = 'hold'", name="ineligible_must_be_hold"),
        CheckConstraint(
            "NOT eligible OR (attribution IS NOT NULL AND attribution <> '')",
            name="eligible_needs_attribution",
        ),
    )


class Document(Base):
    """A source that has been ingested. Only eligible sources can have a document."""

    __tablename__ = "documents"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    source_id: Mapped[str] = mapped_column(Text)
    # Always true. Together with the composite foreign key below it makes the database refuse a
    # document for an ineligible source, and refuse to make a source ineligible while documents
    # from it exist.
    eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("source_id", name="uq_documents_source_id"),  # one document per source
        ForeignKeyConstraint(
            ["source_id", "eligible"],
            ["sources.source_id", "sources.eligible"],
            name="fk_documents_source_id_eligible_sources",
        ),
        CheckConstraint("eligible", name="source_must_be_eligible"),
    )


class DocumentVersion(Base):
    """One stored original file. Never edited; a new revision is a new version."""

    __tablename__ = "document_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE")
    )
    version_number: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str | None] = mapped_column(Text)  # as printed, e.g. "Revision 2"
    sha256: Mapped[str] = mapped_column(String(64))  # fingerprint of the original file
    object_key: Mapped[str] = mapped_column(Text)  # where the original is stored
    byte_size: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    text_layer: Mapped[str | None] = mapped_column(
        String(16)
    )  # can text be selected? yes/partial/no
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id_version_number"
        ),
        UniqueConstraint("document_id", "sha256", name="uq_document_versions_document_id_sha256"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        CheckConstraint(f"sha256 <> '{EMPTY_INPUT_SHA256}'", name="sha256_not_empty_input"),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint(
            "text_layer IS NULL OR text_layer IN ('yes', 'partial', 'no')",
            name="text_layer_values",
        ),
    )


class ProjectDocument(Base):
    """Access rule: a project may retrieve passages only from documents linked to it."""

    __tablename__ = "project_documents"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Chunk(Base):
    """A searchable piece of one document version, with its embedding."""

    __tablename__ = "chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.version_id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)  # order within the version
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    chunk_type: Mapped[str] = mapped_column(String(16), server_default=text("'paragraph'"))
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)  # position in the extracted text
    char_end: Mapped[int | None] = mapped_column(Integer)
    # Empty until the embedding step has run. embedding and embedding_model are set together.
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("version_id", "chunk_index", name="uq_chunks_version_id_chunk_index"),
        CheckConstraint("page_start >= 1", name="page_start_positive"),
        CheckConstraint("page_end >= page_start", name="page_range"),
        CheckConstraint(
            "chunk_type IN ('paragraph', 'heading', 'table', 'caption', 'list')",
            name="chunk_type_values",
        ),
        CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)", name="embedding_with_model"
        ),
        CheckConstraint(
            "char_start IS NULL OR char_end IS NULL OR char_end >= char_start",
            name="char_range",
        ),
        Index("ix_chunks_version_id", "version_id"),
        # Fast nearest-neighbour search using cosine distance (HNSW is an approximate index).
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class AuditEvent(Base):
    """Append-only record of an action. Changes and deletes are blocked by triggers."""

    __tablename__ = "audit_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_NEW_UUID
    )
    actor_id: Mapped[str] = mapped_column(String(128))
    actor_role: Mapped[str] = mapped_column(String(64))
    # Plain text, no foreign key: an audit record must survive even if a project is removed.
    project_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32), server_default=text("'SUCCESS'"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    __table_args__ = (
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_project_id_created_at", "project_id", "created_at"),
        Index("ix_audit_events_action", "action"),
    )
