# Evaluation, Monitoring & Safety Suite

Automated validation pipelines to guarantee domain authenticity, zero-hallucination compliance, and observability.

## Key Capabilities

1. **Citation Faithfulness & Groundedness Benchmarks:**
   - Evaluates LLM responses against retrieved evidence chunks to verify that all claims strictly match source documents.
   - Detects and flags hallucinations, extrapolations, or unsupported inferences.

2. **Pharma Golden Dataset:**
   - Curated test set of 20+ representative formulation questions with ground-truth citations reviewed by formulation scientists.
   - Evaluated automatically in CI/CD before any prompt, chunking strategy, or model weight change is deployed.

3. **Observability & Metrics:**
   - OpenTelemetry instrumentation for end-to-end tracing across API, Haystack pipelines, Celery workers, and vLLM inference.
   - Prometheus metrics for retrieval latency, token throughput, failure rates, and citation verification pass ratios.
