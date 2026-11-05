"""Putting the right page on each citation (S2-09).

A passage can span two pages, and the search records the page range of the whole passage. A citation
should name the page of the quoted sentence, so each quote is looked up in the stored page text of
the version it came from. If that text is missing or unreadable, the citation keeps the passage's
own page range and nothing else changes.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from pharma_ai.features.documents.extraction import PageText
from pharma_ai.features.documents.provenance import locate_quote
from pharma_ai.infrastructure.db.models import DocumentVersion
from pharma_ai.infrastructure.storage import ObjectStore

logger = logging.getLogger(__name__)


def _load_pages(session: Session, store: ObjectStore, version_id: str) -> list[PageText] | None:
    try:
        version = session.get(DocumentVersion, uuid.UUID(version_id))
        if version is None or not version.extracted_key:
            return None
        payload = json.loads(store.get(version.extracted_key))
        return [PageText(int(p["page"]), p["text"]) for p in payload["pages"]]
    except Exception as exc:  # noqa: BLE001 - any problem here must only cost exactness
        logger.warning("Page text unavailable for version %s (%s)", version_id, type(exc).__name__)
        return None


def refine_citation_pages(
    session: Session, store: ObjectStore, citations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The same citations, with ``page_start`` and ``page_end`` set to where each quote is."""
    pages_by_version: dict[str, list[PageText] | None] = {}
    refined = []
    for citation in citations:
        version_id = citation["version_id"]
        if version_id not in pages_by_version:
            pages_by_version[version_id] = _load_pages(session, store, version_id)
        pages = pages_by_version[version_id]
        if pages:
            start, end = locate_quote(
                pages,
                citation["quoted_evidence"],
                citation["page_start"],
                citation["page_end"],
            )
            citation = {**citation, "page_start": start, "page_end": end}
        refined.append(citation)
    return refined
