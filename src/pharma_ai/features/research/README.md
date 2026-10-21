# Trusted research (Phase 1)

Planned modules:
- `source_registry.py`: allowlisted regulatory/scientific sources and acquisition policy.
- `ingestion.py`: controlled loading of permitted documents and metadata.
- `parsing.py`: extraction of text/tables; OCR only where documents are scanned.
- `chunking.py` and `indexing.py`: searchable evidence units, embeddings, and pgvector index.
- `retrieval.py`: permission-aware retrieval with source/version metadata.
- `answering.py`: evidence-grounded answer generation with citations and limitations.

First acceptance target: answer a small, agreed set of questions from a curated document set and let a scientist open every cited source. Do not ingest unapproved client or copyrighted content.
