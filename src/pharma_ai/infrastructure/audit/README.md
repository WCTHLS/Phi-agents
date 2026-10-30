# Audit trail

An append-only record of who did what, to which object, when, and with what result. Design and limits: [ADR-005](../../../../docs/architecture/decisions/ADR-005-audit-trail.md).

## How it works

- **Routes write events through a recorder.** `audit: AuditRecorderDep` gives a route a recorder that already knows the caller (from the verified login token). The route says only what happened: `audit.success("PROJECT_VIEWED", "project", resource_id=...)`.
- **There is no way for a client to write an event.** Refused requests (403) are recorded automatically by a handler in `main.py`.
- **Two backends** (`AUDIT_BACKEND`): `database` (default) writes to the append-only `audit_events` table; `memory` is for tests and database-less local runs and is refused unless `ENVIRONMENT` is `local` or `test`.
- **Fail closed:** if the trail cannot be written the API answers 503 and returns no data.

## Files

- `service.py`: the interface, the in-memory backend and the choice of backend.
- `database.py`: the database backend.
- `recorder.py`: what routes use, plus `fingerprint_text` (research questions are stored as a fingerprint, not as text).

## Not covered yet

- Approvals and sign-offs: they must be written by the server-side review gate (Sprint 2), in the same transaction as the approval.
- Tamper evidence (hash chaining) and a restricted database role: the triggers protect against the application, not against a database owner.
