"""A chat client for any provider that speaks the OpenAI-style ``/chat/completions`` protocol.

Groq (the first provider used here) does. The client sends one prompt, asks for JSON that matches a
schema, and returns the reply text. It is careful about three things:

- **The key** is sent only in the ``Authorization`` header, never logged, never in an error message
  and never in ``repr``.
- **Redirects are not followed.** A redirect could carry the key to another host.
- **Errors say what happened, not what was said:** a status code or an error type, never the
  prompt (which holds document text and the question) or the reply.
"""

from __future__ import annotations

from typing import Any

import httpx

from pharma_ai.infrastructure.llm.base import ChatClient, LlmUnavailable


class OpenAICompatibleChatClient(ChatClient):
    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout
        self._transport = transport  # tests pass a fake network here

    @property
    def name(self) -> str:
        return f"{self._provider}:{self._model}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"

    def complete_json(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> str:
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema},
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            # The type of the error is enough (timeout, connection refused); its text is not logged.
            raise LlmUnavailable(
                f"{self._provider} request failed ({type(exc).__name__})"
            ) from None
        if response.status_code != httpx.codes.OK:
            raise LlmUnavailable(f"{self._provider} answered with HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LlmUnavailable(f"{self._provider} sent a reply in an unexpected shape") from None
        if not isinstance(content, str) or not content.strip():
            raise LlmUnavailable(f"{self._provider} sent an empty reply")
        return content
