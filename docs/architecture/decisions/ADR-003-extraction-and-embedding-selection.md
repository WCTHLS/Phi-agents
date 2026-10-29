# ADR-003: Document Extraction and Embedding Model Selection (S1-14 Spike)

- **Status:** Decided for Sprint 2 Ingestion Pipeline (`S2-03`, `S2-06`).
- **Context:** PRD §12.2, FR-05, FR-08, NFR-15, NFR-21. Phase 1 requires reliable document parsing (preserving tables, numerical thresholds, section titles, and page numbers) and dense semantic indexing into PostgreSQL + `pgvector` to support evidence-grounded Q&A with strict citations.

---

## 1. Document Extraction Decision

We evaluated three extraction strategies across 5 authentic regulatory documents (`ICH-Q3DR2`, `ICH-Q3CR9`, `FDA-PV-2011`, `FDA-ANDA-STAB-2014`, `ICH-M7R2`):

1. **Primary Production Engine (On-Premises / Containerized): Apache Tika 2.9 + Tesseract OCR**
   - **Image:** `tika/tika:2.9.2-full` defined in `docker-compose.yml`.
   - **Licence:** Apache 2.0 (permissive, commercial-safe per NFR-15).
   - **Capabilities:** Multi-format ingestion (PDF, DOCX, scan images via Tesseract OCR), HTML layout extraction preserving tables and headings, metadata extraction.
   - **Rationale:** Keeps all processing inside the local GxP enclave; eliminates cloud data residency / egress concerns.

2. **Fast-Path / Local Dev Engine: Pure Python (`pypdf` 6.x + `pdfplumber`)**
   - **Licence:** BSD-3-Clause / MIT.
   - **Capabilities:** In-process, zero external server dependency, high throughput (20–44 pages/second benchmarked on local CPU).
   - **Role:** Default for local unit tests, CI test pipelines, and clean digital PDFs.

3. **Cloud Extension (Optional): Azure Document Intelligence (`prebuilt-layout`)**
   - **Role:** Preserved behind an ingestion adapter if the client chooses the Azure cloud architecture (PRD §11).

---

## 2. Embedding Model Decision

We evaluated candidate embedding models for pharmaceutical regulatory retrieval:

| Candidate Model | Dimensions | Licence | Parameters | CPU Latency / Chunk | MTEB Retrieval Rank | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`BAAI/bge-small-en-v1.5`** | **384** | **MIT** | **33.5M** | **~22 ms** | **Top 5 (small)** | **SELECTED (Production Default)** |
| `all-MiniLM-L6-v2` | 384 | Apache 2.0 | 22.7M | ~15 ms | Baseline standard | Selected for CI / Dev fallback |
| `pubmedbert-base-embeddings` | 768 | Apache 2.0 | 110M | ~65 ms | Biomedical niche | Retained as option for Sprint 3 |
| Azure `text-embedding-3-small` | 1536 / 384 | Proprietary | N/A | Network call | High | Cloud adapter candidate |

### Why `bge-small-en-v1.5`?
- **Dimensionality:** 384 dimensions matches standard `pgvector` cosine indexing (`vector_cosine_ops`), minimizing index RAM consumption.
- **Accuracy:** Significantly higher semantic recall on regulatory and technical phrasing than `all-MiniLM-L6-v2`.
- **Latency & Footprint:** ~33M parameters runs comfortably on standard CPU without dedicated GPU hardware (NFR-21).
- **Licence:** MIT licence allows unrestricted internal and commercial usage (NFR-15).

---

## 3. Consequences & Next Steps (Sprint 2)
- In `S2-03`: Implement `DocumentIngestionService` connecting to Apache Tika REST API (`/tika`) with fallback to `pypdf`.
- In `S2-06`: Implement recursive text chunking (500 tokens, 100 token overlap) with sentence boundaries, generating 384-dimensional embeddings via `SentenceTransformer('BAAI/bge-small-en-v1.5')` into PostgreSQL `pgvector`.
