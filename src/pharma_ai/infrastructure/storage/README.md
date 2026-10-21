# Storage Infrastructure (SeaweedFS)

Provides object storage client wrappers for managing binary document assets, OCR outputs, and generated report artifacts.

## Key Responsibilities

- **Raw Ingestion Store:** Persist incoming PDF guidelines, regulatory filings, and scientific papers with SHA-256 hashes.
- **Artifact & Extraction Store:** Store OCR output text, bounding box coordinates, and extracted tabular data.
- **Versioned Report Snapshots:** Store immutable, frozen snapshots of Product Development Reports (PDR), CTD Module 3 drafts, and regulatory filing packages upon scientist approval.
- **S3-Compatible API:** Wraps SeaweedFS via S3-compatible endpoints for easy local development and on-premises cluster deployment.
