"""Object storage for original documents and extracted text (S2-03, ADR-007).

Use ``get_object_store()``; it returns a local-folder store today. Other stores (for example an
S3-compatible one) only need to implement ``ObjectStore``.
"""

from __future__ import annotations

from functools import lru_cache

from pharma_ai.core.config import settings
from pharma_ai.infrastructure.storage.base import (
    InvalidKeyError,
    ObjectExistsError,
    ObjectNotFoundError,
    ObjectStore,
    StorageError,
)
from pharma_ai.infrastructure.storage.local import LocalFolderStore

__all__ = [
    "InvalidKeyError",
    "LocalFolderStore",
    "ObjectExistsError",
    "ObjectNotFoundError",
    "ObjectStore",
    "StorageError",
    "get_object_store",
]


@lru_cache
def get_object_store() -> ObjectStore:
    return LocalFolderStore(settings.storage_root)
