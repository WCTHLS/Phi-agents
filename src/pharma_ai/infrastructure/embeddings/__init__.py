"""Embedding models behind a small interface (S2-06, ADR-008)."""

from pharma_ai.infrastructure.embeddings.base import SPECIAL_TOKENS, Embedder, EmbedderUnavailable

__all__ = ["SPECIAL_TOKENS", "Embedder", "EmbedderUnavailable"]
