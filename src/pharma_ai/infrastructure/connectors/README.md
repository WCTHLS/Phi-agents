# Data Connectors

Provides read-only ingestion adapters for authentic regulatory sources and enterprise laboratory information systems.

## Modules

- `fda_ema/`: Connectors for FDA guidance docs, Orange Book, EMA/MHRA guidelines, and ICH quality standards.
- `eln_lims/`: Connectors for internal Electronic Lab Notebooks (ELN) and Laboratory Information Management Systems (LIMS).

All connectors enforce strict source allowlisting, read-only permissions, and provenance capture at acquisition time.
