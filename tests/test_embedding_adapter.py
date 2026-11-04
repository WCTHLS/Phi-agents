"""Tests for the sentence-transformers adapter, with the library replaced by a stand-in.

The real model is not downloaded in the test suite. These tests check what the adapter itself does:
the size check, the unit-length setting, token counting and the helpful error when the library is
missing. The real model is checked by running the indexing script (see ADR-008).
"""

from __future__ import annotations

import sys
import types
from typing import Any, ClassVar

import pytest

from pharma_ai.infrastructure.db.models import EMBEDDING_DIM
from pharma_ai.infrastructure.embeddings import EmbedderUnavailable
from pharma_ai.infrastructure.embeddings.local_model import SentenceTransformerEmbedder


class _Tokenizer:
    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        ids = list(range(len(text.split())))
        return ids + [0, 0] if add_special_tokens else ids


class _Model:
    max_seq_length = 512
    tokenizer = _Tokenizer()
    dimension = EMBEDDING_DIM
    last_call: ClassVar[dict[str, Any]] = {}

    def __init__(self, name: str, device: str = "cpu") -> None:
        if name == "no-such-model":
            raise OSError("not found on the hub")
        self.name, self.device = name, device

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        type(self).last_call = {"texts": texts, **kwargs}
        return [[0.5] * self.dimension for _ in texts]


@pytest.fixture
def stub_library(monkeypatch: pytest.MonkeyPatch) -> type[_Model]:
    _Model.dimension = EMBEDDING_DIM
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _Model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return _Model


def test_the_adapter_reports_what_the_service_needs(stub_library: type[_Model]) -> None:
    embedder = SentenceTransformerEmbedder("some/model", batch_size=7)
    assert embedder.model_name == "some/model"
    assert embedder.dimension == EMBEDDING_DIM and embedder.max_input_tokens == 512
    assert embedder.count_tokens("one two three") == 3  # the model's start and end markers excluded


def test_vectors_are_requested_at_unit_length_in_batches(stub_library: type[_Model]) -> None:
    embedder = SentenceTransformerEmbedder("some/model", batch_size=7)
    vectors = embedder.embed(["a b", "c d"])
    assert len(vectors) == 2 and len(vectors[0]) == EMBEDDING_DIM
    assert isinstance(vectors[0][0], float)
    assert stub_library.last_call["normalize_embeddings"] is True
    assert stub_library.last_call["batch_size"] == 7
    assert embedder.embed([]) == []


def test_a_model_of_the_wrong_size_is_refused(stub_library: type[_Model]) -> None:
    stub_library.dimension = 768
    with pytest.raises(EmbedderUnavailable, match="768.*384"):
        SentenceTransformerEmbedder("big/model")


def test_a_model_that_cannot_be_loaded_gives_a_clear_error(stub_library: type[_Model]) -> None:
    with pytest.raises(EmbedderUnavailable, match="could not load.*no-such-model"):
        SentenceTransformerEmbedder("no-such-model")


def test_a_missing_library_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # makes the import fail
    with pytest.raises(EmbedderUnavailable, match=r"\.\[embeddings\]"):
        SentenceTransformerEmbedder("some/model")
