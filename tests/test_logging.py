"""Tests for structured, redacted logging (S1-05).

The main promise: a sign-in token, a password, an e-mail address, or the text of a scientist's
question or an answer must never appear in a log line.
"""

import io
import json
import logging
import sys
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from pharma_ai.core.config import settings
from pharma_ai.core.logging_config import (
    ContextFilter,
    JsonFormatter,
    TextFormatter,
    configure_logging,
    current_trace_ids,
    request_id_var,
)
from pharma_ai.core.redaction import (
    MAX_TEXT_LENGTH,
    REDACTED,
    redact_mapping,
    redact_text,
)
from pharma_ai.main import app

client = TestClient(app)
MARKER = "ZX-9981"


class Captured:
    """Everything logged during a test, as the formatter wrote it."""

    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    @property
    def raw(self) -> str:
        return self._stream.getvalue()

    def records(self, logger: str | None = None) -> list[dict]:
        rows = [json.loads(line) for line in self.raw.splitlines() if line.strip()]
        return [r for r in rows if logger is None or r["logger"] == logger]


@pytest.fixture
def captured() -> Iterator[Captured]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield Captured(stream)
    root.removeHandler(handler)
    root.setLevel(previous_level)


def _bearer(make_token: Callable[..., str], **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(**kwargs)}"}


# --- redaction rules -------------------------------------------------------------------------


def test_a_sign_in_token_is_redacted(make_token: Callable[..., str]) -> None:
    token = make_token()
    cleaned = redact_text(f"rejected token {token} for caller")
    assert token not in cleaned
    assert "[REDACTED_JWT]" in cleaned
    assert cleaned.startswith("rejected token ")


def test_a_bearer_header_value_is_redacted() -> None:
    cleaned = redact_text("Authorization: Bearer abcdef1234567890")
    assert "abcdef1234567890" not in cleaned


def test_a_password_inside_a_web_address_is_redacted_but_the_host_is_kept() -> None:
    cleaned = redact_text("could not connect to postgresql://pharma_ai:hunter2@db:5432/pharma_ai")
    assert "hunter2" not in cleaned
    assert "db:5432/pharma_ai" in cleaned


def test_an_email_address_is_redacted() -> None:
    assert "jane.doe@example.com" not in redact_text("user jane.doe@example.com logged in")


def test_a_named_secret_in_text_is_redacted_but_its_name_is_kept() -> None:
    cleaned = redact_text("loaded AUTH_DEV_SECRET=abc123xyz and api_key: k-98765")
    assert "abc123xyz" not in cleaned and "k-98765" not in cleaned
    assert "AUTH_DEV_SECRET" in cleaned


def test_harmless_text_is_left_alone() -> None:
    text = "Listed 3 projects for PRJ-001 in 12.5 ms"
    assert redact_text(text) == text


def test_very_long_text_is_cut_short() -> None:
    cleaned = redact_text("a" * (MAX_TEXT_LENGTH * 3))
    assert len(cleaned) < MAX_TEXT_LENGTH + 100
    assert "truncated" in cleaned


def test_fields_are_redacted_by_name_and_nested_fields_too() -> None:
    cleaned = redact_mapping(
        {
            "Authorization": "Bearer something-long-enough",
            "query": "what is in our confidential tablet?",
            "answer": "According to the guidance ...",
            "nested": {"password": "pw", "ok": "fine", "items": [{"api_key": "k"}, "plain"]},
            "count": 3,
            "project_id": "PRJ-001",
        }
    )
    assert cleaned["Authorization"] == REDACTED
    assert cleaned["query"] == REDACTED and cleaned["answer"] == REDACTED
    assert cleaned["nested"]["password"] == REDACTED
    assert cleaned["nested"]["ok"] == "fine"
    assert cleaned["nested"]["items"] == [{"api_key": REDACTED}, "plain"]
    assert cleaned["count"] == 3 and cleaned["project_id"] == "PRJ-001"


def test_very_deep_nesting_does_not_crash_and_is_not_kept() -> None:
    deep: dict = {}
    node = deep
    for _ in range(30):
        node["x"] = {}
        node = node["x"]
    node["leaf"] = "secret-depth"
    assert "secret-depth" not in json.dumps(redact_mapping(deep))


# --- the log format --------------------------------------------------------------------------


