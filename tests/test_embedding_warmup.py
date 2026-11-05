"""Tests for loading the embedding model in the background when the API starts (S2-07).

No real model is loaded: the loader is replaced by a stand-in, so these run without a database.
"""

from __future__ import annotations

import threading

import pytest
from fake_embedder import FakeEmbedder
from fastapi.testclient import TestClient

import pharma_ai.main as main_module
from pharma_ai.api.routes import search as search_routes
from pharma_ai.core.config import settings
from pharma_ai.infrastructure.embeddings import EmbedderUnavailable

WAIT = 10  # seconds; only reached if something hangs


class Loader:
    """Stands in for the real model loader and counts how often it is asked to load."""

    def __init__(self, *, fail_first: bool = False, hold: threading.Event | None = None) -> None:
        self.loads = 0
        self.attempts = 0
        self._fail_first = fail_first
        self._hold = hold
        self.started = threading.Event()
        self.model = FakeEmbedder()

    def __call__(self, name: str, **_kwargs) -> FakeEmbedder:
        self.attempts += 1
        self.started.set()
        if self._hold is not None:
            assert self._hold.wait(WAIT)
        if self._fail_first and self.attempts == 1:
            raise EmbedderUnavailable("could not read C:\\secret\\place\\model.bin")
        self.loads += 1
        return self.model


@pytest.fixture
def loader(monkeypatch: pytest.MonkeyPatch) -> Loader:
    loader = Loader()
    monkeypatch.setattr(search_routes, "_embedder", None)
    monkeypatch.setattr(search_routes, "SentenceTransformerEmbedder", loader)
    return loader


def test_warm_up_is_off_in_the_test_suite_so_no_test_loads_a_model_by_accident() -> None:
    assert settings.embedding_warmup is False


def test_when_warm_up_is_off_nothing_is_started_or_loaded(
    loader: Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "embedding_warmup", False)
    assert search_routes.start_warmup() is None
    assert loader.attempts == 0


def test_when_warm_up_is_on_the_model_is_loaded_in_the_background_and_then_reused(
    loader: Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "embedding_warmup", True)
    thread = search_routes.start_warmup()
    assert thread is not None and thread.daemon  # a daemon thread never blocks shutdown
    thread.join(WAIT)
    assert not thread.is_alive() and loader.loads == 1
    assert search_routes.get_embedder() is loader.model  # a search reuses it ...
    assert loader.loads == 1  # ... without loading again


def test_starting_the_api_does_not_wait_for_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    slow = Loader(hold=release)
    monkeypatch.setattr(search_routes, "_embedder", None)
    monkeypatch.setattr(search_routes, "SentenceTransformerEmbedder", slow)
    monkeypatch.setattr(settings, "embedding_warmup", True)
    try:
        with TestClient(main_module.app) as client:  # runs the start-up and shut-down steps
            assert slow.started.wait(WAIT)  # loading has begun ...
            assert client.get("/health").json() == {"status": "ok"}  # ... and the API answers
            assert slow.loads == 0  # ... although the model is still loading
    finally:
        release.set()


def test_the_api_start_up_asks_for_the_warm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(main_module, "start_warmup", lambda: calls.append(1))
    with TestClient(main_module.app):
        assert calls == [1]


def test_a_search_that_arrives_during_the_load_waits_for_it_instead_of_loading_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    slow = Loader(hold=release)
    monkeypatch.setattr(search_routes, "_embedder", None)
    monkeypatch.setattr(search_routes, "SentenceTransformerEmbedder", slow)
    monkeypatch.setattr(settings, "embedding_warmup", True)
    warm = search_routes.start_warmup()
    assert warm is not None and slow.started.wait(WAIT)

    got: list[object] = []
    searcher = threading.Thread(target=lambda: got.append(search_routes.get_embedder()))
    searcher.start()
    release.set()
    searcher.join(WAIT)
    warm.join(WAIT)
    assert got == [slow.model]
    assert slow.attempts == 1 and slow.loads == 1  # one load served both


def test_if_the_model_cannot_be_loaded_the_api_still_starts_and_a_later_search_tries_again(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    failing_once = Loader(fail_first=True)
    monkeypatch.setattr(search_routes, "_embedder", None)
    monkeypatch.setattr(search_routes, "SentenceTransformerEmbedder", failing_once)
    monkeypatch.setattr(settings, "embedding_warmup", True)
    crashes: list[object] = []
    monkeypatch.setattr(threading, "excepthook", crashes.append)

    with caplog.at_level("INFO"):
        thread = search_routes.start_warmup()
        assert thread is not None
        thread.join(WAIT)

    assert crashes == []  # the thread ended quietly
    assert failing_once.loads == 0 and any("unavailable" in r.message for r in caplog.records)
    assert search_routes.get_embedder() is failing_once.model  # the next attempt is made and works
    assert failing_once.attempts == 2
