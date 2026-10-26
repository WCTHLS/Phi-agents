# Initial task board

## Phase 0 — In progress
- [ ] Manager/team agrees MVP use case and acceptance criteria.
- [ ] Confirm source access/permissions and local-vs-cloud model policy.
- [ ] Confirm developer ownership and repository permissions.
- [ ] Run API and tests on both developers' Windows environments.
- [ ] Review ADR-001; confirm or adjust technology choices.

## Phase 1 — Ready after Phase 0 decisions
- [ ] Build approved-source manifest with provenance fields. (Draft: data/sources/corpus_manifest.csv; approval pending, see ADR-002.)
- [ ] Select 5–10 permitted sample documents. (15 eligible documents in the manifest; 12 in the corpus, 3 deferred because their source sites would not load.)
- [ ] Define 10–20 questions and scientist-approved expected evidence. (16 draft questions in evals/golden_questions.csv; machine-checked, not scientist-approved.)
- [ ] Implement document parsing and extraction-quality tests.
- [ ] Implement chunking, embeddings, and pgvector indexing.
- [ ] Implement permission-aware retrieval and evidence citations.
- [ ] Add Haystack orchestration and an approved LLM adapter.
- [ ] Evaluate retrieval and grounded answers; document known failures.
