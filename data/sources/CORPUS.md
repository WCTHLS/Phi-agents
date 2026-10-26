# Pharma AI Platform – Sprint 2 test corpus (open-licence)

Public documents for building and testing ingestion, chunking, retrieval and cited answers in Sprint 2 (S2-03, S2-06, S2-07). Checked 24–25 Oct 2026.

**Rule for this sprint:** only documents whose reuse terms were read from a primary source are in the core set. Anything with no stated terms, or with restrictive terms, is on hold until someone with authority (the client, counsel or the publisher) approves it. This matches the PRD's own risk R-04 and requirement FR-08 (only eligible content is indexed), and the manifest's `eligible` column is the flag ingestion should honour.

## Files

| File | Purpose |
|---|---|
| `data/sources/corpus_manifest.csv` | 22 documents, with licence status, reuse basis, attribution text, links and flags. The `tier` column says where each stands: 12 `core` (in the corpus), 3 `deferred` (`eligible=yes`, but not downloaded for now) and 7 `hold` (`eligible=no`) |
| `scripts/fetch_corpus.py` | Downloads the core PDFs, fingerprints them, checks the text layer, writes the register |
| `evals/golden_questions.csv` | 16 draft test questions, each with a machine-checkable evidence spec |
| `evals/verify_questions.py` | Finds the evidence page for each question in the downloaded PDFs and flags questions that don't hold up |
| `data/corpus/originals/<doc_id>.pdf` | Created by the script: versioned originals (never edit). Not committed |
| `data/corpus/corpus_register.csv` | Created by the script: manifest plus sha256, size, pages, text layer, retrieval time. Not committed |

## How to run

Run from the repository root.

```bash
pip install pypdf
python scripts/fetch_corpus.py          # core set (12)
python evals/verify_questions.py        # after the PDFs are in place
```

Three documents are **deferred** (`tier=deferred`): they were not downloaded because their source sites would not load, so the fetch script skips them. Their licence findings are unchanged and they stay `eligible=yes`. See the Deferred section below for how to bring them back.

## Core set (12) – all eligible, in the corpus

| # | doc_id | Document | Version | Licence basis |
|---|---|---|---|---|
| 1 | ICH-Q3CR9 | Residual Solvents | Q3C(R9), 2024 | ICH notice in PDF |
| 2 | ICH-Q3DR2 | Elemental Impurities | Q3D(R2), 2022 | ICH notice in PDF |
| 3 | ICH-M7R2 | Mutagenic (DNA reactive) Impurities | M7(R2), 2023 | ICH notice in PDF |
| 4 | ICH-Q9R1 | Quality Risk Management | Q9(R1), Step 4 18 Jan 2023 | ICH notice in PDF |
| 5 | ICH-M9 | BCS-based Biowaivers | M9, 2019 | ICH notice in PDF |
| 6 | ICH-M13A | Bioequivalence, IR Solid Oral Dosage Forms | M13A, 2024 | ICH notice in PDF |
| 7 | FDA-DISS-1997 | Dissolution Testing of IR Solid Oral Dosage Forms | Final, Aug 1997 | FDA site policy |
| 8 | FDA-DISS-HS-2018 | Dissolution Testing and Acceptance Criteria, High Solubility Drugs | Final, Aug 2018 | FDA site policy |
| 9 | FDA-BA-2022 | Bioavailability Studies Submitted in NDAs or INDs | Final, Apr 2022 | FDA site policy |
| 10 | FDA-ANDA-STAB-2014 | ANDAs: Stability Testing, Questions and Answers | Final, May 2014 | FDA site policy |
| 11 | FDA-PV-2011 | Process Validation: General Principles and Practices | Final, Jan 2011 | FDA site policy |
| 12 | FDA-SIZE-2022 | Size, Shape and Physical Attributes of Generic Tablets and Capsules | Rev. 1, Oct 2022 | FDA site policy |

No document in the current corpus is Indian, and the corpus has no FDA nitrosamine guidance. That is a limit of what was downloadable for now, not a judgement of what matters for India.

## Deferred (3) – eligible, not in the corpus for now

