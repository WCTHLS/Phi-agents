# Two-person delivery and collaboration plan

Use ownership to reduce collisions, but review each other's work. Swap roles if one person has stronger backend or scientific-data experience.

| Workstream | Primary owner | Pair/review owner | First deliverables |
|---|---|---|---|
| Scope, architecture, repo, API, configuration, CI, local database | Developer A (suggested: you) | Developer B reviews contracts and tests | Phase 0 scaffold, API health check, ADR, CI |
| Approved sources, document inventory, extraction tests, metadata/provenance | Developer B (teammate) | Developer A reviews security and interface | Source registry proposal, sample-document manifest, extraction evaluation |
| RAG contract and evaluation set | Shared; agree schema before coding in parallel | Each reviews the other's PR | 10–20 questions, expected evidence, citation format, pass/fail rubric |
| Integration and demo | Pair together | Both run test instructions independently | One end-to-end research question with source citations |

## Working agreement

- One GitHub issue per task; include acceptance criteria and an owner.
- Use branches such as `feat/source-registry` or `feat/health-api`; do not both edit the same core file without coordination.
- Open small pull requests; the other teammate reviews before merge.
- Keep secrets, client documents, model weights, and local data out of Git.
- Agree schemas/interfaces before parallel implementation: document metadata, retrieval result, answer+citation response, and error handling.
- A scientist/domain expert owns validation of scientific and regulatory conclusions; engineers do not self-certify them.
