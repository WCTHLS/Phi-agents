# FDA & EMA/MHRA Connector

Read-only ingestion client for authentic regulatory documentation and public health authority releases.

## Scope & Target Sources

- **FDA Guidance Documents:** CMC guidelines, bioequivalence recommendations, nitrosamine risk assessments, and elemental impurity limits (ICH Q3D).
- **EMA / MHRA Guidelines:** Quality and formulation guidelines, product-specific bioequivalence notes, and European Pharmacopoeia monographs.
- **ICH Guidelines:** Q8 (Pharmaceutical Development), Q9 (Quality Risk Management), Q10 (Pharmaceutical Quality System).

## Key Guarantees

- Captures publication date, document identification number, effective status, and source URL/identifier.
- Stores raw files directly in SeaweedFS with SHA-256 integrity verification.
- Re-checks version status periodically to flag superseded regulatory guidance.