These stay `eligible=yes` because their licence findings are unchanged. They were deferred on 26 Oct 2026 because their source sites would not load. Nothing may be ingested from them until the PDFs are saved by hand and the rows are set back to `tier=core`.

| doc_id | Document | Licence basis | Why not downloaded |
|---|---|---|---|
| FDA-NITRO-2024 | Control of Nitrosamine Impurities in Human Drugs | FDA site policy | fda.gov answered scripted requests with a redirect loop (HTTP 301 plus bot-protection cookies). Do not work around the block in the script |
| IN-SCHEDM-2023 | Revised Schedule M (GMP) | Copyright Act s.52(1)(q)(i) | e-Gazette (egazette.gov.in) would not load |
| IN-NDCT-2019 | New Drugs and Clinical Trials Rules (original text) | Copyright Act s.52(1)(q)(i) | e-Gazette would not load |

To bring them back:
1. Save each PDF into `data/corpus/originals/` as `<doc_id>.pdf`. Use the Gazette copy for the two Indian documents (search G.S.R. 922(E) of 28-12-2023 and G.S.R. 227(E) of 19-03-2019), not a website or third-party copy.
2. Set `tier` back to `core` in the manifest and update the counts in this guide.
3. Run `python scripts/fetch_corpus.py`, then `python evals/verify_questions.py`.

## On hold (7) – do not ingest

| doc_id | Why | What unblocks it |
|---|---|---|
| ICH-Q8R2, ICH-Q3BR2, ICH-Q6A | The PDFs carry no legal notice, so reuse rights are not stated | Ask ICH, or client counsel; or the client's approved list |
| ICH-Q1AR2 | Same; also being replaced by a consolidated Q1 (Step 4 expected Nov 2026), which may carry the current notice | Re-check when Q1 is adopted |
| WHO-STAB-TRS1010 | WHO publications are CC BY-NC-SA 3.0 IGO: commercial use needs WHO's permission | WHO permission request |
| IN-BABE-2005 | CDSCO bars reproduction without permission, and no official copy was found | CDSCO permission, via the client |
| EMA-NITRO-QA-R23 | EMA terms were not checked | Check EMA's legal notice; the Q&A is also revised often |

The Indian Pharmacopoeia (IP 2026) is also out: it is licensed content, and nobody on the team can say what the client's licence allows.

## What was checked, and what wasn't

| Source | What was read | Result | Still unverified |
|---|---|---|---|
| ICH PDFs (Q3C, Q3D, M7, Q9, M9, M13A) | The "Legal notice" inside each PDF | Reuse allowed with ICH copyright acknowledged; changes labelled; no implied endorsement | Read through a fetch tool, not by a person: open page 2 of one PDF and confirm. Does not cover third-party content inside a document |
| ICH PDFs (Q1A, Q3B, Q6A, Q8) | Same | No notice found | Whether ICH grants terms elsewhere |
| FDA | FDA website policy | Site content is not copyrighted; credit appreciated | The policy doesn't name guidance PDFs; they sit on fda.gov |
| Gazette notifications | Section 52(1)(q) text on copyright.gov.in | Reproducing Gazette matter, other than an Act, isn't infringement | This is a lay reading, not legal advice; applies to the Gazette copy only |
| WHO | WHO's publishing policy | Non-commercial licence | – |
| CDSCO | CDSCO copyright policy | Permission needed to reproduce | – |
| Journal articles | PMC and Europe PMC | Two candidates were CC BY-NC-ND (not usable for a commercial project); the rest were blocked by captchas and rate limits | No verified CC BY articles |

