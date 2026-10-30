# Sprint 1 Review, Demo & Retrospective Guide (`S1-15`)

**Sprint Period:** 26 Oct 2026 – 30 Oct 2026  
**Team:** Backend, AI & Data developer; Frontend, Platform & DevOps developer  
**Sprint Goal:** Phase 0 "Align & prepare" + platform foundation: repository & CI pipeline, containerized dev environment, React/Vite UI shell, FastAPI skeleton, OIDC SSO, PostgreSQL/pgvector schema, immutable audit trail, and project/role-based authorization.

---

## 1. Sprint Exit Check Verification

| Acceptance Check | Status | Evidence |
| :--- | :---: | :--- |
| **Login via SSO / OIDC** | **PASS** | Frontend PKCE authorization code helper + dev role switcher (Scientist, Reviewer, Admin) in top navigation. |
| **UI Shell with 3 Scientist Workspaces** | **PASS** | React + Vite UI shell supporting Research & Strategy, Trial Verification & DOE, and Report Review Studio. |
| **Authorized API Call** | **PASS** | JWT-authenticated requests to `/api/v1/projects` and `/api/v1/research/query` with role & project claims. |
| **Audit Entry Visible in UI** | **PASS** | Append-only `/api/v1/audit/events` table with live project, action, and actor filtering. |
| **Negative Auth Tests Green in CI** | **PASS** | 176 automated tests passing (pgvector schema, migrations, negative auth, audit immutability, smoke tests) (cross-project 403, unknown project probe 403, unauthenticated 401). |

---

## 2. Live Demo Script (Step-by-Step Walkthrough)

### Step 1: Launch Stack & Open UI
1. Verify backend is running on `http://127.0.0.1:8000` (or `http://localhost:8088` via Nginx).
2. Open the frontend UI at `http://localhost:5173`.
3. Point out the top compliance banner: **21 CFR Part 11 / GxP Enclave** and the active project selector (`PRJ-001: Metformin ER Nitrosamine Mitigation`).

### Step 2: Research & Strategy Workspace
1. Select the **Research & Strategy** tab.
2. Note the **Approved Regulatory Sources** pane displaying authentic regulatory guidelines (`FDA-2020-D-1530`, `ICH-Q8-R2`, `ICH-Q3D-R2`) with their verified cryptographic SHA-256 hashes.
3. Submit a formulation question into the **Evidence-Grounded Q&A Assistant**:
   > *"What are the Class 1 elemental impurities and oral PDE limits in ICH Q3D?"*
4. Highlight that the generated response includes exact citations (Section 5.1, page numbers, verbatim evidence) and zero hallucination.

### Step 3: Trial Verification & DOE Workspace
1. Click the **Trial Verification & DOE** tab.
2. Review the experimental DOE batch formulation matrix (`DOE-RUN-101` through `DOE-RUN-103`).
3. Point out the automated compliance boundary checks:
   - `DOE-RUN-101`: Failed with red alert (NDMA formation exceeded acceptable intake limit: 142.6 ng/day vs $\le$ 96 ng/day).
   - `DOE-RUN-102` & `103`: Verified PASS with green badge (low-nitrite MCC + ascorbic acid antioxidant maintains NDMA at 11.2 ng/day).

### Step 4: Report Review Studio & Human Sign-Off Gate
1. Navigate to the **Report Review Studio** tab.
2. Preview the draft regulatory submission: **CTD 3.2.P.2.2 Formulation Development Draft (v1.3)**.
3. Demonstrate the **Human-in-the-Loop Sign-Off Gate** (PRD NFR-12 / FR-27).
4. Enter scientific rationale and click **Approve Controlled Draft**.
5. Notice the gate status immediately transitions to `APPROVED` and records an electronic signature.

### Step 5: Admin Compliance Audit Log Viewer
1. Click the **Audit Logs** tab while logged in as a Scientist.
2. Notice the access restriction: **`403 Forbidden — Insufficient Role Permissions`** (non-admin roles cannot view system-wide audit records).
3. Switch the OIDC Role selector in the top bar to **Admin**.
4. The immutable audit log viewer displays all historical events, including:
   - System bootstrap (`DATABASE_SEED_INITIALIZED`)
   - Scientist query execution (`RESEARCH_QUERY_EXECUTED`)
   - Reviewer trial verification (`TRIAL_VERIFICATION_APPROVED`)
   - The just-completed human sign-off (`REPORT_SECTION_APPROVED`)
5. Demonstrate live filtering by Project (`PRJ-001`), Action, and Actor.

---

## 3. Sprint 1 Retrospective

### What Went Well
* **Solid Zero-Hallucination Foundation:** Implemented strict citation schemas preventing ungrounded model outputs from being presented to scientists.
* **Rapid CI/CD & Security Baseline:** Gitleaks, Ruff, Pytest, dependency licensing, and Vite builds all integrated from Day 1.
* **High-Contrast Accessible UI:** Clean, WCAG AA/AAA-compliant design built specifically for laboratory and regulatory environments.
* **176 Automated Regression Tests:** Instant test execution against a real PostgreSQL + pgvector database providing confident refactoring.

### Areas for Improvement / Insights
* **Local Subprocess Socket Isolation on Windows:** Required configuring dedicated `.venv` and setting explicit loopback bindings.
* **Hybrid Extraction Strategy:** Decided on Apache Tika 2.9 (container) for multi-format OCR + Pure Python `pypdf` for fast-path in-process tests (ADR-003).

---

## 4. Sprint 2 Hand-off & Backlog Priorities
1. **`S2-03`:** Ingest full approved PDF corpus using Apache Tika 2.9.
2. **`S2-06`:** Recursive text chunking and 384-dimensional dense vector embeddings (`BAAI/bge-small-en-v1.5`) into pgvector.
3. **`S2-07`:** Hybrid BM25 + dense semantic retrieval with server-side metadata filtering.
4. **`S2-09`:** LangGraph evidence Q&A orchestration workflow with human review checkpoint.
