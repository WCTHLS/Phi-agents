"""Where original documents and their extracted text are kept (S2-03, ADR-007).

Plain language: an *object store* holds files under a name (a *key*). The rest of the application
only talks to this small interface, so the files can live in a local folder today and in an
S3-compatible store (for example SeaweedFS) later without touching the ingestion code.

Rules every implementation must follow:
- **Write once.** A stored original is never changed. Writing the same bytes under a key again does
  nothing; writing different bytes under an existing key is refused.
- **Safe keys.** A key is a relative path of plain segments (letters, digits, ``.``, ``_``, ``-``).
  Anything that could climb out of the store (``..``, a leading ``/``, backslashes) is refused.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

# One segment: starts with a letter or digit, then letters, digits, '.', '_' or '-'.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_KEY_LENGTH = 200


class StorageError(Exception):
    """Base class for storage problems."""


class InvalidKeyError(StorageError):
    """The key is not a safe relative path."""


class ObjectExistsError(StorageError):
    """Different bytes were written under a key that already holds an original."""


class ObjectNotFoundError(StorageError):
    """No object is stored under the key."""


def validate_key(key: str) -> str:
    """Return ``key`` if it is a safe relative path, otherwise raise InvalidKeyError."""
    if not key or len(key) > MAX_KEY_LENGTH:
        raise InvalidKeyError("key is empty or too long")
    segments = key.split("/")
    if not all(_SEGMENT.match(segment) for segment in segments):
        raise InvalidKeyError(f"unsafe key: {key!r}")
    return key


class ObjectStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> bool:
        """Store ``data`` under ``key``. True if newly written, False if the same bytes were
        already there. Raises ObjectExistsError if different bytes are already there."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key`` or raise ObjectNotFoundError."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True if something is stored under ``key``."""
