"""Object store backed by a local folder (S2-03, ADR-007)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pharma_ai.infrastructure.storage.base import (
    ObjectExistsError,
    ObjectNotFoundError,
    ObjectStore,
    StorageError,
    validate_key,
)


class LocalFolderStore(ObjectStore):
    """Keeps each object as a file under ``root``; a key's ``/`` separators become sub-folders.

    A file is written to a temporary name and then moved into place, so a crash cannot leave a
    half-written original behind. Two writers storing the same key at once is harmless here
    because keys are built from the file's own fingerprint, so both hold the same bytes.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        path = (self._root / validate_key(key)).resolve()
        # Defence in depth: even a key that passed validation must stay inside the root
        # (for example if a folder inside the store is a link to somewhere else).
        if self._root not in path.parents:
            raise StorageError(f"key resolves outside the store: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> bool:
        path = self._path(key)
        if path.exists():
            if path.read_bytes() == data:
                return False
            raise ObjectExistsError(f"{key!r} already holds a different original")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".part-")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return True

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