def test_a_record_is_one_line_of_json_with_the_expected_fields(captured: Captured) -> None:
    logging.getLogger("pharma_ai.test").info("hello %s", "world", extra={"project_id": "PRJ-001"})
    (record,) = captured.records("pharma_ai.test")
    assert record["message"] == "hello world"
    assert record["level"] == "INFO"
    assert record["project_id"] == "PRJ-001"
    assert record["timestamp"].endswith("+00:00")


def test_the_servers_terminal_colour_codes_are_not_logged(captured: Captured) -> None:
    logging.getLogger("uvicorn.error").info(
        "Started server", extra={"color_message": "\x1b[36mStarted server\x1b[0m"}
    )
    (record,) = captured.records("uvicorn.error")
    assert "color_message" not in record and "\\u001b" not in captured.raw


def test_a_newline_in_a_message_cannot_forge_a_second_log_line(captured: Captured) -> None:
    logging.getLogger("pharma_ai.test").info('first\n{"level":"CRITICAL","message":"forged"}')
    assert len(captured.raw.strip().splitlines()) == 1
    (record,) = captured.records("pharma_ai.test")
    assert record["level"] == "INFO"


def test_extra_fields_are_redacted_in_the_output(captured: Captured) -> None:
    logging.getLogger("pharma_ai.test").info(
        "login", extra={"password": "pw123456", "email_seen": "jane@example.com"}
    )
    assert "pw123456" not in captured.raw and "jane@example.com" not in captured.raw


def test_an_exception_is_logged_without_the_secret_inside_its_message(
    captured: Captured,
) -> None:
    try:
        raise RuntimeError("cannot connect to postgresql://u:topsecret@db/x")
    except RuntimeError:
        logging.getLogger("pharma_ai.test").exception("startup failed")
    (record,) = captured.records("pharma_ai.test")
    assert "topsecret" not in captured.raw
    assert "RuntimeError" in record["exception"]


def test_the_request_id_is_added_while_one_is_set(captured: Captured) -> None:
    token = request_id_var.set("req-abc12345")
    try:
        logging.getLogger("pharma_ai.test").info("inside")
    finally:
        request_id_var.reset(token)
    logging.getLogger("pharma_ai.test").info("outside")
    inside, outside = captured.records("pharma_ai.test")
    assert inside["request_id"] == "req-abc12345"
    assert "request_id" not in outside


def test_the_text_format_is_readable_and_also_redacts() -> None:
    record = logging.LogRecord(
        "pharma_ai.test", logging.INFO, __file__, 1, "token=abc123456789 for %s", ("PRJ-001",), None
    )
    record.project_id = "PRJ-001"
    record.password = "pw123456"
    line = TextFormatter().format(record)
    assert "\n" not in line
    assert "PRJ-001" in line and "INFO" in line
    assert "abc123456789" not in line and "pw123456" not in line


# --- OpenTelemetry hook ----------------------------------------------------------------------


def test_trace_ids_are_added_when_a_trace_is_active(captured: Captured) -> None:
    pytest.importorskip("opentelemetry.trace")
    from opentelemetry import context, trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    span = NonRecordingSpan(
        SpanContext(trace_id=0xABC123, span_id=0xDEF456, is_remote=False, trace_flags=TraceFlags(1))
    )
    attached = context.attach(trace.set_span_in_context(span))
    try:
        logging.getLogger("pharma_ai.test").info("inside a trace")
    finally:
        context.detach(attached)
    logging.getLogger("pharma_ai.test").info("outside a trace")
    inside, outside = captured.records("pharma_ai.test")
    assert inside["trace_id"] == format(0xABC123, "032x")
    assert inside["span_id"] == format(0xDEF456, "016x")
    assert "trace_id" not in outside


def test_logging_works_without_opentelemetry_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    assert current_trace_ids() is None


# --- configuration ---------------------------------------------------------------------------


def _marked_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "_pharma_ai_handler", False)]


def test_configuring_twice_leaves_one_handler_and_applies_the_level() -> None:
    try:
        configure_logging("WARNING", "json")
        configure_logging("WARNING", "text")
        assert len(_marked_handlers()) == 1
        assert logging.getLogger().level == logging.WARNING
        assert isinstance(_marked_handlers()[0].formatter, TextFormatter)
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        configure_logging(settings.log_level, settings.log_format)


