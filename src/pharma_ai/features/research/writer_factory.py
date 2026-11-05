"""Choosing the answer writer from the settings (S2-09).

``LLM_PROVIDER=not_configured`` (the default): the quoting writer, nothing leaves this machine.
``LLM_PROVIDER=groq``: a Groq-hosted model picks the sentences to quote.

The settings are checked once, when the API starts, so a mistake stops the start instead of
failing on the first question: a missing key, or an address that is not ``https://api.groq.com``
(so the key cannot be sent anywhere else by a typo).
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

import httpx

from pharma_ai.core.config import Settings, settings
from pharma_ai.features.research.answer_writer import AnswerWriter, QuotingAnswerWriter
from pharma_ai.features.research.llm_writer import LlmQuoteWriter
from pharma_ai.infrastructure.llm import OpenAICompatibleChatClient

NOT_CONFIGURED = "not_configured"
# Open-weight, and one of the Groq models documented to support JSON-schema output. The model ids
# change: check https://console.groq.com/docs/models and the licence before relying on this one.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_HOST = "api.groq.com"


class AnswerWriterConfigurationError(RuntimeError):
    """The language-model settings are unsafe or incomplete. The API refuses to start."""


def _check_groq_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != GROQ_HOST or parts.port not in (None, 443):
        raise AnswerWriterConfigurationError(
            f"GROQ_BASE_URL must be an https address on {GROQ_HOST}; "
            "the API key is only ever sent there"
        )
    if parts.username or parts.password:
        raise AnswerWriterConfigurationError(
            "GROQ_BASE_URL must not contain a user name or password"
        )


def build_answer_writer(
    cfg: Settings, *, transport: httpx.BaseTransport | None = None
) -> AnswerWriter:
    """The writer the settings ask for. Raises ``AnswerWriterConfigurationError`` if unsafe."""
    if cfg.llm_provider == NOT_CONFIGURED:
        return QuotingAnswerWriter()

    # Only "groq" is left (the setting only allows these two values).
    key = cfg.groq_api_key.get_secret_value().strip() if cfg.groq_api_key else ""
    if not key:
        raise AnswerWriterConfigurationError(
            "LLM_PROVIDER=groq needs GROQ_API_KEY. Put it in your own .env, never in Git."
        )
    _check_groq_url(cfg.groq_base_url)
    if cfg.llm_timeout_seconds <= 0:
        raise AnswerWriterConfigurationError("LLM_TIMEOUT_SECONDS must be above zero")
    model = cfg.llm_model.strip()
    if not model or model == NOT_CONFIGURED:
        model = DEFAULT_GROQ_MODEL
    client = OpenAICompatibleChatClient(
        provider="groq",
        api_key=key,
        model=model,
        base_url=cfg.groq_base_url,
        timeout=cfg.llm_timeout_seconds,
        transport=transport,
    )
    return LlmQuoteWriter(client, provider="groq")


@lru_cache(maxsize=1)
def get_answer_writer() -> AnswerWriter:
    """The writer for this process (a FastAPI dependency). Tests replace it."""
    return build_answer_writer(settings)
