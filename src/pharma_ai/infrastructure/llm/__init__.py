"""Language models behind a small interface (S2-09, ADR-010)."""

from pharma_ai.infrastructure.llm.base import ChatClient, LlmUnavailable

__all__ = ["ChatClient", "LlmUnavailable"]
