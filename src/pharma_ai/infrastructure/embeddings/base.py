"""The interface to an embedding model (S2-06, ADR-008).

Plain language: an *embedding* turns a piece of text into a list of numbers (here 384 of them) so
that passages with similar meaning get similar numbers. Searching is then a comparison of numbers,
which the database does. The model that makes the numbers is behind this small interface, so the
real model, a hosted one, or a stand-in used in tests can be swapped without touching the rest.

Two things must hold for search to be meaningful:
- Passages and questions must be embedded by the **same model**. Every stored vector records the
  model's name for that reason, and a different model means the passages are embedded again.
- The vector length must match the database column (``EMBEDDING_DIM``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

# BERT-style models wrap the text in a start and an end marker, which count towards their limit.
SPECIAL_TOKENS = 2


class EmbedderUnavailable(Exception):
    """The embedding model cannot be used (library missing, model not found, wrong size)."""


class Embedder(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stored with every vector, so vectors from different models are never mixed."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Length of each vector."""

    @property
    @abstractmethod
    def max_input_tokens(self) -> int:
        """Longest text (in tokens, including the model's own start and end markers) it reads in
        full. Longer text is silently cut off by the model, which would hide the end of a passage."""

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Number of tokens the model sees in ``text``, without its start and end markers."""

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the same order."""
