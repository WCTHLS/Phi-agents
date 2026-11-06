# Pharma AI Development Assistant

An open-source-first foundation for a pharma-domain AI assistant supporting trusted scientific/regulatory research, formulation guidance, Design of Experiments (DOE), development-risk review, and controlled documentation.

> **Current status:** Phase 0 foundation and the start of Phase 1. The API (sign-in checks, project access, audit trail), the PostgreSQL + pgvector database (schema, migrations, source register) and a UI shell are in place. Chunking and embeddings, RAG answers, OCR for scanned pages, formulation guidance, DOE workflows, and regulatory document generation are **not implemented yet**.

## What works today

| Part | State |
|---|---|
| **API** (FastAPI) | Health and readiness checks, signed-token sign-in, project and role checks, a research Q&A endpoint that answers from a small built-in set of sample passages, and an admin-only audit log. |
| **Database** (PostgreSQL + pgvector) | Schema v1 with migrations, a seed script that loads the approved-source register, and an append-only audit table. |
| **Audit trail** | The server records who did what: project access, source listing, research queries (as a fingerprint, not the question text), reading the log, and refused requests. |
| **UI** (React + Vite) | The document library, the question-and-answer screen with its review gate, and the audit viewer read and write the real API (with `AUTH_MODE=dev` the page fetches a signed development token for the role chosen in the top bar). A call that fails shows its error; nothing invented stands in for it. The Trials and Reports tabs still show **sample data**, and say so. |
| **Document ingestion** | An administrator (or the `scripts/ingest_corpus.py` script) can ingest an approved PDF: the original is kept unchanged, fingerprinted (SHA-256) and read page by page, and a project's members can list its documents. Held documents are refused. Scanned pages are recorded as having no text (no OCR yet). |
| **Chunking and embeddings** | `scripts/index_chunks.py` cuts the ingested text into passages of at most 500 tokens (keeping page numbers and section headings) and turns each into 384 numbers with the `BAAI/bge-small-en-v1.5` model, so the database can find passages by meaning. It runs on your own machine and is not yet part of the upload endpoint. See ADR-008. |
| **Search** | Hybrid search (by meaning plus PostgreSQL keywords, merged by rank) in `features/research/search.py`, with the project, eligibility, model and newest-version rules applied inside the SQL (ADR-009). Run `alembic upgrade head` once to add the keyword column (migration 0004). It is behind `POST /api/v1/projects/{project_id}/search` (project members only; the embedding model loads in the background when the API starts, see `EMBEDDING_WARMUP`). `POST /api/v1/research/query` with a `project_id` answers from that project's documents: the answer is made only of quotes found word for word in the retrieved passages, or is withheld, and the run is stored awaiting review (ADR-010; no language model is used yet). Without `project_id` it still returns built-in sample passages, marked `data_source: built_in_sample`. Reviewers record decisions with `POST /api/v1/research/reviews` (`reviewer` or `qa` role) against a stored run; the status change and the audit event are saved together (D15, D16). Run `alembic upgrade head` once more for the new `workflow_runs` table (migration 0005). |
| **Not yet** | Searching the passages from the API, the answer workflow with a review gate, and a real sign-in screen in the UI. The research answers still come from a small built-in set of sample passages, not from the ingested documents. |

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

**3b. Ingest the corpus** (optional; needs the PDFs from `python scripts/fetch_corpus.py` and the manual downloads listed in `data/sources/CORPUS.md`):

```bash
docker compose run --rm dev python scripts/ingest_corpus.py
docker compose run --rm dev python scripts/assign_documents.py
```

It prints one line per document: `v1 added` with page count and whether the pages have text, `already` if that exact file was ingested before, `held` for documents that are not eligible (never ingested), and `missing` if the PDF is not on disk. Originals and extracted text go to `data/storage` (git-ignored). Run it again and nothing changes. Add `--dry-run` to preview.

