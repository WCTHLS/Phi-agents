"""Tests for the OpenAI-style chat client (S2-09). A fake network stands in for the provider.

These check what is sent, what is made of the reply, and that the API key and the prompt never
come back out in an error message.
"""

from __future__ import annotations

import json

import httpx
import pytest

from pharma_ai.infrastructure.llm import ChatClient, LlmUnavailable, OpenAICompatibleChatClient

KEY = "gsk_test_key_value_1234567890"
SECRET_PROMPT = "confidential-looking question text"
SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def client(
    handler, *, base_url: str = "https://api.groq.com/openai/v1"
) -> OpenAICompatibleChatClient:
    return OpenAICompatibleChatClient(
        provider="groq",
        api_key=KEY,
        model="test/model",
        base_url=base_url,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )


def ok(content: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def call(c: ChatClient) -> str:
    return c.complete_json(system="be exact", user=SECRET_PROMPT, schema_name="s", schema=SCHEMA)


# --- what is sent -------------------------------------------------------------------------------


def test_the_request_goes_to_the_chat_completions_address_with_the_key_in_a_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok('{"x": "y"}')

    assert call(client(handler)) == '{"x": "y"}'
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url) and KEY not in request.content.decode()


def test_the_body_asks_for_deterministic_json_that_matches_the_schema() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok("{}")

    call(client(handler))
    (body,) = bodies
    assert body["model"] == "test/model" and body["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "be exact"
    assert body["messages"][1]["content"] == SECRET_PROMPT
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "s", "schema": SCHEMA},
    }


def test_a_trailing_slash_on_the_base_address_is_harmless() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return ok("{}")

    call(client(handler, base_url="https://api.groq.com/openai/v1/"))
    assert urls == ["https://api.groq.com/openai/v1/chat/completions"]


def test_a_redirect_is_not_followed_so_the_key_cannot_be_sent_elsewhere() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    with pytest.raises(LlmUnavailable, match="HTTP 302"):
        call(client(handler))
    assert [r.url.host for r in requests] == ["api.groq.com"]


# --- what comes back ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_an_error_status_is_reported_by_number_without_the_body_the_key_or_the_prompt(
    status: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f"echo {SECRET_PROMPT} {KEY} secret-body")

    with pytest.raises(LlmUnavailable) as caught:
        call(client(handler))
    message = str(caught.value)
    assert f"HTTP {status}" in message
    for forbidden in (KEY, SECRET_PROMPT, "secret-body"):
        assert forbidden not in message


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_a_network_failure_is_reported_by_type_only(error: type[httpx.HTTPError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error(f"failed while sending {KEY} and {SECRET_PROMPT}")

    with pytest.raises(LlmUnavailable) as caught:
        call(client(handler))
    assert error.__name__ in str(caught.value)
    assert KEY not in str(caught.value) and SECRET_PROMPT not in str(caught.value)
    assert caught.value.__cause__ is None  # the original error text is not chained in either


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"choices": "nope"}),
        httpx.Response(200, json=["list"]),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": 42}}]}),
    ],
)
def test_a_reply_in_the_wrong_shape_is_refused(response: httpx.Response) -> None:
    with pytest.raises(LlmUnavailable):
        call(client(lambda request: response))


# --- the key stays secret -----------------------------------------------------------------------


def test_the_key_is_not_in_the_clients_name_or_representation() -> None:
    c = client(lambda request: ok("{}"))
    assert c.name == "groq:test/model"
    assert KEY not in repr(c) and KEY not in str(c.name)
    assert KEY not in str(vars(c).get("_url", ""))
