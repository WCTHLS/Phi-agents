"""A stand-in embedding model for tests: no download, no PyTorch, same interface.

It turns a text into 384 numbers by counting its words into buckets, so texts that share words get
vectors that point the same way. That is enough to test that the right passage is found; it says
nothing about the quality of the real model.
"""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Sequence

from pharma_ai.infrastructure.db.models import EMBEDDING_DIM
from pharma_ai.infrastructure.embeddings import Embedder


class FakeEmbedder(Embedder):
    def __init__(self, name: str = "fake-bag-of-words", max_input_tokens: int = 512) -> None:
        self._name = name
        self._max = max_input_tokens
        self.calls: list[list[str]] = []  # the texts of each embed() call

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    @property
    def max_input_tokens(self) -> int:
        return self._max

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


class FailingEmbedder(FakeEmbedder):
    """Raises when asked to embed, to check that a failure leaves the stored passages alone."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("the model fell over")