The second command divides the documents between the two sample projects: PRJ-001 (Metformin ER nitrosamine risk) gets the impurity and risk documents (FDA nitrosamine guidance, ICH M7, Q3C, Q3D, Q9, and the FDA stability Q&A) and PRJ-002 (BCS Class II bioavailability) gets the bioavailability, dissolution and process documents (ICH M9, M13A, FDA bioavailability, both dissolution guidances, process validation and tablet size). Each document belongs to exactly one project, so a question in one project is never answered from the other's documents; the list is `SAMPLE_PROJECT_DOCUMENTS` in `src/pharma_ai/infrastructure/db/seed.py` (decision log D22). It adds or removes links only, so nothing needs indexing again; `--dry-run` previews, and running it twice changes nothing. Each change is written to the audit trail.

**3c. Cut the text into passages and embed them** (optional; needs step 3b). This one runs on your own machine, not in the dev tools container: the embedding library includes PyTorch (several hundred MB to install), and the model's download should be kept between runs. Use a virtual environment (see Option B):

```bash
pip install -e ".[embeddings]"     # once
python scripts/index_chunks.py
```

The first run downloads the model's public weights (about 130 MB) from Hugging Face; no document text leaves your machine. It prints one line per document version: `indexed` with the number of passages, `already indexed` if nothing needed doing, or `skipped` for a scan with no text. On a laptop without a GPU expect roughly a third of a second per passage (about 4 minutes for the 13-document corpus) and under 1 GB of memory. Run it again and nothing changes; `--force` cuts and embeds again, `--only ICH-Q3DR2` limits it, `--dry-run` previews without loading the model.

**After the text extractor improves** (for example, documents ingested before 5 Nov 2026 read "10⁻⁵" as "10-5" and show list bullets as boxes; decision log D20, D21), read the stored originals again, then cut and embed the passages again for the documents it lists:

```bash
python scripts/reextract_text.py --dry-run     # list what would change
python scripts/reextract_text.py               # store the new text beside the old
python scripts/index_chunks.py --force --only <the documents it printed>
```

The originals and the old text are never changed. Answers asked before the re-index keep their stored citations.

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

### Test users for project access

Until real sign-in (Keycloak) is connected, the sign-in screen and the header's **Signed in as** list offer these made-up users. The API decides each person's role and projects from its own table (`src/pharma_ai/infrastructure/security/dev_users.py`), so the browser cannot ask for more. They exist so that a refusal can be seen:

| User | Role | Projects |
|---|---|---|
| Dr. Aris Thorne (`thorne`) | scientist | PRJ-001 |
| Dr. Priya Nair (`nair`) | scientist | PRJ-002 |
| Dr. Marcus Webb (`webb`) | scientist | PRJ-001 and PRJ-002 |
| Dr. Nora Lind (`lind`) | scientist | none |
| Dr. Elena Vance (`vance`) | reviewer | PRJ-001 |
| Dr. Ravi Menon (`menon`) | reviewer | PRJ-002 |
| Platform Administrator (`admin`) | admin | PRJ-001 and PRJ-002 |

The project list shows both projects and marks the ones the signed-in person does not belong to "no access"; choosing one shows the API refusing it (`403`), and the refusal appears in the audit log (sign in as the administrator, tab 4). Things to try:

1. As Thorne, ask a nitrosamine question in PRJ-001, then switch to PRJ-002: the library and the question are refused.
2. As Nair, the reverse. As Lind, everything is refused.
3. As Thorne, ask a question in PRJ-001, then sign in as Menon (a reviewer, but only for PRJ-002) and try to approve it: refused. Vance (the reviewer for PRJ-001) can approve it; Thorne (a scientist) and the administrator cannot.
4. As Vance, open the **Review Queue** tab: the questions waiting for review in PRJ-001, longest waiting first. Open one to read its answer, quotes and gaps, then approve, reject or ask for more evidence with a written rationale. Menon sees only PRJ-002's queue; Thorne and the administrator are told only a reviewer can see it.

Users are made up for development. The endpoint that signs them in only works when `AUTH_MODE=dev` and `ENVIRONMENT` is `local` or `test`, and it will never be a substitute for real access control (decision log D23).

### Try the API with a real sign-in token

