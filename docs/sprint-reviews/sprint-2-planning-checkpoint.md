# Sprint 2 planning and source-approval checkpoint (S2-01)

Meeting: Monday 2 Nov 2026 (time and time zone are not stated in the sprint plan).
Purpose, from the sprint plan: *agree the Sprint 2 goal; confirm the approved document set and the model licence status before ingestion starts.*

Nothing on this page is approved yet. The approval columns and the sign-off block are blank on purpose: they are for the people with authority to fill in. Open questions are numbered Q1 to Q15 in the [decision log](../implementation/decision-log.md).

**Plain-language terms.** *Ingestion* is reading a document into the system so it can be searched. *Eligible* means the document's own legal notice or publisher policy lets us reuse its text. *Held* means that is not clear, so the document stays out. A *golden question* is a test question with a known correct passage.

---

## 1. Sprint 2 goal and scope (decision needed: Q13)

**Proposed goal (from the earlier planning review; not yet adopted):** ingest 3 to 5 public text-layer PDFs from the approved set, ask a question, get an answer that cites passage, document and version, approve or reject it, and see that action in the audit log.

| In the proposal | Deferred in the proposal |
|---|---|
| Ingestion of text PDFs (S2-03), chunking and embeddings (S2-06), vector retrieval with project filters (S2-07), a workflow with a human approval step kept as a status field plus an approval endpoint (S2-09) | OCR for scanned pages, full-text search and reranking, the LangGraph Postgres checkpointer, CSV export, Playwright end-to-end tests |

- **Why re-scope:** the plan budgets about 18 hours for the four RAG tasks (S2-03, S2-06, S2-07, S2-09: 5 + 4 + 4 + 5). The earlier review estimated 45 to 70 hours. This is an estimate, not a measurement (UNVERIFIED). It also advised a 25 to 30 percent buffer.
- **Known cost of deferring hybrid search:** pure vector search can confuse near-identical documents (ICH Q3C vs Q3D, NDMA vs NDEA). Expect some golden questions to fail until keyword matching is added, or filter by document first.
- **Order problem in the plan:** the question-and-answer screen (S2-08) and the review screen (S2-10) end before the workflow they depend on (S2-09). The frontend developer needs a stub answer to build against.

**To agree today: the answer contract.** The starting point is what `POST /api/v1/research/query` already returns: `answer`, `citations` (source, section, page, quoted text), `evidence_gaps`, `confidence_score`, `verified`, `retrieved_evidence`. Two fields are misleading today and should not be shown as fact: `verified` is always true when the best passage scores 0.10 or more on word overlap, and `confidence_score` is the average of that word-overlap score, never below 0.50 and never above 0.99. Neither says whether the evidence actually supports the answer. Until a scientist has reviewed the evidence the screen should say "not yet reviewed by a domain expert" (decision D9).

## 2. Document set to approve for the Sprint 2 test corpus (Q2)

Thirteen documents are eligible and in the corpus. "Basis" is what the manifest records; it was checked from the primary source, but nobody on the team is qualified to judge licences. The approval needs someone with authority.

