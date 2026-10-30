# Pharma AI Development Assistant

An open-source-first foundation for a pharma-domain AI assistant supporting trusted scientific/regulatory research, formulation guidance, Design of Experiments (DOE), development-risk review, and controlled documentation.

> **Current status:** Phase 0 foundation and the start of Phase 1. The API (sign-in checks, project access, audit trail), the PostgreSQL + pgvector database (schema, migrations, source register) and a UI shell are in place. RAG, OCR/document ingestion, formulation guidance, DOE workflows, and regulatory document generation are **not implemented yet**.

## What works today

| Part | State |
|---|---|
| **API** (FastAPI) | Health and readiness checks, signed-token sign-in, project and role checks, a research Q&A endpoint that answers from a small built-in set of sample passages, and an admin-only audit log. |
| **Database** (PostgreSQL + pgvector) | Schema v1 with migrations, a seed script that loads the approved-source register, and an append-only audit table. |
| **Audit trail** | The server records who did what: project access, source listing, research queries (as a fingerprint, not the question text), reading the log, and refused requests. |
| **UI** (React + Vite) | A workspace shell. Its role switcher is a **demo login** and its data is built in, so it does not call the API with a real token yet (see "Try the API"). |
| **Not yet** | Real document ingestion, embeddings and search over PDFs, the answer workflow with a review gate, and a real sign-in screen in the UI. |

## Start here

### Prerequisites
- **Git**
- **Docker** with Compose v2 (a recent version; Docker Desktop on Windows/macOS, Docker Engine on Linux). Podman users: replace `docker compose` with `podman compose` (not tested here).
- **Python 3** on your own machine, only to run one setup script (`scripts/make_env.py`, no packages needed). Windows: use `py` instead of `python`.
- **Node.js 20 or newer**, only to run the UI.

Every command below is run from the repository root. They are the same in PowerShell, bash and zsh unless noted.

### Option A: everything in Docker (recommended)

You do not need to install Python packages. A "dev tools" container has them all and can reach the database.

**1. Create your private settings file.** It is not committed and holds random passwords for your machine only:

```bash
python scripts/make_env.py        # Windows: py scripts/make_env.py
```

It refuses to overwrite an existing `.env`. To set up by hand instead, copy `.env.example` to `.env`, replace every `change_me_local_only` with your own password (the same one in `POSTGRES_PASSWORD` and `DATABASE_URL`), set `AUTH_MODE=dev` and a long random `AUTH_DEV_SECRET`, and uncomment the `COMPOSE_*` lines.

**2. Start the database** and wait until it says `healthy`:

```bash
docker compose up -d db
docker compose ps
```

**3. Create the tables and load the starter data:**

```bash
docker compose run --rm dev alembic upgrade head
docker compose run --rm dev python scripts/seed_dev_data.py
```

You should see `Seeded: 4 roles, 2 projects, 22 sources`.

**4. Run the tests.** The database tests need their own empty database (they erase it), so create it once:

```bash
docker compose run --rm dev python scripts/create_test_database.py
docker compose run --rm dev pytest
```

All tests should pass.

**5. Run the API:**

```bash
docker compose run --rm -p 8000:8000 dev uvicorn pharma_ai.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000/docs for the interactive API documentation, and http://localhost:8000/ready (it reports `ready` only if the database answers).

**6. Run the UI** in a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. The UI forwards `/api` calls to the API on port 8000.

**7. Check that everything works together** (with the database and the API from steps 2 to 5 running):

```bash
python scripts/smoke_test.py      # Windows: py scripts/smoke_test.py
```

It plays a scientist and an admin against the real API and reads the audit log back. Every line should say `PASS` (details in "Test the whole system" below).

**Stop and reset**

```bash
docker compose stop db           # pause the database, keep its data
docker compose down -v           # remove the database and ALL its data (start again from step 2)
```

### Try the API with a real sign-in token

The UI's demo login is not a real token, so the API answers it with `401`. To see the real behaviour, make a signed token for local development (it only works when `AUTH_MODE=dev` and `ENVIRONMENT=local`, which `make_env.py` sets):

```bash
docker compose run --rm dev python scripts/dev_token.py --role admin
docker compose run --rm dev python scripts/dev_token.py --role scientist --project PRJ-001
```

In http://localhost:8000/docs click **Authorize** and paste the token. A good first try, with a scientist token then an admin token:

1. As a scientist: `GET /api/v1/projects/PRJ-001` works, `GET /api/v1/projects/PRJ-002` is refused (`403`), and `POST /api/v1/research/query` returns a cited answer from the sample passages.
2. As an admin: `GET /api/v1/audit/events` lists what the scientist just did, including the refused request.

The audit log keeps a fingerprint of each research question, never its text. Rows cannot be edited or deleted, even in the database.

### Option B: Python on your own machine

Use this if you prefer your own editor and debugger. The database still runs in Docker.

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell    |   macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

python scripts/make_env.py        # Windows: py scripts/make_env.py
docker compose up -d db
alembic upgrade head
python scripts/seed_dev_data.py
uvicorn pharma_ai.main:app --reload
```