def test_a_bad_level_or_format_is_refused() -> None:
    with pytest.raises(ValueError):
        configure_logging("LOUD", "json")
    with pytest.raises(ValueError):
        configure_logging("INFO", "xml")


# --- request logging -------------------------------------------------------------------------


def test_each_request_writes_one_safe_line_and_returns_its_request_id(
    captured: Captured, make_token: Callable[..., str]
) -> None:
    headers = _bearer(make_token, sub="user-scientist-001", projects=["PRJ-001"])
    response = client.get("/api/v1/projects/PRJ-001", headers=headers)
    assert response.status_code == 200
    (record,) = captured.records("pharma_ai.request")
    assert record["method"] == "GET"
    assert record["path"] == "/api/v1/projects/PRJ-001"
    assert record["status"] == 200
    assert record["level"] == "INFO"
    assert record["actor_sub"] == "user-scientist-001"
    assert record["project_id"] == "PRJ-001"
    assert record["duration_ms"] >= 0
    assert record["request_id"] == response.headers["X-Request-ID"]
    # Neither the token nor the person's e-mail address is written.
    assert headers["Authorization"].split()[1] not in captured.raw
    assert "user-scientist-001@pharma.local" not in captured.raw


def test_the_query_string_is_never_logged(
    captured: Captured, make_token: Callable[..., str]
) -> None:
    headers = _bearer(make_token, sub="admin-001", roles=["admin"])
    response = client.get(
        "/api/v1/audit/events?actor=someone@example.com&page_size=5", headers=headers
    )
    assert response.status_code == 200
    (record,) = captured.records("pharma_ai.request")
    assert record["path"] == "/api/v1/audit/events"
    assert "actor=" not in json.dumps(record) and "someone" not in json.dumps(record)
    # Even a library that does log the full address (the HTTP test client does) is redacted.
    assert "someone@example.com" not in captured.raw


def test_refused_requests_are_logged_as_warnings(
    captured: Captured, make_token: Callable[..., str]
) -> None:
    assert client.get("/api/v1/projects").status_code == 401
    forbidden = client.get("/api/v1/projects/PRJ-002", headers=_bearer(make_token))
    assert forbidden.status_code == 403
    unauthenticated, refused = captured.records("pharma_ai.request")
    assert unauthenticated["status"] == 401 and unauthenticated["level"] == "WARNING"
    assert unauthenticated["actor_sub"] is None
    assert refused["status"] == 403 and refused["level"] == "WARNING"
    assert refused["project_id"] == "PRJ-002"


def test_a_caller_supplied_request_id_is_kept_only_if_it_looks_safe(captured: Captured) -> None:
    good = client.get("/health", headers={"X-Request-ID": "trace-12345678"})
    assert good.headers["X-Request-ID"] == "trace-12345678"
    bad = client.get("/health", headers={"X-Request-ID": 'x" injected {"level":"CRITICAL"}'})
    assert bad.headers["X-Request-ID"] != 'x" injected {"level":"CRITICAL"}'
    assert len(bad.headers["X-Request-ID"]) == 32
    assert "injected" not in captured.raw


def test_health_probes_are_logged_at_debug_only(captured: Captured) -> None:
    client.get("/health")
    (record,) = captured.records("pharma_ai.request")
    assert record["level"] == "DEBUG"


def test_a_research_question_and_its_answer_never_reach_the_logs(
    captured: Captured, make_token: Callable[..., str]
) -> None:
    question = f"Does excipient {MARKER} in a confidential tablet raise a nitrite risk? NDMA limit"
    response = client.post(
        "/api/v1/research/query", json={"query": question}, headers=_bearer(make_token)
    )
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert answer and "ng/day" in answer
    assert MARKER not in captured.raw
    assert "confidential tablet" not in captured.raw
    assert "ng/day" not in captured.raw


def test_a_crash_is_logged_with_its_secrets_removed(captured: Captured) -> None:
    @app.get("/__boom")
    def boom() -> None:
        raise RuntimeError("db at postgresql://u:hunter2@db/x is down")

    try:
        response = TestClient(app, raise_server_exceptions=False).get("/__boom")
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "endpoint", None) is not boom
        ]
    assert response.status_code == 500
    failed = [r for r in captured.records("pharma_ai.request") if r["message"] == "request failed"]
    assert len(failed) == 1 and failed[0]["level"] == "ERROR"
    assert "RuntimeError" in failed[0]["exception"]
    assert "hunter2" not in captured.raw
