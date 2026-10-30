"""Database tests that need no running database: models, URL handling and the manifest loader."""

from pathlib import Path

import pytest
from pgvector.sqlalchemy import Vector

from pharma_ai.infrastructure.db.models import (
    EMBEDDING_DIM,
    EMPTY_INPUT_SHA256,
    ROLE_KEYS,
    Base,
)
from pharma_ai.infrastructure.db.seed import DEFAULT_MANIFEST, read_manifest
from pharma_ai.infrastructure.db.session import to_sqlalchemy_url

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "roles",
    "users",
    "projects",
    "memberships",
    "sources",
    "documents",
    "document_versions",
    "project_documents",
    "chunks",
    "audit_events",
}


# --- models ----------------------------------------------------------------------------------


def test_schema_has_the_expected_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_embedding_column_is_384_dimensions() -> None:
    # 384 matches BAAI/bge-small-en-v1.5 chosen in ADR-003.
    column = Base.metadata.tables["chunks"].c.embedding
    assert EMBEDDING_DIM == 384
    assert isinstance(column.type, Vector)
    assert column.type.dim == 384


def test_empty_input_hash_constant_is_the_real_sha256_of_nothing() -> None:
    import hashlib

    assert hashlib.sha256(b"").hexdigest() == EMPTY_INPUT_SHA256


def test_role_keys_match_the_proposed_roles() -> None:
    assert ROLE_KEYS == ("scientist", "reviewer", "qa", "admin")


def test_every_table_is_described_in_the_erd_document() -> None:
    erd = (REPO_ROOT / "docs" / "architecture" / "erd.md").read_text(encoding="utf-8")
    missing = [name for name in EXPECTED_TABLES if name not in erd]
    assert missing == []


# --- database URL ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@localhost:5432/db", "postgresql+psycopg://u:p@localhost:5432/db"),
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_to_sqlalchemy_url_selects_the_psycopg_driver(given: str, expected: str) -> None:
    assert to_sqlalchemy_url(given) == expected


# --- manifest loader -------------------------------------------------------------------------


def test_real_manifest_loads_all_22_rows() -> None:
    rows = read_manifest(DEFAULT_MANIFEST)
    assert len(rows) == 22
    assert len({r["source_id"] for r in rows}) == 22  # no duplicate IDs


def test_real_manifest_rows_satisfy_the_database_rules() -> None:
    """The same rules the database enforces, so a bad manifest edit is caught before seeding."""
    for row in read_manifest(DEFAULT_MANIFEST):
        assert row["tier"] in {"core", "deferred", "hold"}, row["source_id"]
        # An ineligible source must be on hold, never core or deferred.
        assert row["eligible"] or row["tier"] == "hold", row["source_id"]
        # An eligible source needs attribution text to show next to citations (ADR-002).
        assert not row["eligible"] or row["attribution"], row["source_id"]


def test_real_manifest_eligibility_matches_tiers() -> None:
    rows = read_manifest(DEFAULT_MANIFEST)
    held = [r["source_id"] for r in rows if r["tier"] == "hold"]
    assert all(not r["eligible"] for r in rows if r["tier"] == "hold")
    assert all(r["eligible"] for r in rows if r["tier"] in {"core", "deferred"})
    # The documents ADR-002 says must stay out.
    assert {"ICH-Q8R2", "ICH-Q1AR2", "ICH-Q3BR2", "ICH-Q6A"} <= set(held)


def test_manifest_row_is_mapped_to_source_columns() -> None:
    row = next(r for r in read_manifest(DEFAULT_MANIFEST) if r["source_id"] == "ICH-Q3DR2")
    assert row["authority"] == "ICH"
    assert row["eligible"] is True
    assert row["tier"] == "core"
    assert row["use_cases"] == ["UC-05"]
    assert "ICH" in row["attribution"]
    assert row["hold_reason"] is None  # blank cells become None


def test_multiple_use_cases_are_split() -> None:
    row = next(r for r in read_manifest(DEFAULT_MANIFEST) if r["source_id"] == "ICH-Q9R1")
    assert row["use_cases"] == ["UC-03", "UC-05"]


HEADER = (
    "doc_id,title,authority,doc_type,version,issued,status,use_cases,why_included,test_targets,"
    "licence_status,reuse_basis,attribution,eligible,hold_reason,landing_page,pdf_url,flags,tier\n"
)


@pytest.mark.parametrize(
    ("eligible_cell", "expected"),
    [
        ("yes", True),
        ("YES", True),
        (" yes ", True),
        ("no", False),
        ("", False),
        ("y", False),
        ("true", False),
        ("1", False),
    ],
)
def test_only_the_value_yes_counts_as_eligible(
    tmp_path: Path, eligible_cell: str, expected: bool
) -> None:
    csv_file = tmp_path / "m.csv"
    csv_file.write_text(
        HEADER + f"X-1,T,AUTH,,v1,,,UC-01,,,lic,,attr,{eligible_cell},,,,,core\n", encoding="utf-8"
    )
    assert read_manifest(csv_file)[0]["eligible"] is expected
