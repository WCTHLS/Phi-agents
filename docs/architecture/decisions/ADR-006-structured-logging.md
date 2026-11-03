# ADR-006: Structured, redacted application logging

- **Status:** Proposed (S1-05). The redaction rules are a strict default; what the client permits in logs is not yet confirmed (see `docs/security/data-handling.md`: "what must never appear in logs?").
- **Plain-language terms:** A *log* is the running diary a program keeps so developers can find and fix problems. *Structured* means each entry is one line of JSON with named fields, so tools can search and count them. *Redaction* means replacing a sensitive value with `[REDACTED]` before it is written. A log is **not** the audit trail: the audit trail (ADR-005) is the permanent, tamper-resistant record of who did what, kept in the database; logs are temporary and may be copied into tickets or shipped to other systems.
- **Decision:**
  - **Standard library, no new framework.** Python's built-in `logging` with a small JSON formatter (`core/logging_config.py`). `LOG_LEVEL` and `LOG_FORMAT` (`json` or `text`) are settings; the server's own messages go through the same formatter.
  - **Redaction happens in the formatter, on every record.** It cannot be forgotten by the code that logs, and it covers libraries too. Two layers (`core/redaction.py`): by field name (`token`, `password`, `authorization`, `query`, `answer`, `text` ... lose their value) and by shape inside any text (sign-in tokens, passwords in web addresses, e-mail addresses, `secret=...`). Long text is cut, and error traces keep their end, where the error is named.
  - **One line per request** (`api/middleware.py`): method, path, status, duration, the signed-in person's stable id (`sub`, never the e-mail), the project in the path, and a `request_id` that is returned in `X-Request-ID`. A caller's own id is accepted only if it is 8 to 64 letters, digits, `.`, `_` or `-`.
  - **Never logged:** the query string, request and response bodies, headers, and the text of questions, answers and documents. Health probes are logged at DEBUG. The server's built-in access log is switched off because it prints the full URL.
  - **OpenTelemetry hook.** If `opentelemetry-api` is installed and a trace is active, records carry `trace_id` and `span_id`. Nothing else in OpenTelemetry (exporters, instrumentation) is set up yet.
- **Why:** The sprint plan asks for "structured logging with OpenTelemetry hooks (redacted)". The product handles confidential formulation questions and regulated records, so the safe default is to log metadata and never content.
- **Trade-offs and limits:**
  - **Over-redaction is accepted.** A field named `token_count` or `text` is redacted too. Add an explicit allow-list when something harmless is needed.
  - **Redaction by shape is best effort.** It catches common secret formats, not every possible one. Code must still avoid passing secrets and content to the logger.
  - **No central log store, metrics or dashboards yet.** Lines go to the process's standard error; the platform decides where they end up.
  - **Not tested against real client data.** The rules were checked with made-up secrets and a live server, not with the client's own definition of sensitive content.
- **Revisit when:** the client defines what may and may not appear in logs; a log shipper or OpenTelemetry exporter is added; ingestion (S2-03) starts handling document text, which must be kept out of logs by the same rules.
