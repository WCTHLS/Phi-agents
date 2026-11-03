"""Structured, redacted application logging (S1-05).

Plain language: *structured* logging writes each log entry as one line of JSON with named fields
(time, level, request id ...) instead of free text, so a machine can search and count them.
*Operational* logs are for developers to find and fix problems; they are not the audit trail
(see ADR-005), which records who did what and is kept in the database.

Every record passes through ``redact_*`` (see redaction.py) before it is written, so a token, a
password or the text of a scientist's question cannot leak into a log by accident.

OpenTelemetry hook: if the optional ``opentelemetry-api`` package is installed and a trace is
active, each record also carries ``trace_id`` and ``span_id`` so logs can be joined to traces.
Nothing here needs OpenTelemetry to run.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from pharma_ai.core.redaction import redact_mapping, redact_text

# The id of the request being served; set by the request-logging middleware.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else on a record came from ``extra={...}``.
_STANDARD_RECORD_FIELDS = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None))
) | {"message", "asctime", "taskName"}

_HANDLER_MARK = "_pharma_ai_handler"
_LOG_FORMATS = ("json", "text")


def current_trace_ids() -> tuple[str, str] | None:
    """The active OpenTelemetry trace and span ids as hex text, or None if there is no trace."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


class ContextFilter(logging.Filter):
    """Attach the request id and trace ids to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        ids = current_trace_ids()
        record.trace_id, record.span_id = ids if ids else (None, None)
        return True


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in vars(record).items()
        if key not in _STANDARD_RECORD_FIELDS
        and key not in {"request_id", "trace_id", "span_id", "color_message"}
        and not key.startswith("_")
    }


class _RedactingFormatter(logging.Formatter):
    """Shared work: build the safe parts of a record. Subclasses decide how to print them."""

    def _parts(self, record: logging.LogRecord) -> dict[str, Any]:
        parts: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for name in ("request_id", "trace_id", "span_id"):
            value = getattr(record, name, None)
            if value:
                parts[name] = redact_text(str(value))
        parts.update(redact_mapping(_extra_fields(record)))
        if record.exc_info:
            parts["exception"] = redact_text(self.formatException(record.exc_info), keep_end=True)
        elif record.exc_text:
            parts["exception"] = redact_text(record.exc_text, keep_end=True)
        return parts


class JsonFormatter(_RedactingFormatter):
    """One JSON object per line. JSON escapes newlines, so a user cannot forge extra log lines."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            self._parts(record), ensure_ascii=True, default=str, separators=(",", ":")
        )


class TextFormatter(_RedactingFormatter):
    """Readable single-line text for local development (LOG_FORMAT=text)."""

    def format(self, record: logging.LogRecord) -> str:
        parts = self._parts(record)
        head = f"{parts.pop('timestamp')} {parts.pop('level'):<8} {parts.pop('logger')}"
        message = str(parts.pop("message"))
        exception = parts.pop("exception", None)
        fields = " ".join(f"{key}={json.dumps(value, default=str)}" for key, value in parts.items())
        line = f"{head} {message}" + (f" {fields}" if fields else "")
        if exception:
            line += "\n" + str(exception)
        return line


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Send all application and server logs through the redacting formatter.

    Safe to call more than once: it replaces its own handler and leaves other handlers (for
    example the test runner's) alone. The server's own access log is switched off because the
    request-logging middleware writes one safer line per request (it never logs the query string).
    """
    if log_format not in _LOG_FORMATS:
        raise ValueError(f"LOG_FORMAT must be one of {_LOG_FORMATS}, not {log_format!r}")
    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        raise ValueError(f"LOG_LEVEL {level!r} is not a valid log level")

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # uvicorn installs its own plain-text handlers; route its messages through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
