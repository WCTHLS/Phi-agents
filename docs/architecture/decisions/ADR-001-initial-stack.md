# ADR-001: Initial stack and sequencing

- **Status:** Proposed for MVP; confirm with manager/team during Phase 0.
- **Decision:** Start with a single FastAPI application and PostgreSQL + pgvector. Use Haystack for the Phase 1 RAG pipeline. Parse ordinary text PDFs first and add OCR for scanned documents only where required. Keep scientific analysis in explicit Python functions rather than asking an LLM to do calculations.
- **Why:** This keeps the first milestone small, supports Windows CPU development, and minimizes deployment pieces while retaining a path to the target architecture.
- **Trade-offs:** A single application is less independently scalable than separate services, but easier to test and operate for an MVP. Local LLM inference can be slow on CPU; select an approved API or small local model after a latency/privacy decision.
- **Revisit when:** volume, permission boundaries, workflow durability, client deployment, or integration needs justify separate services/Temporal/other infrastructure.