Tests: `pytest` runs everything that does not need the database. To include the database tests, create the test database and point the tests at it. The tests read `TEST_DATABASE_URL` from your shell, not from `.env`:

```powershell
# PowerShell
$env:TEST_DATABASE_URL = (Select-String '^TEST_DATABASE_URL=' .env).Line.Split('=',2)[1]
python scripts/create_test_database.py
pytest
```

```bash
# macOS / Linux
export "$(grep '^TEST_DATABASE_URL=' .env)"
python scripts/create_test_database.py
pytest
```

No database at all? Put `AUDIT_BACKEND=memory` in `.env`. The API then keeps its audit trail in memory (lost on restart), which is allowed only when `ENVIRONMENT` is `local` or `test`.

### Option C: the full platform in containers

This starts the API, PostgreSQL, Keycloak (sign-in server), Apache Tika (document text extraction) and the Nginx gateway. It is heavy (several large images) and was **not re-tested** when this guide was written; the UI is not part of it (run it as in step 6).

```bash
make up                           # macOS / Linux / WSL
docker compose up -d --build      # Windows PowerShell (after creating .env)
```

Then create the tables as in step 3. To stop: `make down` or `docker compose down`.

| Service | Local URL | Purpose |
| :--- | :--- | :--- |
| Nginx gateway | http://localhost:8088 | Reverse proxy for `/api`, `/health`, `/ready`, `/realms` |
| API | http://localhost:8000/docs | Interactive API docs, `/health`, `/ready` |
| Keycloak | http://localhost:8080 | Local sign-in server with a pre-imported `pharma` realm (no users yet) |
| Apache Tika | http://localhost:9998 | Document text extraction |
| PostgreSQL + pgvector | `localhost:5432` | Database |

## Test the whole system

There are several layers of checks, from fastest to broadest. Run them in this order.

| # | What | Command | Needs | What it proves |
|---|---|---|---|---|
| 1 | **Automated tests** | `docker compose run --rm dev pytest` (or `pytest` in your own environment) | Database for the database tests; without one they are skipped | The API's behaviour (sign-in, permissions, audit trail), the database rules (licence rule, append-only audit table, scoped search), the migrations (up, down and back up) and the setup scripts. All tests should pass. |
| 2 | **Lint and format** | `ruff check src tests` and `ruff format --check src tests` | Nothing running | Code style; the same checks CI runs. |
| 3 | **UI build** | `cd frontend && npm run build` | Node.js | The UI compiles and type-checks. |
| 4 | **End-to-end smoke test** | `python scripts/smoke_test.py` | Database with tables, and the API on port 8000 | The pieces work **together** over real HTTP (details below). |
| 5 | **Evidence check** | `python evals/verify_questions.py` | The corpus PDFs (`python scripts/fetch_corpus.py` plus the manual downloads in `data/sources/CORPUS.md`) | Each golden question's evidence page can be found in the approved documents. Today 11 of 16 pass; the reasons for the rest are listed in `CORPUS.md`. |
| 6 | **GitHub CI** | Push, then open the repository's Actions tab | GitHub | Secret scan, lint, tests against a real pgvector database, licence report and the UI build, on a clean machine. |

### The end-to-end smoke test

`scripts/smoke_test.py` needs only Python 3 (no packages) and your `.env`. It signs its own development tokens, so `AUTH_MODE=dev` must be set (`make_env.py` does that). It checks, in order:

