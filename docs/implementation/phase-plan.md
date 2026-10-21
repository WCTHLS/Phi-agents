# Implementation plan

Estimates are planning ranges for two developers, not commitments. They exclude waiting time for access, source permissions, domain review, and client integrations.

| Phase | Target effort | Deliverable | Exit gate |
|---|---:|---|---|
| 0. Align and prepare | 3–5 working days | Agreed MVP, roles, approved-source policy, acceptance tests, runnable scaffold | Scope/access/security/ownership accepted |
| 1. Trusted research | 7–10 working days | Ingest curated approved documents, retrieve relevant passages, answer with source citations | Scientist verifies evidence and citations on a test set |
| 2. Formulation and DOE | 10–15 working days | Initial formulation guidance template and reproducible basic DOE/statistical workflow | Scientific/domain review approves methods and limitations |
| 3. Experiment feedback and risk | 7–10 working days | Capture structured experimental results and produce reviewable risk/evidence summaries | Results and rationale reviewed by scientists |
| 4. Documentation integration | 8–12 working days | Source-linked report drafts; one approved export/integration path | Draft traceability and versioning accepted |
| 5. Evaluate and expand | 5–8 working days | Evaluation report, reliability/security checks, monitoring and next roadmap | Team agrees go/no-go for expanded pilot |

## Day 1 — 15 October 2026

1. Read all architecture diagrams together and agree the minimum demonstration goal.
2. Confirm target users, first user journey, environment (Windows/CPU), allowed sources, and whether any client data may be used.
3. Create Git repository, add this scaffold, install dependencies, run `/health`, and run tests.
4. Create issues for Phase 0 decisions and Phase 1 backlog; assign one owner and acceptance criteria to each.

## Day 2

1. Review the source policy with the manager/domain contact; agree a small source set and permitted sample documents.
2. Define the first 10–20 scientist questions and expected evidence/answer criteria.
3. Agree the document metadata schema: source name, URL/identifier, publication/update date, access rights, version/hash, ingestion time, page/section, and extraction status.

## Day 3

1. Implement and test ingestion for the approved sample documents.
2. Preserve document/page metadata and fail closed when source permissions are unclear.
3. Compare extraction quality on text PDFs and scanned examples; add OCR only if scans are in scope.

## Day 4

1. Implement chunking/embeddings and pgvector retrieval using CPU-compatible settings.
2. Evaluate retrieval against the agreed scientist questions and record failures.

## Day 5

1. Add Haystack pipeline and grounded answer response schema with citations/source links.
2. Run the baseline evaluation, review failures with the scientist/domain contact, and demo only what passes the agreed checks.

**Do not begin Phase 2 until Phase 1 can retrieve evidence and cite it reliably on an agreed small test set.**
