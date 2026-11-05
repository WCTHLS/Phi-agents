"""Tests for scripts/check_llm.py (S2-09). A fake network stands in for the provider."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from pharma_ai.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_llm", REPO_ROOT / "scripts" / "check_llm.py")
assert spec and spec.loader
check_llm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_llm)

KEY = "gsk_test_key_value_1234567890"


def cfg(**values) -> Settings:
    return Settings(_env_file=None, llm_provider="groq", groq_api_key=KEY, **values)


def network(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def reply(text: str) -> httpx.Response:
    quotes = {"quotes": [{"passage": 1, "text": text}]}
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(quotes)}}]})


def test_a_working_model_gives_ok_and_exit_code_0(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_llm.main(
        [], cfg=cfg(), transport=network(lambda r: reply(check_llm.EXPECTED_QUOTE))
    )
    out = capsys.readouterr().out
    assert code == 0 and "OK: the model answered" in out
    assert check_llm.EXPECTED_QUOTE in out and "Sent to provider: groq" in out


@pytest.mark.parametrize("status", [401, 429, 500])
def test_a_failing_provider_gives_a_fallback_message_with_the_status(
    status: int, capsys: pytest.CaptureFixture[str]
) -> None:
    code = check_llm.main([], cfg=cfg(), transport=network(lambda r: httpx.Response(status)))
    out = capsys.readouterr().out
    assert code == 1 and "FALLBACK" in out and f"HTTP {status}" in out


def test_a_made_up_quote_is_not_reported_as_ok(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_llm.main(
        [],
        cfg=cfg(),
        transport=network(lambda r: reply("The acceptable intake for NDMA is 960 ng/day.")),
    )
    out = capsys.readouterr().out
    assert code == 1 and "NO ANSWER" in out and "960" not in out.split("Note:")[0]


def test_without_a_provider_nothing_is_sent_and_it_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing should be sent")

    code = check_llm.main([], cfg=Settings(_env_file=None), transport=network(must_not_be_called))
    assert code == 2 and "not_configured" in capsys.readouterr().out


def test_a_missing_key_is_reported_not_crashed(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_llm.main([], cfg=Settings(_env_file=None, llm_provider="groq"))
    assert code == 2 and "GROQ_API_KEY" in capsys.readouterr().out


def test_the_key_is_never_printed(capsys: pytest.CaptureFixture[str]) -> None:
    check_llm.main([], cfg=cfg(), transport=network(lambda r: reply(check_llm.EXPECTED_QUOTE)))
    check_llm.main([], cfg=cfg(), transport=network(lambda r: httpx.Response(401, text=KEY)))
    captured = capsys.readouterr()
    assert KEY not in captured.out and KEY not in captured.err
