"""Redaction of sensitive values before they reach a log (S1-05).

Plain language: *redaction* means replacing a sensitive value with a marker such as ``[REDACTED]``
so that the log line still shows that something was there, but not what it was. Logs are read by
developers, copied into tickets and shipped to other systems, so they must never carry passwords,
sign-in tokens, personal details or the text of a confidential question or document.

Two layers work together, and both are applied by the log formatter to every record (including
records from third-party libraries):

1. **By name.** A field called ``authorization`` or ``password`` is replaced whatever it holds, and
   so is a field that carries user or document content (``query``, ``answer``, ``text`` ...).
2. **By shape.** Inside any text, things that look like a sign-in token, a password in a web
   address, an e-mail address or ``secret=...`` are replaced even when nobody named them.

This is a default-deny starting point. What the client allows in logs is still to be confirmed
(see docs/security/data-handling.md), so unclear cases are redacted rather than kept.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
MAX_TEXT_LENGTH = 2000
MAX_EXCEPTION_LENGTH = 6000
MAX_DEPTH = 6

# Field names that always hold secrets or credentials.
_SECRET_KEY = re.compile(
    r"(authorization|cookie|password|passwd|secret|token|api[_-]?key|credential|private[_-]?key"
    r"|session|jwt|bearer)",
    re.IGNORECASE,
)
# Field names that carry what a scientist asked, what the system answered, or document text.
_CONTENT_KEY = re.compile(
    r"^(query|question|prompt|answer|completion|response_text|quoted_evidence|passage|chunk|"
    r"document_text|content|body|text|message_text|rationale)$",
    re.IGNORECASE,
)

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_NAMED_SECRET = re.compile(
    r"(?i)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key|authorization)[\w-]*)"
    r"(\s*[=:]\s*)(?!\[REDACTED)\S+"
)


def is_sensitive_key(key: object) -> bool:
    """True if a field with this name must never be logged with its real value."""
    name = str(key)
    return bool(_SECRET_KEY.search(name) or _CONTENT_KEY.match(name))


def redact_text(text: str, *, keep_end: bool = False) -> str:
    """Replace secrets, tokens, web-address passwords and e-mail addresses inside ``text``.

    Very long text is cut. Keep the start by default; ``keep_end`` keeps the end instead, which is
    what a traceback needs (the error is named on its last lines).
    """
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _URL_PASSWORD.sub(lambda m: f"{m.group(1)}:{REDACTED}@", text)
    text = _NAMED_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    return truncate(text, MAX_EXCEPTION_LENGTH if keep_end else MAX_TEXT_LENGTH, keep_end=keep_end)


def truncate(text: str, limit: int = MAX_TEXT_LENGTH, *, keep_end: bool = False) -> str:
    """Cap very long text so a log line can never carry a whole document."""
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    if keep_end:
        return f"[truncated {cut} characters]...{text[-limit:]}"
    return f"{text[:limit]}...[truncated {cut} characters]"


def redact_value(value: Any, *, depth: int = 0) -> Any:
    """Redact a value of any kind: text, numbers, lists and nested mappings."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if depth >= MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(value, depth=depth + 1)
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(item, depth=depth + 1) for item in value]
    return redact_text(str(value))


def redact_mapping(data: Mapping[Any, Any], *, depth: int = 0) -> dict[str, Any]:
    """Redact a mapping: sensitive field names lose their value, the rest is redacted by shape."""
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(key):
            clean[str(key)] = REDACTED
        else:
            clean[str(key)] = redact_value(value, depth=depth)
    return clean
