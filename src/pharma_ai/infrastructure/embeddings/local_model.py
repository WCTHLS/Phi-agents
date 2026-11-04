"""Embeddings from a model that runs on this machine, via sentence-transformers (S2-06).

The library is imported only when the model is created, so the API and the test suite do not need
it (it pulls in PyTorch, several hundred megabytes). Install it with
``pip install -e ".[embeddings]"``. The first use downloads the model's public weights from
Hugging Face into the local cache; no document text is sent anywhere, the model runs here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pharma_ai.infrastructure.db.models import EMBEDDING_DIM
from pharma_ai.infrastructure.embeddings.base import Embedder, EmbedderUnavailable


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str, *, batch_size: int = 32, device: str = "cpu") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbedderUnavailable(
                'sentence-transformers is not installed. Run: pip install -e ".[embeddings]"'
            ) from exc
        try:
            self._model: Any = SentenceTransformer(model_name, device=device)
        except Exception as exc:  # the library raises many kinds of error (network, files, format)
            raise EmbedderUnavailable(f"could not load the model {model_name!r}: {exc}") from exc
        # Newer versions of the library renamed this method; the project allows both.
        get_dimension = getattr(
            self._model, "get_embedding_dimension", self._model.get_sentence_embedding_dimension
        )
        dimension = get_dimension()
        if dimension != EMBEDDING_DIM:
            raise EmbedderUnavailable(
                f"{model_name} gives vectors of {dimension} numbers; the database column holds "
                f"{EMBEDDING_DIM}. Changing the model size needs a migration (see ADR-003)."
            )
        self._name = model_name
        self._batch_size = batch_size

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    @property
    def max_input_tokens(self) -> int:
        return int(self._model.max_seq_length)

    def count_tokens(self, text: str) -> int:
        return len(self._model.tokenizer.encode(text, add_special_tokens=False))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,  # unit length: cosine distance is then well behaved
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in vector] for vector in vectors]