| # | Document ID | Authority | Title | Version | Basis | Approve? | Approver and date |
|---|---|---|---|---|---|---|---|
| 1 | ICH-Q3CR9 | ICH | Impurities: Guideline for Residual Solvents | Q3C(R9) | notice in PDF | | |
| 2 | ICH-Q3DR2 | ICH | Guideline for Elemental Impurities | Q3D(R2) | notice in PDF | | |
| 3 | ICH-M7R2 | ICH | Assessment and Control of DNA Reactive (Mutagenic) Impurities | M7(R2) | notice in PDF | | |
| 4 | ICH-Q9R1 | ICH | Quality Risk Management | Q9(R1) | notice in PDF | | |
| 5 | ICH-M9 | ICH | Biopharmaceutics Classification System-based Biowaivers | M9 | notice in PDF | | |
| 6 | ICH-M13A | ICH | Bioequivalence for Immediate-Release Solid Oral Dosage Forms | M13A | notice in PDF | | |
| 7 | FDA-NITRO-2024 | FDA | Control of Nitrosamine Impurities in Human Drugs | Revised final (Sept 2024) | FDA site policy | | |
| 8 | FDA-DISS-1997 | FDA | Dissolution Testing of Immediate Release Solid Oral Dosage Forms | Final (Aug 1997) | FDA site policy | | |
| 9 | FDA-DISS-HS-2018 | FDA | Dissolution Testing and Acceptance Criteria for Immediate-Release Solid Oral Dosage Form Drug Products Containing High Solubility Drug Substances | Final (Aug 2018) | FDA site policy | | |
| 10 | FDA-BA-2022 | FDA | Bioavailability Studies Submitted in NDAs or INDs - General Considerations | Final (Apr 2022) | FDA site policy | | |
| 11 | FDA-ANDA-STAB-2014 | FDA | ANDAs: Stability Testing of Drug Substances and Products: Questions and Answers | Final (May 2014) | FDA site policy | | |
| 12 | FDA-PV-2011 | FDA | Process Validation: General Principles and Practices | Final (Jan 2011) | FDA site policy | | |
| 13 | FDA-SIZE-2022 | FDA | Size, Shape, and Other Physical Attributes of Generic Tablets and Capsules | Revision 1 (Oct 2022) | FDA site policy | | |

Before ingesting, the checks in `data/sources/CORPUS.md` ("Check before ingesting") still apply, and ingestion must skip anything that is not `eligible = yes`.

## 3. Deferred: two Indian statutory documents

| Document ID | Title | What is needed |
|---|---|---|
| IN-SCHEDM-2023 | Revised Schedule M (Good Manufacturing Practices), G.S.R. 922(E), 28-12-2023 | The correct PDF from the e-Gazette. The file first saved was a different notification. |
| IN-NDCT-2019 | New Drugs and Clinical Trials Rules, 2019 | The correct PDF from the e-Gazette. The CDSCO website copy is on hold because CDSCO requires permission. |

Until they are saved, the corpus has no Indian document and India-specific questions cannot be tested. **Who saves them, and by when?**

## 4. Held: seven documents that stay out (Q9, Q10, Q12)

| Document ID | Why it is held | What would release it |
|---|---|---|
| ICH-Q8R2 | The PDF has no legal notice, so the right to reuse is not stated | Written permission from ICH, client counsel's view, or an approved source list |
| ICH-Q1AR2 | No legal notice | Same; also re-check when the consolidated ICH Q1 is published (expected around Nov 2026) |
| ICH-Q3BR2 | No legal notice | Same as Q8 |
| ICH-Q6A | No legal notice | Same as Q8 |
| WHO-STAB-TRS1010 | WHO publications are CC BY-NC-SA 3.0 IGO, so commercial use needs WHO's permission | WHO permission |
| IN-BABE-2005 | CDSCO's copyright policy bars copying without permission, and no official copy was found online | CDSCO permission and an official copy |
| EMA-NITRO-QA-R23 | EMA's reuse terms were not checked | A check of EMA's legal notice (the team can do this) |

Effect on the product: questions that need these documents (stability testing, pharmaceutical development, specifications) must be answered "not covered by the approved sources". Golden questions GQ-15 and GQ-16 test exactly that.

## 5. The "Open Knowledge Sources" document disagrees with the licence rule

`docs/Open Knowledge Sources for Literature and Regulatory Research.md` came from `main`. It was written as a broad source list, not as a clearance. Compared with the manifest:

