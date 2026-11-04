# Phase 0 decision log and open questions (S1-03)

Last updated: 3 Nov 2026 (D13 added). This is the one place to see what the project has decided, what is still open, who has to decide it, and by when.

**How to read it**
- **Decided** means a person or the team made the call and it is written down somewhere in the repo. It is **not** a client approval unless the row says so.
- **Proposed** means written down but not yet accepted by anyone with authority.
- **Open** means no answer is recorded in the repo. Someone may have answered it elsewhere; if so, say so and this log will be updated.
- **Overdue** means the needed-by date has passed and the repo holds no answer.
- **UNVERIFIED** marks anything stated here without a source we could check.
- Owners are *proposed* roles, not assignments: nobody has agreed to them yet.

## A. Decisions made so far

| ID | Decision | Status | Recorded in | Caveat |
|---|---|---|---|---|
| D1 | Only documents marked `eligible = yes` in the source manifest may be ingested. Held documents stay out until someone with authority approves them. | Decided for the Sprint 2 test corpus (team decision) | [ADR-002](../architecture/decisions/ADR-002-corpus-licence-rule.md), `data/sources/corpus_manifest.csv` | Not ratified by the client, counsel or the publishers. Nobody on the team is qualified to judge licences, so the rule is deliberately conservative. |
| D2 | Extract text with Apache Tika (pypdf as fallback). Embeddings: `BAAI/bge-small-en-v1.5` for production, `all-MiniLM-L6-v2` for CI and development. | Decided for S2-03 and S2-06 | [ADR-003](../architecture/decisions/ADR-003-extraction-and-embedding-selection.md) | The licences are as stated in the ADR; nobody has independently checked them. One mismatch: the ADR names `tika/tika:2.9.2-full`, but compose and CI now pin `apache/tika:4.2.0`, the version the page reader was tested against (ADR-007 supersedes the version). `models_config.py` now defaults to the bge-small model (aligned 3 Nov, ADR-008). The ADR also speaks of a "GxP enclave", which nothing in the project provides. |
| D3 | Development uses public, open-source documents only. No regulated or client data. | Manager's instruction (from the project handoff) | Not written anywhere else in the repo | Needs a one-line entry in `docs/security/data-handling.md`. |
| D4 | The interface is a web workspace, not mobile. | Default taken from the PRD (FR-01) | Handoff brief | Confirm; no one has said mobile is wanted. |
| D5 | Database schema v1: ten tables, with the licence rule enforced inside the database. | Proposed | [ADR-004](../architecture/decisions/ADR-004-database-schema-v1.md) | Needs review by the platform owner. |
| D6 | The audit trail is written by the server, stored in the database, and cannot be edited or deleted. | Proposed | [ADR-005](../architecture/decisions/ADR-005-audit-trail.md) | Needs review by the frontend and platform owner. |
| D7 | Logs are structured and redacted; they never carry tokens, passwords, e-mail addresses, or question, answer or document text. | Proposed | [ADR-006](../architecture/decisions/ADR-006-structured-logging.md) | Strict default. The client has not said what may appear in logs (open question Q11). |
| D8 | Technology stack (FastAPI, PostgreSQL with pgvector, React with Vite, Keycloak, Tika, and so on). | Proposed for the MVP | [ADR-001](../architecture/decisions/ADR-001-initial-stack.md) | The ADR says "confirm with manager/team during Phase 0". No confirmation is recorded. |
| D9 | Demo output is labelled "not yet reviewed by a domain expert" until a scientist has reviewed the evidence. | Decided in the handoff | Handoff brief | Not done: the UI still shows an answer marked "verified" when a request fails, and a "✓ VERIFIED BY CITATIONS" badge that only means words matched. With no domain expert this sprint (D11) the label matters more: every answer shown must say it is not reviewed. |
| D10 | Original documents are kept in a local folder behind an `ObjectStore` interface; ingestion stores the file, its SHA-256 fingerprint and page-by-page text; only administrators upload; scans are kept but marked as having no text. | Proposed (built 2 Nov) | [ADR-007](../architecture/decisions/ADR-007-document-ingestion-and-storage.md) | Needs review by the platform owner. A local folder suits one machine only. |
| D11 | No domain expert and no external approver is available this sprint, so the team works with what it has. (a) The 13 eligible documents are the Sprint 2 test corpus on the team's own approval (D1). (b) The 7 held documents stay held, because nobody can release them. (c) The golden questions stay machine-checked only. (d) Every demo output is labelled "not yet reviewed by a domain expert" (D9) and is not presented as scientifically validated. (e) Questions that need expert judgment are out of scope. | Decided 2 Nov by the backend developer, for Sprint 2 | This log | Covers Sprint 2 and public documents only (D3). The licence basis is still a team check, not legal advice. Revisit when a domain expert, the client or the client's legal contact is available, and before any client or regulated data is used. |
| D12 | Ingested text is cut into passages of at most 500 tokens (100 repeated from the previous one), never across a section heading, with page numbers and running headers removed. Each passage is embedded with `BAAI/bge-small-en-v1.5` (384 numbers) and records the model. Indexing runs from a script for now; a scan with no text is skipped and reported. | Proposed (built 3 Nov) | [ADR-008](../architecture/decisions/ADR-008-chunking-and-embeddings.md) | Needs review by the platform owner. A first check on the draft golden questions found the right passage in the top 5 for 6 of the 9 that can be scored (the right document for 8 of 9; saved as `evals/results/baseline-dense.json`); this is not a verdict on answer quality, and no domain expert has reviewed anything (D11). Section names on citations are approximate; page numbers and quoted text are reliable. |
| D13 | Search combines search by meaning and PostgreSQL keyword search, merged by rank. Both searches follow the same visibility rules inside one SQL query (caller's projects, eligible source, named embedding model, newest version). | Proposed (built 3 Nov) | [ADR-009](../architecture/decisions/ADR-009-hybrid-retrieval.md) | Needs review by the platform owner. On the 9 scoreable golden questions hybrid found the right passage at rank 1 for 6 (meaning alone: 5) and the right document in the top 3 for 9 (8); that is two questions, not proof. No endpoint calls it yet, so nothing reaches a model through it (S2-09). |

## B. Open questions

| ID | Question | Proposed owner | Needed by | Status | What we know, and what we do meanwhile |
|---|---|---|---|---|---|
| Q1 | Which sign-in service: Keycloak or Entra ID? | Manager, with the client's IT | 27 Oct | **Overdue** | The API is written for either: it reads roles from the Keycloak claim (`realm_access.roles`) and the Entra ID claim (`roles`). Entra ID would also need `OIDC_JWKS_URL` set, and either service needs a custom `projects` claim added. The compose file ships a Keycloak with no users, and the UI has no real sign-in yet. Until this is decided the project uses development tokens, which are refused outside local and test environments. Blocks: a real UI sign-in (S1-09). |
| Q2 | Who approves the source documents, and which? | Manager and the client's legal contact | 30 Oct | **Closed for Sprint 2** (D11): no external approval needed; the team's test corpus stands (D1) | 13 documents are ready in the corpus, 2 are deferred, 7 are held. See the [checkpoint sheet](../sprint-reviews/sprint-2-planning-checkpoint.md). |
| Q3 | What is the licence of the language model, and which model is it? | Manager, with the client | 2 Nov (today) | **Open** | `models_config.py` names `Qwen/Qwen2.5-14B-Instruct`, but as a default, not a decision. Its licence is not recorded anywhere in the repo (from memory it is Apache 2.0: **UNVERIFIED**, check the model card). The reranker `BAAI/bge-reranker-base` is also unrecorded. Blocks: the language-model step of the workflow (S2-09). |
| Q4 | How is the language model run during development? | Backend developer | Before S2-09 starts (planned 5 Nov) | **Open** | The developer laptop is Windows with 16 GB RAM and no GPU, so the planned server (vLLM) cannot run on it. Options: WSL2, a shared dev server, or a hosted model on public documents only. Which one is allowed depends on Q6. |
| Q5 | Who is the scientist for the demo and the question review? | Manager | Sprint 2 demo, Fri 6 Nov | **Closed for Sprint 2** (D11): no domain expert available | The 16 draft golden questions are machine-checked (11 of 16 pass), not scientist-approved. Until a scientist reviews them, demo output carries the "not yet reviewed" label (D9). |
| Q6 | Azure or on-premises? May content go to a cloud model? | Manager, with the client | Before any client document is used | **Open** | Not needed while only public documents are used (D3). The design keeps extraction and the model behind adapters so either answer works. [`docs/security/data-handling.md`](../security/data-handling.md) lists this as a question to ask. |
| Q7 | Which product is the pilot? | Manager, with the scientist | Before product-specific sources are chosen | **Open** | Product-specific guidance and labels cannot be picked without it. The corpus has none today. |
| Q8 | Where are original files stored: local folder or an S3-style store such as SeaweedFS? | Backend developer, with the platform developer | 2 Nov, before S2-03 starts | **Decided 2 Nov** (by the backend developer): a local folder behind a small interface | Recorded in [ADR-007](../architecture/decisions/ADR-007-document-ingestion-and-storage.md) and built. Another store only has to implement three methods. The platform developer has not reviewed it yet. |
| Q9 | What are the Indian Pharmacopoeia licence terms? | Manager and the client's legal contact | Not stated | **Open** | Treated as licensed, so it stays out of the corpus. |
| Q10 | Which bioavailability / bioequivalence guidance does CDSCO apply now? | Domain expert | Not stated | **Deferred** (needs a domain expert; not in Sprint 2) | The 2005 CDSCO guideline is on hold: CDSCO's policy bars copying without permission, and no official copy was found online. |
| Q11 | What must be logged for traceability, and what must never appear in logs? | Manager and the client's security contact | Before real documents are ingested | **Open** | Logs are strict by default (D7). Item taken from `docs/security/data-handling.md`. |
| Q12 | Can the four held ICH guidelines (Q1A, Q3B, Q6A, Q8) be used? | Manager, with the client's legal contact (or ICH) | Next consolidated ICH Q1 release, expected around Nov 2026 | **Held for Sprint 2** (D11): nobody is available to release them | Their PDFs carry no legal notice, so the right to reuse is not stated. See the checkpoint sheet: the "Open Knowledge Sources" document wrongly lists them as ready to ingest. |
| Q13 | Is the proposed Sprint 2 scope adopted? | Both developers and the manager | 2 Nov (today) | **Open** | Described in the checkpoint sheet. |
| Q14 | What time zone do the ceremonies (09:30, 10:00, 14:00) use? | Manager | Before the next ceremony | **Open** | The plan gives times without a zone; Arizona and India differ by about 12.5 hours. |
| Q15 | Who is the client? | Manager | Before anything client-specific is written | **Open** | Not confirmed. Do not hard-code a client name anywhere. |

## C. What each open question blocks

| Blocked work | Waiting for |
|---|---|
| Real sign-in in the UI (S1-09) | Q1 |
| Ingesting the approved set for real (S2-03 is built) | The document approval in Q2 |
| Language-model step (S2-09) | Q3, Q4 |
| A demo that is honest about its limits | Labelling every output "not yet reviewed by a domain expert" (D9); the UI does not do this yet |
| Using any client document, or a cloud model | Q6, Q11 |
| Using the four held ICH guidelines | Q12 |

## D. Other documents that disagree with the project

Found while writing this log. None has been edited.

- `docs/sprint-reviews/sprint-1-review-and-demo.md` claims things the project cannot show: verified document hashes, a "GxP enclave", sample trial results.
- `docs/Open Knowledge Sources for Literature and Regulatory Research.md` (from `main`) disagrees with the licence rule in four places. See the checkpoint sheet.
- `docs/implementation/task-board.md` still lists every Phase 0 item as not done.
- The sprint-plan workbook marks S1-03, S1-05 and S1-09 as Done. S1-03 (this log) and S1-05 (structured logging) were only completed on 2 Nov, and S1-09 is development tokens only, with no real single sign-on.
- The plan says "Podman" for the dev environment (S1-06); the project uses Docker.
