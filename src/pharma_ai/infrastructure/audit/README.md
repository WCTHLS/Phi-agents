# Audit & Compliance Infrastructure

Provides an append-only, tamper-evident audit logging service to support 21 CFR Part 11 and GxP compliance.

## Key Responsibilities

- **Access & Authentication Tracking:** Log scientist logins, workspace sessions, and authorization checks via Keycloak OIDC.
- **Source Provenance:** Maintain cryptographic hashes, version IDs, and retrieval timestamps for every document and chunk used in AI responses.
- **Scientist Sign-offs:** Record human verification, approval timestamps, and rationale for all generated report drafts, study proposals, and risk evaluations.
- **Query & Evidence Logging:** Store query prompts, retrieved context IDs, and model parameters without storing unredacted sensitive confidential data.