| Where in that document | What it says | What the manifest found |
|---|---|---|
| Section 5, line 128 | "FDA and ICH documents. Generally permissive" | Four of the ten ICH guidelines checked (Q1A, Q3B, Q6A, Q8) have **no legal notice**. They are held. |
| Section 7, line 149 (pilot corpus) | Lists ICH Q8, Q1A to F, Q3A to D, Q6A, M7 as the first documents to ingest | Q8, Q1A, Q3B and Q6A are held. Q1B to F, Q3A, Q10 and Q11 were never reviewed, so they are not cleared either. |
| Section 7, line 150 | Lists "EMA's nitrosamine Q&A" in the pilot corpus | EMA-NITRO-QA-R23 is held: reuse terms not confirmed. |
| Tier A, line 44 | WHO Publications: "Free" | The WHO stability report is non-commercial only (CC BY-NC-SA 3.0 IGO) and held. The document's own rule (line 143) says to exclude non-commercial content, so its pilot list should not include it. |
| Whole document | Covers the US, EU, UK, WHO, Japan and Canada | It never mentions India, CDSCO or the Indian Pharmacopoeia, although the target market is India. |

The manifest, not that document, is the source of truth for what may be ingested. **Decision needed:** add a note at the top of that document pointing to the manifest, or leave it as it is. It has not been edited.

## 6. Models (Q3, Q4)

| Role | Model | Licence as recorded | Where recorded | Status |
|---|---|---|---|---|
| Language model | `Qwen/Qwen2.5-14B-Instruct` | Not recorded. From memory Apache 2.0: **UNVERIFIED**, check the model card | `models_config.py` (a default, not a decision) | **Open** |
| Embeddings, production | `BAAI/bge-small-en-v1.5` (384 dimensions) | MIT | ADR-003 | Decided; licence not independently checked |
| Embeddings, CI and dev | `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions) | Apache 2.0 | ADR-003 | Decided. `models_config.py` still defaults to this model: align it. |
| Reranker | `BAAI/bge-reranker-base` | Not recorded | `models_config.py` | Deferred in the proposal |
| Text extraction | Apache Tika, pypdf fallback | Apache 2.0 (Tika), BSD (pypdf), as stated in ADR-003 | ADR-003 | Decided. Compose and CI now pin `apache/tika:4.2.0` (the version the reader was tested against), not the `2.9.2-full` the ADR names. |

Both embedding models give 384-dimension vectors, so the database column does not change if the model does. **How is the language model run during development?** The developer laptop (Windows, 16 GB RAM, no GPU) cannot run the planned server. Options: WSL2, a shared development server, or a hosted model used on public documents only. What is allowed depends on Q6 (cloud or on-premises).

## 7. Decisions to take at this meeting

| # | Decision | Recommendation |
|---|---|---|
| 1 | Adopt the reduced Sprint 2 scope (section 1)? | Yes, with the buffer. |
| 2 | Approve the 13 documents (section 2) for the test corpus? | Yes, by someone with authority, understanding that the basis is a team check and not legal advice. |
| 3 | Keep the 7 held documents out (section 4)? | Yes. Ask ICH or client counsel about the four ICH guidelines; check EMA's legal notice ourselves. |
| 4 | Who saves the 2 Indian PDFs, and by when? | Name an owner today. |
| 5 | Which language model, and how is it run in development? | Choose a candidate and record its licence from the model card before S2-09. |
| 6 | Where are original files stored (Q8)? | **Decided by the backend developer on 2 Nov:** a local folder behind a small interface ([ADR-007](../architecture/decisions/ADR-007-document-ingestion-and-storage.md)). Confirm, or name an object store. |
| 7 | Agree the answer contract (section 1). | Keep the current response shape; do not show `verified` and `confidence_score` as fact. |
| 8 | Which sign-in service (Q1)? | Decide, or confirm that development tokens are enough until after the Sprint 2 demo. |
| 9 | Who is the demo scientist (Q5)? | Name one, even for a short review of the 16 golden questions. |
| 10 | What time zone do the ceremonies use (Q14)? | State it in the plan. |

## 8. Sign-off

To be completed by the people who decide. Leaving a line blank means *not decided*.

| Item | Decision | Name and role | Date |
|---|---|---|---|
| Sprint 2 goal and scope | | | |
| Document set (section 2) | | | |
| Held documents stay out (section 4) | | | |
| Language model and its licence | | | |
| Storage for original files | | | |