**Attribution duties** (store the manifest's `attribution` text with each document and show it in the citation panel, S2-08): for ICH documents acknowledge ICH's copyright, label any adaptation and never imply ICH endorsement; for Gazette items name the notification. Chunking and embedding are likely "use", but if the platform displays adapted text (summaries), label it as adapted.

## Golden questions and review

`evals/golden_questions.csv` holds 16 draft questions: table values, definitions, a code trap (Q3C vs Q3D), Indian rules, a version trap, a reasoning question and two gaps (where the right answer is "not covered by approved sources"). Nobody with domain knowledge has reviewed them, so each question moves through four levels:

1. **Draft:** written from general knowledge, not from the documents. The `draft_answer_from_memory` column may contain errors and is never a source.
2. **Machine-checked:** `evals/verify_questions.py` finds the page where the evidence appears and prints the passage. Questions it can't support (`NOT_FOUND`) must be fixed or dropped. For the gap questions it confirms the terms are absent from the corpus. Results go to `evals/golden_questions_checked.csv`, which Git ignores.
3. **Developer-read (about an hour):** a developer reads each evidence passage and confirms the answer follows from it. This needs reading, not domain knowledge. Replace the draft answer with what the passage says.
4. **Scientist-reviewed:** pending. This matches the plan to build the first phase and then bring in a scientist. Until then, label the demo "questions not yet reviewed by a domain expert".

The checker proves the passage exists and where. It cannot show whether the question matters to a scientist or whether the answer is scientifically right. The PRD's Phase 1 exit gate ("reviewed research background") needs that scientist review.

### Machine check (25-26 Oct 2026, 12 documents in the corpus)

| Result | Questions |
|---|---|
| OK (evidence page found) | GQ-01, 02, 04, 05, 07, 08, 09, 14 |
| OK for the two gap questions: terms not found in the 12 corpus documents | GQ-15, GQ-16. Valid for this corpus only; they must be re-checked if the deferred documents are added, because Schedule M or the NDCT Rules could mention the terms |
| `NOT_FOUND` | GQ-06. The 2018 FDA guidance (page 8) says the dissolution criterion is "Q=80% in 30 minutes"; the question's pattern and its draft answer say 85%. The question needs fixing by its owner |
| `DOC_MISSING` | GQ-03, GQ-10 (need the deferred `FDA-NITRO-2024`), GQ-11 (needs the deferred `IN-SCHEDM-2023`), GQ-12, GQ-13 (need the deferred `IN-NDCT-2019`) |

No question was marked `CHECK`. The questions have not been edited. The checker reads the question file, not the manifest, so it keeps reporting `DOC_MISSING` for the five questions that depend on deferred documents. Those five cannot be answered or scored until the documents are restored, or the questions are changed or dropped by their owner.

## Built-in test cases

- **Near-identical codes:** Q3C vs Q3D. NDMA vs NDEA is deferred with the FDA nitrosamine guidance.
- **Table values:** PDE tables (Q3D) and solvent classes (Q3C). The acceptable-intake tables (FDA nitrosamine) are deferred.
- **Statutory text (deferred):** the NDCT Rules and Schedule M have a very different structure from guidance, so they would test structure-aware chunking. Not available until the Gazette PDFs are restored.
- **Version trap (deferred):** the NDCT text predates later amendments; answers must say so (GQ-13).
- **Deliberate gaps:** no India-specific stability condition (the ICH Q1A(R2) it would defer to is on hold) and no IP monographs; answers must say "not covered".
- **Older PDFs:** the 1997 FDA dissolution guidance was suspected to need OCR (reading text from scanned images). The 25 Oct 2026 run found a text layer on all 12 downloaded PDFs, including this one, so OCR is not needed for them.

## Gaps still open

- **Scientific literature:** none in the corpus. Add 3–5 articles whose licence is plain CC BY, checking each article's own licence line. Many journals use CC BY-NC-ND, which does not fit.
- **Indian rules:** Schedule M and the NDCT Rules are deferred, so the corpus has no Indian document. Restore them (see Deferred) before testing India-specific questions.
- **Nitrosamine limits:** the FDA nitrosamine guidance is deferred, so questions about NDMA and NDEA limits cannot be tested yet.
- **Pilot product:** product-specific sources need a chosen product.
- **Stability for India:** the ICH Q1A(R2) and WHO documents that would answer this are on hold. Ask the client for access or approval.

## Check before ingesting

- [ ] Run the fetch script: 12 automatic downloads. The 3 deferred documents are skipped.
- [ ] Open page 2 of one ICH PDF and confirm the legal notice reads as described.
- [ ] Open each PDF's first page and confirm title and version match the manifest.
- [ ] Run `python evals/verify_questions.py`; fix every `NOT_FOUND` and `DOC_MISSING`; look at every `CHECK`.
- [ ] Make ingestion skip anything with `eligible` other than `yes`.
