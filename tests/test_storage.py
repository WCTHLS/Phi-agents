"""Tests for the original-document store (S2-03, ADR-007)."""

import os
from pathlib import Path

import pytest

from pharma_ai.infrastructure.storage import (
    InvalidKeyError,
    LocalFolderStore,
    ObjectExistsError,
    ObjectNotFoundError,
    StorageError,
)
from pharma_ai.infrastructure.storage.base import MAX_KEY_LENGTH, validate_key

KEY = "originals/ICH-Q3DR2/" + "a" * 64 + ".pdf"


@pytest.fixture
def store(tmp_path: Path) -> LocalFolderStore:
    return LocalFolderStore(tmp_path / "files")


def test_a_stored_file_can_be_read_back_and_lives_under_the_root(
    store: LocalFolderStore,
) -> None:
    assert store.put(KEY, b"%PDF-1.4 original") is True
    assert store.get(KEY) == b"%PDF-1.4 original"
    assert store.exists(KEY)
    assert (store.root / KEY).is_file()


def test_storing_the_same_bytes_again_changes_nothing(store: LocalFolderStore) -> None:
    store.put(KEY, b"same")
    assert store.put(KEY, b"same") is False
    assert store.get(KEY) == b"same"


def test_different_bytes_under_an_existing_key_are_refused_and_the_original_is_kept(
    store: LocalFolderStore,
) -> None:
    store.put(KEY, b"original")
    with pytest.raises(ObjectExistsError):
        store.put(KEY, b"tampered")
    assert store.get(KEY) == b"original"


def test_reading_something_that_is_not_there_says_so(store: LocalFolderStore) -> None:
    assert not store.exists(KEY)
    with pytest.raises(ObjectNotFoundError):
        store.get(KEY)


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "../outside.pdf",
        "originals/../../outside.pdf",
        "/absolute.pdf",
        "C:/windows/file.pdf",
        "originals\\file.pdf",
        "originals//file.pdf",
        "originals/.hidden",
        ".hidden",
        "originals/file name.pdf",
        "originals/fi\x00le.pdf",
        "originals/" + "x" * MAX_KEY_LENGTH,
    ],
)
def test_unsafe_keys_are_refused_everywhere(store: LocalFolderStore, bad_key: str) -> None:
    with pytest.raises(InvalidKeyError):
        validate_key(bad_key)
    with pytest.raises(InvalidKeyError):
        store.put(bad_key, b"x")
    with pytest.raises(InvalidKeyError):
        store.get(bad_key)
    with pytest.raises(InvalidKeyError):
        store.exists(bad_key)


def test_nothing_is_written_outside_the_root_for_an_unsafe_key(
    tmp_path: Path, store: LocalFolderStore
) -> None:
    with pytest.raises(InvalidKeyError):
        store.put("../escaped.pdf", b"x")
    assert not (tmp_path / "escaped.pdf").exists()


def test_a_link_inside_the_store_cannot_lead_outside_it(
    tmp_path: Path, store: LocalFolderStore
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    store.root.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, store.root / "originals", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow creating symbolic links")
    with pytest.raises(StorageError):
        store.put("originals/escaped.pdf", b"x")
    assert not any(outside.iterdir())


def test_a_failed_write_leaves_no_partial_file(
    store: LocalFolderStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        store.put(KEY, b"data")
    monkeypatch.undo()
    assert not store.exists(KEY)
    assert not any(p.name.startswith(".part-") for p in store.root.rglob("*"))


def test_a_successful_write_leaves_no_temporary_file(store: LocalFolderStore) -> None:
    store.put(KEY, b"data")
    assert not any(p.name.startswith(".part-") for p in store.root.rglob("*"))