The UI gets its own signed token from the API's development endpoint, so it needs no setup here. To call the API yourself (for example from the API docs), make a signed token for local development (it only works when `AUTH_MODE=dev` and `ENVIRONMENT=local`, which `make_env.py` sets):

```bash
docker compose run --rm dev python scripts/dev_token.py --role admin
docker compose run --rm dev python scripts/dev_token.py --role scientist --project PRJ-001
docker compose run --rm dev python scripts/dev_token.py --user nair     # a named user, see below
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

This starts the API, the UI, PostgreSQL, Keycloak (sign-in server), Apache Tika (document text extraction) and the Nginx gateway. It is heavy (several large images) and the whole stack has **not been run end to end**; the API and UI images were built and checked on their own.

```bash
make up                           # macOS / Linux / WSL
docker compose up -d --build      # Windows PowerShell (after creating .env)
docker compose exec api alembic upgrade head    # create the tables (the image includes the migrations)
```

Open the UI through the gateway at http://localhost:8088, not on port 5173: the UI container does not forward `/api` calls itself. The UI has no Keycloak sign-in yet, so for local work keep `AUTH_MODE=dev` in `.env` (what `scripts/make_env.py` writes); with the default `AUTH_MODE=oidc` the UI cannot obtain a token. To stop: `make down` or `docker compose down`.

When you run the UI with `npm run dev` it forwards `/api` to http://127.0.0.1:8000. Set `VITE_API_URL` to point it at an API on another port.

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
| 5 | **Evidence check** | `python evals/verify_questions.py` | The corpus PDFs (`python scripts/fetch_corpus.py` plus the manual downloads in `data/sources/CORPUS.md`) | Each golden question's evidence page can be found in the approved documents. Today 30 of 33 pass; the other 3 need the deferred Indian documents (see `CORPUS.md`). |
| 5b | **Search check** | `python evals/retrieval_check.py --retriever hybrid --save evals/results/run.json` (`--retriever dense`, `keyword` or `hybrid`) | The corpus ingested and indexed (steps 3b and 3c above); loads the embedding model | For each golden question, whether the search returns a passage from the right document on the right page (top 1, 3 and 5). It is a measure for comparing changes (`--compare earlier.json`), not a verdict on answer quality; questions whose own patterns match no page, gap questions and uningested documents are listed and left out of the totals. |
| 5c | **Answer check** | `python evals/answer_check.py --cross-limit 20 --save evals/results/answers.json` | The corpus ingested and indexed; `LLM_PROVIDER=groq` in `.env` to measure the language model (without it the word-matching writer is measured, and nothing leaves your machine) | Runs each golden question through the real search, the answer writer and the same checks the API applies, and scores the quotes with the golden questions' own evidence rules: how often the answer is withheld or correct, answer precision, recall and F1, how often the model's quotes are word for word in the passages, whether the system stays silent for a question asked in the project that cannot answer it, retries and speed, each with a 95% interval. With the model on it sends the question and three public passages per ask to the provider (about 22 s between asks keeps it inside the free tier). It stores nothing and audits nothing. See the definitions at the top of the script and decision log D27. |
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

Start the API (`uvicorn pharma_ai.main:app --reload`, with `AUTH_MODE=dev`) and the UI (`cd frontend`, `npm install` once, `npm run dev`), then open http://localhost:5173. The UI forwards `/api` calls to port 8000 itself, so the API must be running first.
1. As **Scientist**, ask a question on the Research tab. You should see quotes from the project's documents with their document, version, page and attribution, a stored run id, and the status "pending expert review". A question the documents cannot answer is withheld.
2. Switch to **Reviewer** (the answer stays on screen), write a rationale and approve, reject or ask for more evidence. Only a reviewer can; the server checks this too.
3. Switch to **Admin** and open the Audit tab to see the question and the decision.
If the API is down or refuses a call you see the error, not an answer. The Trials and Reports tabs show invented sample data and are labelled as such.

## Logs

The API writes one log line per request (method, path, status, time taken, the signed-in person's stable id, the project in the path) plus its own warnings and errors. Each request has a `request_id`, which is also returned in the `X-Request-ID` response header; send your own `X-Request-ID` (8 to 64 letters, digits, `.`, `_`, `-`) to follow a request across systems.

What is **never** written: sign-in tokens, passwords, secrets, e-mail addresses, the query string, request or response bodies, and the text of questions, answers or documents. Fields with names such as `token`, `password`, `query`, `answer` or `text` are replaced with `[REDACTED]`, and anything that looks like a token, an e-mail address or a password in a web address is removed from messages and error traces. This is not the audit trail (that is the `audit_events` table, see ADR-005); logs are for fixing problems.

If the optional `opentelemetry-api` package is installed (`pip install -e ".[observability]"`) and a trace is active, log lines also carry `trace_id` and `span_id`. Nothing needs it to run. The rules live in `src/pharma_ai/core/redaction.py`; what the client actually permits in logs is still to be confirmed (`docs/security/data-handling.md`), so the rules are strict by default.

## Settings (`.env`)

`.env.example` lists every setting with a safe placeholder; your `.env` holds the real values and is never committed.

| Setting | What it does |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | Database account and port. If you change them after the database was first created, recreate it: `docker compose down -v`. |
| `DATABASE_URL` | How tools on your own machine reach the database. Inside the dev container the host is `db`; compose sets that for you. |
| `TEST_DATABASE_URL` | The throwaway database the tests erase. Its name must contain `test`; the tests refuse anything else. |
| `AUTH_MODE`, `AUTH_DEV_SECRET` | `dev` accepts tokens from `scripts/dev_token.py`; `oidc` uses Keycloak. Dev mode is refused unless `ENVIRONMENT` is `local` or `test`. |
| `AUDIT_BACKEND` | `database` (default) or `memory` (no database; local/test only). |
| `STORAGE_ROOT`, `MAX_UPLOAD_MB`, `EXTRACTION_ENGINE` | Where ingested originals and extracted text are kept (a folder; a volume in compose), the largest upload accepted, and the PDF reader: `pypdf` (in-process, default) or `tika` (uses `TIKA_URL`, falls back to pypdf). See ADR-007. |
| `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE`, `CHUNK_SIZE`, `CHUNK_OVERLAP` | The embedding model (must give 384 numbers per passage; default `BAAI/bge-small-en-v1.5`), how many passages it embeds at a time, and how passages are cut (at most 500 tokens, repeating the last 100). Changing any of them means running `scripts/index_chunks.py --force`. See ADR-008. |
| `EMBEDDING_WARMUP` | `true` (default): the API loads the embedding model in the background when it starts, so the first search is not slow. The API answers other requests while it loads; a search that arrives early waits for the same load. If the model cannot be loaded (for example the `embeddings` extra is not installed) the API still starts and searches answer 503. The test suite turns it off. |
| `LOG_LEVEL`, `LOG_FORMAT` | How much the API logs (`INFO` by default) and how: `json` (default, one JSON object per line) or `text` (readable, for local work). See "Logs" below. |
| `COMPOSE_FILE`, `COMPOSE_PATH_SEPARATOR` | Make `docker compose` also load the dev tools container. |
| `LLM_PROVIDER`, `LLM_MODEL`, `GROQ_API_KEY`, `GROQ_BASE_URL`, `LLM_TIMEOUT_SECONDS` | `LLM_PROVIDER=not_configured` (the default): no language model; answers are made by matching the question's words and nothing leaves the machine. `groq` is a development option: a Groq-hosted open-weight model picks the sentences to quote, and the question and the retrieved passages are sent to Groq (public documents only, decision log D19). Every quote is still checked word for word against the passages. If the model is unreachable the answer falls back to word matching and says so. The API refuses to start with `groq` and no key. Put model keys only in your own `.env`, never in Git. |

## Common problems

| What you see | Why, and what to do |
|---|---|
| `relation "projects" already exists` when migrating | An old database volume from before the migrations. Run `docker compose down -v`, then start again from step 2. |
| `password authentication failed` | `.env` changed after the database was created. Run `docker compose down -v`. |
| `no such service: dev` | `.env` is missing the `COMPOSE_FILE` lines. Run `scripts/make_env.py`, or add `-f docker-compose.yml -f docker-compose.dev.yml` to each command. |
| Connection refused, or commands hang | Docker is not running, or the database is not `healthy` yet (`docker compose ps`). |
| `503 Audit trail unavailable` from the API | The database is down or has no tables. Start it and run `alembic upgrade head`, or use `AUDIT_BACKEND=memory`. |
| `AUTH_MODE=dev is not allowed` / `AUDIT_BACKEND=memory is not allowed` | `ENVIRONMENT` is not `local` or `test`. These modes are blocked outside development on purpose. |
| The UI says "Not signed in to the API" or calls answer `401` | The API is not running, or is not in dev mode. Start it with `AUTH_MODE=dev` and `ENVIRONMENT=local` in `.env`, then reload the page. |
| A document you uploaded disappears from search | A new version is only searched after it has been indexed. Run `python scripts/index_chunks.py`. |
| An exponent reads as a plain number ("10-5", "1 in 106"), or bullets show as boxes (▯) | The document was ingested before superscripts and symbol-font bullets were converted (decision log D20, D21). Run `python scripts/reextract_text.py`, then `python scripts/index_chunks.py --force` for the documents it lists. |
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
├── evals/                                 # Golden questions, evidence checker (verify_questions.py), search check (retrieval_check.py), answer check (answer_check.py)
├── scripts/
│   ├── make_env.py                        # Create your .env with random passwords
│   ├── dev_token.py                       # Make a signed token for local API testing
│   ├── smoke_test.py                      # End-to-end check of the running API and database
│   ├── seed_dev_data.py                   # Load roles, sample projects and the source register
│   ├── ingest_corpus.py                   # Ingest the corpus PDFs: store, fingerprint, extract text
│   ├── index_chunks.py                    # Cut ingested text into passages and embed them
│   ├── assign_documents.py                # Divide the documents between the sample projects
│   ├── reextract_text.py                  # Read stored originals again after an extractor change
│   ├── create_test_database.py            # Create the throwaway database for tests
│   ├── fetch_corpus.py                    # Download the approved corpus PDFs (not committed)
│   └── init-db.sql                        # Enables the pgvector extension (tables come from migrations)
├── src/
│   └── pharma_ai/                         # Core Python package
│       ├── api/                           # FastAPI routers & request validation
│       ├── core/                          # App configuration & settings
│       ├── features/                      # Domain capabilities
│       │   ├── documents/                 # Ingestion, PDF text extraction, chunking & indexing
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
│       │   ├── embeddings/                # Embedding model interface + local sentence-transformers model
│       │   ├── security/                  # Sign-in token checks and project/role permissions
│       │   └── storage/                   # Object store interface + local-folder store (originals)
│       ├── workers/                       # Celery + RabbitMQ asynchronous task workers (planned)
│       └── evals/                         # Benchmark test suite, faithfulness evals & telemetry (planned)
└── tests/                                 # Automated pytest suite
```

## Where to read next

- [docs/architecture/erd.md](docs/architecture/erd.md): the database tables and the rules the database enforces.
- [docs/architecture/decisions/](docs/architecture/decisions/): why things are built this way (stack, licence rule, extraction and embedding choice, schema, audit trail, logging, ingestion and storage).
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
- **Workflow orchestration:** LangGraph (Phase 1, PRD FR-28), adopted after the Sprint 2 demo; plain functions until then (decision log D17)
- **Document Extraction & OCR:** Apache Tika + Tesseract OCR for scanned PDF filings
- **Embeddings & Vector Store:** Sentence Transformers with PostgreSQL + pgvector
- **LLM Serving:** On-premises local vLLM endpoint
- **Asynchronous Processing:** Celery + RabbitMQ workers
- **Object Storage:** SeaweedFS (S3-compatible for raw PDFs and frozen report snapshots)
- **Scientific Analysis:** pandas, SciPy, statsmodels (Phase 2+)
- **Observability & Validation:** OpenTelemetry, Prometheus, and automated citation faithfulness benchmarks

These are proposed defaults, not a commitment to deploy every component in Phase 0. Check licenses, client policies, model terms, and compatibility before adoption.