1. The API is up and ready, and the database answers (`/health`, `/ready`).
2. A request with no token is refused (`401`).
3. A scientist sees only their own project, can open it, and is refused another project (`403`).
4. A research question is answered.
5. Nobody can write a fake audit event (`405`), and a scientist cannot read the audit log (`403`).
6. An admin can read back what the scientist did, every event is attributed to the right person, and the refused request is recorded as `DENIED`.
7. The question text is stored only as a fingerprint and appears nowhere in the audit log.

Exit code `0` means every check passed. Each run adds a few audit rows for a made-up person (`smoke-xxxxxx`). The audit trail cannot be edited or deleted, so run it against a development database only. If the API is not reachable, it says so; if the database is down, the checks that need it fail with `503`, which is the intended "fail closed" behaviour.

### Checking the UI by hand

Start the UI (step 6) and open http://localhost:5173. Switch between Scientist, Reviewer and Admin in the top bar, ask a question on the Research tab, and open the Audit tab as Admin. Everything you see is the UI's own **built-in sample data and demo login**; it is not read from the API or the database yet. So a working UI proves the UI builds and renders, not that it is connected. To see real API behaviour use `scripts/smoke_test.py` or the API docs with a token from `scripts/dev_token.py`.

## Settings (`.env`)

`.env.example` lists every setting with a safe placeholder; your `.env` holds the real values and is never committed.

| Setting | What it does |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | Database account and port. If you change them after the database was first created, recreate it: `docker compose down -v`. |
| `DATABASE_URL` | How tools on your own machine reach the database. Inside the dev container the host is `db`; compose sets that for you. |
| `TEST_DATABASE_URL` | The throwaway database the tests erase. Its name must contain `test`; the tests refuse anything else. |
| `AUTH_MODE`, `AUTH_DEV_SECRET` | `dev` accepts tokens from `scripts/dev_token.py`; `oidc` uses Keycloak. Dev mode is refused unless `ENVIRONMENT` is `local` or `test`. |
| `AUDIT_BACKEND` | `database` (default) or `memory` (no database; local/test only). |
| `COMPOSE_FILE`, `COMPOSE_PATH_SEPARATOR` | Make `docker compose` also load the dev tools container. |
| `LLM_PROVIDER`, `LLM_MODEL` | Not configured yet. Put model endpoints and keys only here, never in Git. |

## Common problems

| What you see | Why, and what to do |
|---|---|
| `relation "projects" already exists` when migrating | An old database volume from before the migrations. Run `docker compose down -v`, then start again from step 2. |
| `password authentication failed` | `.env` changed after the database was created. Run `docker compose down -v`. |
| `no such service: dev` | `.env` is missing the `COMPOSE_FILE` lines. Run `scripts/make_env.py`, or add `-f docker-compose.yml -f docker-compose.dev.yml` to each command. |
| Connection refused, or commands hang | Docker is not running, or the database is not `healthy` yet (`docker compose ps`). |
| `503 Audit trail unavailable` from the API | The database is down or has no tables. Start it and run `alembic upgrade head`, or use `AUDIT_BACKEND=memory`. |
| `AUTH_MODE=dev is not allowed` / `AUDIT_BACKEND=memory is not allowed` | `ENVIRONMENT` is not `local` or `test`. These modes are blocked outside development on purpose. |
| The UI shows data but the API answers `401` | Expected: the UI uses a demo login. Use `scripts/dev_token.py` with the API docs instead. |
| A port is already in use (5432, 8000, 5173) | Another program uses it. Change `POSTGRES_PORT` in `.env`, or map a different host port, for example `-p 8001:8000`. |
| Database tests are skipped | `TEST_DATABASE_URL` is not set in your shell (see Option B). The dev container always sets it; it only needs `create_test_database.py` to have been run once. |
| Smoke test says it could not reach the API | The API is not running. Start it as in step 5 (the dev container maps port 8000; the smoke test uses `http://127.0.0.1:8000` unless you pass `--base-url`). |
| `alembic: command not found` | You are outside the virtual environment. Activate it, or run through the dev container. |

## Useful commands

