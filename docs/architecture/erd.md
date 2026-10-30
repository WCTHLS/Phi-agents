# Database schema v1 (entity-relationship diagram)

Sprint task S1-07. The migration `migrations/versions/0001_schema_v1.py` is the authoritative definition; `src/pharma_ai/infrastructure/db/models.py` mirrors it, and a test fails if the two differ.

## Diagram

```mermaid
erDiagram
    roles ||--o{ memberships : "role of"
    users ||--o{ memberships : "has"
    projects ||--o{ memberships : "has members"
    projects ||--o{ project_documents : "may use"
    documents ||--o{ project_documents : "shared with"
    sources ||--o| documents : "ingested as"
    documents ||--o{ document_versions : "has versions"
    document_versions ||--o{ chunks : "split into"

    roles {
        string role_key PK "scientist, reviewer, qa, admin"
        text description
    }
    users {
        uuid user_id PK
        text subject UK "login token sub claim"
        text email
        text display_name
    }
    projects {
        string project_id PK "for example PRJ-001"
        string name
        string therapeutic_area
        string dosage_form
    }
    memberships {
        string project_id PK, FK
        uuid user_id PK, FK
        string role_key PK, FK
    }
    sources {
        text source_id PK "manifest doc_id"
        text title
        string authority "ICH, FDA ..."
        text licence_status
        text attribution "shown with citations"
        boolean eligible "cleared for ingestion"
        string tier "core, deferred, hold"
    }
    documents {
        uuid document_id PK
        text source_id FK, UK
        boolean eligible "always true"
    }
    document_versions {
        uuid version_id PK
        uuid document_id FK
        int version_number
        string sha256 "fingerprint of the original"
        text object_key "where the original is stored"
        bigint byte_size
        int page_count
        string text_layer "yes, partial, no"
    }
    project_documents {
        string project_id PK, FK
        uuid document_id PK, FK
    }
    chunks {
        uuid chunk_id PK
        uuid version_id FK
        int chunk_index
        int page_start
        int page_end
        text section
        string chunk_type
        text text
        vector embedding "384 numbers"
        string embedding_model
    }
    audit_events {
        uuid event_id PK
        string actor_id "stable subject ID from the login token"
        string actor_email
        string actor_role "all roles, joined for display"
        string[] actor_roles
        string project_id "plain text, no foreign key"
        string action
        string resource_type
        string resource_id
        string resource_version
        string outcome "SUCCESS, DENIED or FAILURE"
        jsonb details
    }
```

## How the rules are enforced by the database itself

| Rule | Where it comes from | How the database enforces it |
|---|---|---|
| Only documents verified as reusable may be ingested | ADR-002, PRD FR-08 | `documents` points at `sources (source_id, eligible)` and has a check that `eligible` is true. A document for an ineligible source is refused, and a source cannot be made ineligible while a document from it exists. |
| Held documents can never be in the core corpus | ADR-002 | `sources` check: a source that is not eligible must have `tier = 'hold'`. |
| Every eligible source has attribution text | ADR-002 (citations show attribution) | `sources` check: eligible rows need non-empty `attribution`. |
| A stored original has a real fingerprint | PRD FR-06 | `document_versions` checks: `sha256` is 64 lowercase hex characters and is not the fingerprint of empty input (`e3b0c442...`). |
| Originals are never edited | PRD ALCOA+ "original" | A trigger refuses any change to `sha256`, `object_key` or `byte_size` of a stored version. A revision is a new version. |
| Each chunk belongs to one version, in order | S2-06 | Unique `(version_id, chunk_index)`; page range check (`page_end >= page_start`). |
| A vector always records which model made it | ADR-003 | Check: `embedding` and `embedding_model` are both set or both empty. |
| Audit records cannot be altered | PRD FR-30, NFR-07 | Triggers refuse UPDATE, DELETE and TRUNCATE on `audit_events`. |
| Audit outcomes are a fixed set | S1-11 | Check: `outcome` is SUCCESS, DENIED or FAILURE. |
| Audit records are written by the server | S1-11, ADR-005 | The API records events from the verified login token; there is no endpoint for a client to write one. |
| Retrieval is limited to the caller's projects | PRD FR-10, NFR-06 | `ChunkRepository.search_nearest` applies the project filter and the eligibility check in the same SQL statement as the vector search. No allowed projects means no results. |

## Limits to know about

- **Triggers are not full protection.** The database owner can still drop a trigger. A restricted application role (insert-only on `audit_events`) is a deployment step, not part of this migration.
- **Project access comes from the login token for now.** The `users` and `memberships` tables exist, but the API still reads project membership from token claims (S1-09/S1-10). Switching it to these tables is a separate change.
- **`chunk_type` values** (`paragraph`, `heading`, `table`, `caption`, `list`) are a proposal for the chunking work in Sprint 2.
- **The vector size (384) is fixed** by the column. A model with a different size needs a new column or a re-embedding migration.
- **Search with a strict filter:** an approximate (HNSW) index can return fewer than the requested number of results when the permission filter removes many of the nearest candidates. At this corpus size that is not a concern, but it should be re-checked when the corpus grows (UNVERIFIED for the installed pgvector version).
