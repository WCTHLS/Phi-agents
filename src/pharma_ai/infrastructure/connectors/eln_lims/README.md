# ELN & LIMS Connector

Integration connector for enterprise Electronic Lab Notebooks (ELN) and Laboratory Information Management Systems (LIMS).

## Scope & Functionality

- **Structured Experiment Ingestion:** Ingest completed DOE formulation runs, assay results, dissolution profiles, and stability data.
- **Bi-directional Reference:** Link AI-assisted study designs directly to planned laboratory protocol IDs in the ELN.
- **Read-Only Enclave:** Ingestion operations are strictly read-only and pull only authorized project datasets.
- **Data Model Normalization:** Maps proprietary ELN/LIMS experiment schemas into standardized tabular data formats suitable for statistical analysis (`pandas` / `scipy` / `statsmodels`).