| Task | Command |
|---|---|
| Open a shell inside the dev container | `docker compose run --rm dev bash` |
| Open a database prompt | `docker compose exec db psql -U pharma_ai -d pharma_ai` |
| Lint and format check (what CI runs) | `ruff check src tests` and `ruff format --check src tests` |
| Check the evidence page of each golden question | `python evals/verify_questions.py` (needs the corpus PDFs; see `data/sources/CORPUS.md`) |
| Download the approved corpus PDFs | `python scripts/fetch_corpus.py` (they are not committed) |
| Remove the tables again | `docker compose run --rm dev alembic downgrade base` |

## Repository structure

```text
pharma-ai-platform/
├── .env.example                           # Names of all local settings; scripts/make_env.py turns it into your .env
├── alembic.ini, migrations/               # Database migrations (Alembic)
├── Dockerfile, docker-compose.yml         # API image and the full local stack
├── Dockerfile.dev, docker-compose.dev.yml # Dev tools container (migrations, scripts, tests)
├── frontend/                              # React + Vite Researcher Workspace UI (3-panel layout)
├── infra/                                 # Container configuration
│   ├── keycloak/                          # Pre-seeded local 'pharma' OIDC realm
│   └── nginx/                             # Reverse proxy routing configuration
├── data/
│   └── sources/                           # Approved-source manifest (corpus_manifest.csv) and corpus guide (CORPUS.md)
├── docs/
│   ├── architecture/                      # System diagrams, ERD, overview & ADRs
│   ├── implementation/                    # Phased delivery plan & task board
│   └── security/                          # 21 CFR Part 11, GxP & data handling checklists
├── evals/                                 # Golden questions + evidence checker (verify_questions.py)
├── scripts/
│   ├── make_env.py                        # Create your .env with random passwords
│   ├── dev_token.py                       # Make a signed token for local API testing
│   ├── smoke_test.py                      # End-to-end check of the running API and database
│   ├── seed_dev_data.py                   # Load roles, sample projects and the source register
│   ├── create_test_database.py            # Create the throwaway database for tests
│   ├── fetch_corpus.py                    # Download the approved corpus PDFs (not committed)
│   └── init-db.sql                        # Enables the pgvector extension (tables come from migrations)
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
│       │   ├── audit/                     # Append-only audit trail (database or in-memory)
│       │   ├── connectors/                # Read-only ingestion connectors
│       │   │   ├── eln_lims/              # Electronic Lab Notebook & LIMS connector
│       │   │   └── fda_ema/               # FDA, EMA/MHRA & ICH regulatory connector
│       │   ├── db/                        # Database models, sessions, scoped repositories, seed loader
│       │   ├── security/                  # Sign-in token checks and project/role permissions
│       │   └── storage/                   # SeaweedFS object storage client (planned)
│       ├── workers/                       # Celery + RabbitMQ asynchronous task workers (planned)
│       └── evals/                         # Benchmark test suite, faithfulness evals & telemetry (planned)
└── tests/                                 # Automated pytest suite
```

## Where to read next

- [docs/architecture/erd.md](docs/architecture/erd.md): the database tables and the rules the database enforces.
- [docs/architecture/decisions/](docs/architecture/decisions/): why things are built this way (stack, licence rule, extraction and embedding choice, schema, audit trail).
- [data/sources/CORPUS.md](data/sources/CORPUS.md): the approved documents, their licence status, and the golden questions.
- [docs/implementation/phase-plan.md](docs/implementation/phase-plan.md) and [docs/implementation/team-ownership.md](docs/implementation/team-ownership.md): plan and ways of working.
- [docs/security/data-handling.md](docs/security/data-handling.md): what to confirm before using real client material.

## Phase sequence

1. **Phase 0 — Align and prepare:** confirm MVP, users, permissions, data handling, architecture, acceptance criteria, and team ownership.
2. **Phase 1 — Trusted research:** approved-source registry, controlled ingestion, document parsing, chunking, embeddings, retrieval, citations, and scientist verification.
3. **Phase 2 — Formulation and DOE:** structured formulation guidance and transparent statistical/DOE tools.
4. **Phase 3 — Experiment feedback and risk:** capture verified experimental results and support risk review.
5. **Phase 4 — Documentation integration:** controlled drafts and integrations with ELN/document systems.
6. **Phase 5 — Evaluate and expand:** quality, security, traceability, performance, and deployment evaluation.

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
