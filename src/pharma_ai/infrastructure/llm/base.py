"""What the answer pipeline needs from a language model: one question in, one JSON text out."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LlmUnavailable(RuntimeError):
    """The model could not be reached or gave an unusable reply.

    The message is safe to log and to show: it never contains the key, the prompt or the reply.
    """


class ChatClient(ABC):
    """A language model that answers one prompt with JSON that matches a schema."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short provider and model name, for example ``groq:openai/gpt-oss-20b``."""

    @abstractmethod
    def complete_json(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> str:
        """Send the prompt and return the reply text (JSON). Raises ``LlmUnavailable``."""
