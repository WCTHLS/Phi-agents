# ADR-004: Database schema v1 and how the licence rule is enforced

- **Status:** Proposed (S1-07). Needs review by the developer who owns the platform and DevOps setup, because it replaces the tables in `scripts/init-db.sql` and changes how the database is created.
- **Decision:**
  - Use SQLAlchemy 2 models and Alembic migrations (up and down) on PostgreSQL with the pgvector extension. The migration `migrations/versions/0001_schema_v1.py` is the authoritative schema; a test fails if the models differ from it. Tables and rules are listed in [erd.md](../erd.md).
  - `sources` replaces `approved_sources`. It is filled from `data/sources/corpus_manifest.csv`, so the database and the manifest cannot disagree about which documents are eligible (ADR-002). No document hashes are seeded: a real SHA-256 exists only once a file has been ingested.
  - The database enforces ADR-002 itself. A `documents` row must point at a source whose `eligible` flag is true (composite foreign key plus a check), so application code cannot ingest a held document by mistake.
  - Stored originals are fixed: a check rejects malformed hashes and the hash of empty input, and a trigger rejects any change to the hash, location or size of a stored version.
  - `audit_events` is append-only: triggers refuse update, delete and truncate. The table keeps the columns of the existing seed table and adds `outcome`.
  - Project access for retrieval is a link table (`project_documents`). Search filters by project and eligibility in the same SQL statement as the vector search (PRD FR-10).
  - Embeddings are 384-dimensional, matching the model chosen in ADR-003 (`BAAI/bge-small-en-v1.5`), with an HNSW cosine index. Each vector records the model that produced it.
  - `users`, `roles` and `memberships` exist, but the API still reads project membership from login-token claims. Moving it to these tables is a later, separate change.
- **Why:** The sprint plan asks for versioned migrations, access metadata on chunks and an append-only audit table. Putting the licence and integrity rules into the database means a coding mistake in the ingestion pipeline (Sprint 2) is refused at the last step, instead of silently putting an ineligible document in front of scientists.
- **Trade-offs:**
  - A source cannot be made ineligible while documents from it exist; those documents must be removed first. This is deliberate, because indexed text from a withdrawn source should not remain searchable.
  - Triggers stop the application and ordinary users, not a database owner. A restricted application role is a deployment step.
  - The vector size is fixed in the column, so a model with a different size needs a new column or a re-embedding migration.
  - An approximate (HNSW) index can return fewer results than requested when the permission filter removes many candidates. This is acceptable at the current corpus size and should be re-checked as it grows (UNVERIFIED for the installed pgvector version).
  - `init-db.sql` must no longer create tables, or the migration would fail on a database created from it. Existing local database volumes need to be recreated.
- **Revisit when:** the audit service moves to the database (S1-11); project membership moves from token claims to the tables; ingestion (S2-03) or chunking (S2-06) needs columns this schema lacks; the corpus grows enough for search quality or index behaviour to matter.
