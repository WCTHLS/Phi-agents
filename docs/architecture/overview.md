# Architecture overview

## Target request flow

1. Scientist submits a research/formulation/documentation question through the UI.
2. FastAPI authenticates the request and checks project/source permissions.
3. A workflow coordinates the requested capability.
4. Phase 1 research service retrieves evidence from approved and versioned sources.
5. The LLM drafts an answer tied to retrieved evidence and flags evidence gaps/uncertainty.
6. The API returns the answer plus citations/provenance; scientist reviews it.
7. Only approved facts feed controlled report drafts and later workflows.

## Proposed logical components

- User interface (future): React/Vite
- API: FastAPI (implemented minimally)
- RAG orchestration (Phase 1): Haystack
- Parsing: text extraction first; OCR for scanned files as needed
- Embeddings: evaluate a CPU-compatible Sentence Transformers model
- Retrieval store: PostgreSQL + pgvector for the initial MVP
- LLM: approved cloud endpoint or approved local/open-weight model; do not assume the model is accurate merely because it is local
- Scientific computing (Phase 2+): pandas, SciPy, statsmodels
- Workflow orchestration: introduce Temporal only when workflows require durable long-running state/approval/retries
- Identity: client SSO or Keycloak before multi-user deployment
- Observability (later): structured logs, metrics, tracing, dashboards

## Guardrails

- Every source must have permission/terms, owner, version/date, and provenance metadata.
- Retrieval must enforce permissions; hiding a result in the UI is not sufficient.
- Answers should separate sourced facts, model interpretation, and unsupported/unknown information.
- Generated outputs are drafts; scientists must review important scientific and regulatory content.
- Audit actions and decisions without logging unnecessary sensitive content.
- Evaluate retrieval and answer quality against a scientist-reviewed test set before expanding scope.

## Deliberately out of scope for Phase 0

Production SSO, a live regulatory crawler, model serving, full RAG, ELN/LIMS integration, formulation recommendation logic, regulated electronic signatures, and production compliance claims.
