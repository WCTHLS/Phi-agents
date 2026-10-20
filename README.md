# Phi-agents

An AI-enabled platform that assists formulation scientists across the pharmaceutical product development lifecycle, from initial research through development documentation and regulatory filing support.

The platform is **pharma-domain driven**, built on **authentic and validated information**, and designed with **regulatory compliance in mind**. It is not a generic AI assistant.

> **Status:** Phase 0 (Align and Prepare). The repository currently contains documentation and architecture diagrams only. No code has been written yet.

## Key capabilities

| Capability | What it provides |
|---|---|
| Literature and regulatory research | Consolidates information from trusted sources (FDA, EMA, MHRA, scientific literature) to establish product development background and critical considerations. |
| Formulation development assistance | Guidance on product criticalities, formulation approaches, process considerations, excipient compatibility and associated risk areas. |
| Experimentation and DOE | Uses experimental results and structured data to guide later development decisions and support a more systematic Design of Experiments. |
| Quality and development risks | Covers bioavailability, stability and impurities, including nitrosamine and elemental impurities. |
| Documentation and regulatory support | Integrates with ELN and document management systems and helps prepare the Product Development Report and regulatory documentation. |

## Design principles

- **Validated sources only.** Every source is approved in a source register (license, owner, version, hash, approver) before ingestion.
- **Cited answers.** Responses link back to an exact passage, source and version.
- **Human in the loop.** Scientists verify critical extracted values, review guidance, approve study plans and approve final documents.
- **On-premises, no cloud AI.** Models, data and identity run in the client environment, with egress controls.
- **Permission-aware retrieval.** Access follows the client's project and document permissions.
- **Auditable.** Access events, decisions and approved report snapshots are recorded and versioned.

## Architecture

The system is layered:

1. **Access and application:** researcher workspace, identity and roles, application API, workflow queue.
2. **Pharmaceutical development services:** regulatory and scientific research, formulation and process guidance, quality and development risks, experimentation and DOE, Product Development Report drafting.
3. **Shared AI and scientific services:** document parsing and OCR, local model serving, permission-aware evidence retrieval, scientific Python and statistics.
4. **Human review and safeguards:** scientist and quality review, access control, provenance and egress safeguards.
5. **Controlled data and records:** audit records, versioned source and report files, a relational and vector store.

Diagrams are in [docs/System Design](docs/System%20Design/):

- [System design](docs/System%20Design/system-design.png)
- [Overall flow](docs/System%20Design/overall-flow.png)
- [Use-case agent architecture](docs/System%20Design/use-case-agent-architecture.png)
- [Phased implementation](docs/System%20Design/phased-implementation.png)
- [On-premises integrated agent architecture](docs/System%20Design/on-premises-integrated-agent-architecture.png)
- [On-premises technical architecture](docs/System%20Design/on-premises-technical-architecture.png)
- [Open-source on-premises technical architecture](docs/System%20Design/open-source-on-premises-technical-architecture.png)

### Candidate stack

These are candidates and have not been finalized.

| Area | Candidates |
|---|---|
| User interface | React, Vite, Nginx |
| Identity | Keycloak (or the client's existing SSO) |
| API and workflow | FastAPI, Celery, RabbitMQ |
| Data | PostgreSQL with pgvector, SeaweedFS |
| Local AI | vLLM with a license-approved open-weight model, Sentence Transformers |
| Document processing | Apache Tika, Tesseract |
| Scientific computing | Python scientific tools |
| Operations | OpenTelemetry, Prometheus, Grafana, Podman on Linux |

## Phased roadmap

| Phase | Focus | Exit gate |
|---|---|---|
| 0. Align and Prepare | Scope, users, data owners, access, security, compliance and success measures | Scope and access approved |
| 1. Trusted Research | Approved sources, citations, provenance, scientist verification | Source quality and workflow accepted |
| 2. Formulation and DOE | Formulation and process guidance, structured results, next studies | Guidance and proposals reviewed |
| 3. Experiment Feedback and Risk | Verified outcomes and risk review: bioavailability, stability, nitrosamine, elemental impurities | Results and decisions reviewed |
| 4. Documentation Integration | ELN and document integration, development report and regulatory drafts | Drafts and traceability reviewed |
| 5. Evaluate and Expand | Accuracy, usefulness, workflow fit, controls, traceability, operations | Evidence-based expansion decision |

Enterprise controls apply throughout all phases: identity and least privilege, source permissions and provenance, scientist approval and audit trail, data quality, security and testing, and operational monitoring.

## Documentation

- [Open Knowledge Sources for Literature and Regulatory Research](docs/Open%20Knowledge%20Sources%20for%20Literature%20and%20Regulatory%20Research.md): catalog of freely accessible regulatory, literature, compound and patent sources, with licensing notes, ingestion guidelines, a recommended pilot corpus and a source register template.

Licensing caveats to keep in mind before ingesting anything:

- **PubMed Central:** use only the commercial-use subset.
- **MHRA:** permission is required to reuse content, so treat it as "ask first".
- **DrugBank:** non-commercial use only, so it needs a commercial license.
- **Paid resources** such as USP-NF, Ph. Eur. and Lhasa are client-supplied.

Confirm current terms with the client's legal team before ingestion.
