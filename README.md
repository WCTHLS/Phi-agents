# Pharma AI Development Assistant

An open-source-first foundation for a pharma-domain AI assistant supporting trusted scientific/regulatory research, formulation guidance, Design of Experiments (DOE), development-risk review, and controlled documentation.

> **Current status:** Phase 0 starter scaffold. The API health check and project documentation are in place. RAG, OCR/document ingestion, formulation guidance, DOE workflows, and regulatory document generation are **not implemented yet**.

## Start here (Windows PowerShell)

### 1. Prerequisites
- Python 3.11 or 3.12
- Git
- Optional: Docker Desktop (only needed to run the local PostgreSQL/pgvector database)

### 2. Create and activate a virtual environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 3. Run the API

```powershell
uvicorn pharma_ai.main:app --reload
```

Open `http://127.0.0.1:8000/health` and expect `{"status":"ok"}`.

### 4. Run tests

```powershell
pytest
```

### 5. Optional local database

```powershell
docker compose up -d db
```

Database settings are examples in `.env.example`. No database connection is used by the health-check endpoint yet.

## Repository structure

```text
pharma-ai-platform/
├── frontend/                              # React + Vite Researcher Workspace UI (3-panel layout)
├── data/
│   └── sources/                           # Approved-source manifest (corpus_manifest.csv) and corpus guide (CORPUS.md)
├── docs/
│   ├── architecture/                      # System diagrams, overview & ADRs
│   ├── implementation/                    # Phased delivery plan & task board
│   └── security/                          # 21 CFR Part 11, GxP & data handling checklists
├── evals/                                 # Golden questions + evidence checker (verify_questions.py)
├── scripts/
│   ├── fetch_corpus.py                    # Download the approved corpus PDFs (not committed)
│   └── init-db.sql                        # PostgreSQL + pgvector schema initialization
├── src/
│   └── pharma_ai/                         # Core Python package
│       ├── api/                           # FastAPI routers & request validation
│       ├── core/                          # App configuration & settings
│       ├── features/                      # Domain capabilities
│       │   ├── documents/                 # Document ingestion & parsing orchestration
│       │   ├── doe/                       # Design of Experiments statistical modeling
│       │   ├── formulation/               # Formulation & excipient compatibility guidance
│       │   ├── quality_risk/              # Bioavailability, stability & impurity evaluation
│       │   ├── reporting/                 # Product Development Report (PDR) & filing drafts
│       │   └── research/                  # Evidence retrieval & citation-backed Q&A
│       ├── infrastructure/                # External adapters & persistent storage
│       │   ├── audit/                     # Immutable GxP / 21 CFR Part 11 audit logging
│       │   ├── connectors/                # Read-only ingestion connectors
│       │   │   ├── eln_lims/              # Electronic Lab Notebook & LIMS connector
│       │   │   └── fda_ema/               # FDA, EMA/MHRA & ICH regulatory connector
│       │   ├── db/                        # PostgreSQL + pgvector client
│       │   ├── security/                  # Keycloak SSO & permission enforcement
│       │   └── storage/                   # SeaweedFS object storage client
│       ├── workers/                       # Celery + RabbitMQ asynchronous task workers
│       └── evals/                         # Benchmark test suite, faithfulness evals & telemetry
└── tests/                                 # Automated pytest suite
```

## Phase sequence

1. **Phase 0 — Align and prepare:** confirm MVP, users, permissions, data handling, architecture, acceptance criteria, and team ownership.
2. **Phase 1 — Trusted research:** approved-source registry, controlled ingestion, document parsing, chunking, embeddings, retrieval, citations, and scientist verification.
3. **Phase 2 — Formulation and DOE:** structured formulation guidance and transparent statistical/DOE tools.
4. **Phase 3 — Experiment feedback and risk:** capture verified experimental results and support risk review.
5. **Phase 4 — Documentation integration:** controlled drafts and integrations with ELN/document systems.
6. **Phase 5 — Evaluate and expand:** quality, security, traceability, performance, and deployment evaluation.

See [`docs/implementation/phase-plan.md`](docs/implementation/phase-plan.md) and [`docs/implementation/team-ownership.md`](docs/implementation/team-ownership.md).

## Architecture principles

- Use approved and permissioned sources; do not silently scrape or ingest client data.
- Keep source links, version, retrieval time, and document provenance with evidence.
- Show evidence and uncertainty; treat generated content as a draft, not a scientific or regulatory decision.
- Require scientist review for critical decisions and controlled documents.
- Keep secrets and sensitive client data out of Git.
- Prefer small, independently testable services; add infrastructure only when the MVP needs it.

## Target architecture & technology choices

- **Frontend:** React + Vite (3-panel researcher workspace: Sources/Verification, Q&A Assistant, DOE & Report Draft Studio)
- **API Backend:** FastAPI
- **Authentication & Identity:** Keycloak SSO (OIDC)
- **RAG Orchestration:** Haystack (Phase 1)
- **Document Extraction & OCR:** Apache Tika + Tesseract OCR for scanned PDF filings
- **Embeddings & Vector Store:** Sentence Transformers with PostgreSQL + pgvector
- **LLM Serving:** On-premises local vLLM endpoint
- **Asynchronous Processing:** Celery + RabbitMQ workers
- **Object Storage:** SeaweedFS (S3-compatible for raw PDFs and frozen report snapshots)
- **Scientific Analysis:** pandas, SciPy, statsmodels (Phase 2+)
- **Observability & Validation:** OpenTelemetry, Prometheus, and automated citation faithfulness benchmarks

These are proposed defaults, not a commitment to deploy every component in Phase 0. Check licenses, client policies, model terms, and compatibility before adoption.
