# Asynchronous Background Workers

Background distributed task processing queue powered by Celery and RabbitMQ.

## Worker Queues & Task Domains

1. **`ocr_extraction` Queue:**
   - Apache Tika parsing and Tesseract OCR execution for scanned PDF regulatory filings and multi-column literature.
   - Text, metadata, and table layout extraction.

2. **`embeddings_indexing` Queue:**
   - Document chunking, Sentence Transformers dense embedding calculation, and pgvector bulk upserts.
   - Periodic index synchronization and re-indexing.

3. **`scientific_doe` Queue:**
   - Multi-factor Design of Experiments (DOE) model fitting and optimization.
   - Long-running statistical simulations (`scipy`, `statsmodels`) and formulation response surface modeling.

4. **`report_assembly` Queue:**
   - Asynchronous generation of complex Product Development Reports (PDR) and eCTD Module 3 filing appendices.
