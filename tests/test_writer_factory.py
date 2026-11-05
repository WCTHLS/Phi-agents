"""Tests for choosing the answer writer from the settings (S2-09).

Settings are built with ``_env_file=None`` so that a developer's own ``.env`` cannot change a result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from pharma_ai.core.config import Settings
from pharma_ai.features.research.answer_writer import QuotingAnswerWriter
from pharma_ai.features.research.llm_writer import LlmQuoteWriter
from pharma_ai.features.research.writer_factory import (
    DEFAULT_GROQ_MODEL,
    AnswerWriterConfigurationError,
    build_answer_writer,
)

KEY = "gsk_test_key_value_1234567890"
REPO_ROOT = Path(__file__).resolve().parents[1]


def cfg(**values) -> Settings:
    return Settings(_env_file=None, **values)


def groq(**values) -> Settings:
    return cfg(llm_provider="groq", groq_api_key=KEY, **values)


# --- the default --------------------------------------------------------------------------------


def test_the_test_suite_never_uses_a_real_language_model_whatever_is_in_the_developers_env() -> (
    None
):
    """Guard: a developer's own .env (with a real key) once made tests call the live service."""
    from pharma_ai.core.config import settings

    assert settings.llm_provider == "not_configured"
    assert settings.groq_api_key is None or not settings.groq_api_key.get_secret_value()
    from pharma_ai.features.research.writer_factory import get_answer_writer

    assert isinstance(get_answer_writer(), QuotingAnswerWriter)


def test_by_default_no_model_is_used_and_nothing_leaves_the_machine() -> None:
    assert isinstance(build_answer_writer(cfg()), QuotingAnswerWriter)
    assert cfg().llm_provider == "not_configured"


def test_a_key_alone_does_not_switch_a_model_on() -> None:
    assert isinstance(build_answer_writer(cfg(groq_api_key=KEY)), QuotingAnswerWriter)


def test_the_placeholder_values_already_in_env_example_are_accepted() -> None:
    settings = cfg(llm_provider="not_configured", llm_model="not_configured")
    assert isinstance(build_answer_writer(settings), QuotingAnswerWriter)


def test_every_value_in_env_example_is_accepted_by_the_settings() -> None:
    """A new setting must not break a .env copied from the example (it once nearly did)."""
    values = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if match and match.group(1).lower() in Settings.model_fields:
            values[match.group(1).lower()] = match.group(2)
    assert {"llm_provider", "llm_model", "groq_api_key", "groq_base_url"} <= set(values)
    settings = cfg(**values)
    assert isinstance(build_answer_writer(settings), QuotingAnswerWriter)


def test_an_unknown_provider_is_refused_by_the_settings() -> None:
    with pytest.raises(ValueError):
        cfg(llm_provider="skynet")


# --- groq ---------------------------------------------------------------------------------------


def test_groq_with_a_key_gives_the_model_backed_writer_with_the_default_model() -> None:
    built = build_answer_writer(groq())
    assert isinstance(built, LlmQuoteWriter)
    assert built.mode == f"groq:{DEFAULT_GROQ_MODEL}"


@pytest.mark.parametrize("placeholder", ["not_configured", "", "   "])
def test_the_default_model_is_used_when_none_is_named(placeholder: str) -> None:
    assert build_answer_writer(groq(llm_model=placeholder)).mode == f"groq:{DEFAULT_GROQ_MODEL}"


def test_a_named_model_is_used() -> None:
    assert (
        build_answer_writer(groq(llm_model="openai/gpt-oss-120b")).mode
        == "groq:openai/gpt-oss-120b"
    )


@pytest.mark.parametrize("key", [None, "", "   "])
def test_groq_without_a_key_stops_the_start_with_a_clear_message(key: str | None) -> None:
    with pytest.raises(AnswerWriterConfigurationError, match="GROQ_API_KEY"):
        build_answer_writer(cfg(llm_provider="groq", groq_api_key=key))


@pytest.mark.parametrize(
    "url",
    [
        "http://api.groq.com/openai/v1",
        "https://api.x.ai/v1",
        "https://api.groq.com.evil.example/openai/v1",
        "https://evil.example/api.groq.com/openai/v1",
        "https://api.groq.com@evil.example/openai/v1",
        "https://user:pw@api.groq.com/openai/v1",
        "https://api.groq.com:8443/openai/v1",
        "ftp://api.groq.com/openai/v1",
        "api.groq.com/openai/v1",
        "",
    ],
)
def test_an_address_other_than_https_api_groq_com_is_refused(url: str) -> None:
    with pytest.raises(AnswerWriterConfigurationError, match="GROQ_BASE_URL") as caught:
        build_answer_writer(groq(groq_base_url=url))
    assert KEY not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.groq.com/openai/v1",
        "https://api.groq.com/openai/v1/",
        "https://api.groq.com:443/openai/v1",
        "https://API.GROQ.COM/openai/v1",
    ],
)
def test_the_proper_groq_addresses_are_accepted(url: str) -> None:
    assert isinstance(build_answer_writer(groq(groq_base_url=url)), LlmQuoteWriter)


@pytest.mark.parametrize("timeout", [0, -1, -0.5])
def test_a_timeout_that_is_not_positive_is_refused(timeout: float) -> None:
    with pytest.raises(AnswerWriterConfigurationError, match="LLM_TIMEOUT_SECONDS"):
        build_answer_writer(groq(llm_timeout_seconds=timeout))


# --- the key stays secret -----------------------------------------------------------------------


def test_the_key_is_masked_when_the_settings_are_printed() -> None:
    settings = groq()
    assert KEY not in repr(settings) and KEY not in str(settings)
    assert KEY not in json.dumps(settings.model_dump(mode="json"))


def test_the_missing_key_message_never_contains_a_key() -> None:
    with pytest.raises(AnswerWriterConfigurationError) as caught:
        build_answer_writer(cfg(llm_provider="groq"))
    assert "gsk_" not in str(caught.value)


# --- end to end through the settings ------------------------------------------------------------


def test_the_built_writer_sends_the_key_and_model_it_was_configured_with() -> None:
    seen: list[httpx.Request] = []
    passage_text = "The acceptable intake for NDMA is 96 ng/day."

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = {"quotes": [{"passage": 1, "text": passage_text}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    built = build_answer_writer(
        groq(llm_model="openai/gpt-oss-120b"), transport=httpx.MockTransport(handler)
    )
    from test_llm_writer import hit

    draft = built.write("What is the acceptable intake for NDMA?", [hit(passage_text)])
    (request,) = seen
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content)["model"] == "openai/gpt-oss-120b"
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert [q.text for q in draft.quotes] == [passage_text] and draft.sent_to == "groq"
