"""Passage search endpoint: a question in, ranked citable passages out (S2-07, ADR-009).

- ``POST /api/v1/projects/{project_id}/search``: search the passages of one project's documents.
  Project members only.

Who may see what is decided in three places, and a request cannot widen any of them:
1. The project comes from the URL and the caller's membership is checked first, so a non-member
   gets 403 before anything is embedded or searched (and the refusal is audited, see main.py).
2. The search covers that one project only. The body has no project field and the caller's other
   projects are not added, so a question asked in PRJ-001 never returns PRJ-002 passages.
3. The visibility rules (eligible source, newest version, the current embedding model) are SQL in
   ``ChunkRepository._visible_chunks``.

Every search is written to the audit trail with a fingerprint of the question (not the question)
and the passages returned. If the trail cannot be written the request fails with 503 and the
passages are not returned.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from pharma_ai.core.config import settings
from pharma_ai.features.research.search import FusedHit, PassageSearch
from pharma_ai.infrastructure.audit.recorder import AuditRecorderDep, fingerprint_text
from pharma_ai.infrastructure.db.session import get_db_session
from pharma_ai.infrastructure.embeddings import Embedder, EmbedderUnavailable
from pharma_ai.infrastructure.embeddings.local_model import SentenceTransformerEmbedder
from pharma_ai.infrastructure.security.auth import CurrentPrincipal, ensure_project_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["search"])

SessionDep = Annotated[Session, Depends(get_db_session)]

MAX_QUESTION_CHARS = 2000
MAX_RESULTS = 20


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """The embedding model, loaded on the first search (about 30 s) and then kept.

    The lock stops two simultaneous first searches from each loading the model (about 1 GB
    each). If the model cannot be loaded the answer is 503 with a fixed message: the detail of
    the failure goes to the log, not to the caller. Tests replace this dependency with a
    stand-in model.
    """
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            try:
                _embedder = SentenceTransformerEmbedder(
                    settings.embedding_model, batch_size=settings.embedding_batch_size
                )
            except EmbedderUnavailable as exc:
                logger.error("Search is unavailable: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Search is unavailable: the embedding model could not be loaded",
                ) from exc
        return _embedder


def _warm_up() -> None:
    started = time.monotonic()
    try:
        embedder = get_embedder()
    except HTTPException:
        return  # get_embedder has already logged the reason; a search will try again later
    logger.info(
        "Embedding model %s is ready (%.1f s); searches will not wait for it",
        embedder.model_name,
        time.monotonic() - started,
    )


def start_warmup() -> threading.Thread | None:
    """Load the embedding model in the background (when EMBEDDING_WARMUP is on).

    Returns the thread, or None if warm-up is off. The thread never raises: if the model cannot be
    loaded the API still starts, and a search then answers 503 (and tries to load it again).
    A search that arrives during the load waits for it instead of starting a second load.
    """
    if not settings.embedding_warmup:
        return None
    thread = threading.Thread(target=_warm_up, name="embedding-warmup", daemon=True)
    thread.start()
    logger.info("Loading embedding model %s in the background", settings.embedding_model)
    return thread


def embedder_provider() -> Callable[[], Embedder]:
    """Hands the route a function that gets the model, instead of the model itself.

    FastAPI runs dependencies before the route body, so injecting the model directly would load it
    (and could answer 503) before the membership check. This way the route checks membership first
    and only then asks for the model. Tests replace this dependency to supply a stand-in model.
    """
    return get_embedder


EmbedderProviderDep = Annotated[Callable[[], Embedder], Depends(embedder_provider)]


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    limit: int = Field(default=5, ge=1, le=MAX_RESULTS)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("the question is empty")
        return value


class PassageOut(BaseModel):
    """One citable passage: everything ADR-002 needs to show next to a quotation."""

    rank: int
    chunk_id: uuid.UUID
    source_id: str
    title: str | None
    attribution: str | None  # must be shown next to any citation of this passage
    version_id: uuid.UUID
    version_number: int
    version_label: str | None
    sha256: str
    page_start: int
    page_end: int
    section: str | None
    text: str
    score: float  # the fused score; only comparable within this one response
    vector_rank: int | None  # place in the search by meaning (None = that search did not find it)
    keyword_rank: int | None


class SearchResponse(BaseModel):
    project_id: str
    question: str
    embedding_model: str
    results: list[PassageOut]


def _passage_out(rank: int, fused: FusedHit) -> PassageOut:
    hit = fused.hit
    return PassageOut(
        rank=rank,
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        title=hit.title,
        attribution=hit.attribution,
        version_id=hit.version_id,
        version_number=hit.version_number,
        version_label=hit.version_label,
        sha256=hit.sha256,
        page_start=hit.page_start,
        page_end=hit.page_end,
        section=hit.section,
        text=hit.text,
        score=fused.score,
        vector_rank=fused.vector_rank,
        keyword_rank=fused.keyword_rank,
    )


@router.post(
    "/{project_id}/search",
    response_model=SearchResponse,
    summary="Search a project's documents for passages that answer a question",
    description=(
        "Returns the best passages from the newest version of each eligible document in this "
        "project, each with the version, page and attribution a citation needs. Project members "
        "only. The question is not stored in the audit trail, only a fingerprint of it."
    ),
)
def search_project_passages(
    project_id: Annotated[str, Path(max_length=64)],
    body: SearchRequest,
    principal: CurrentPrincipal,
    audit: AuditRecorderDep,
    session: SessionDep,
    embedder_for_search: EmbedderProviderDep,
) -> SearchResponse:
    # Membership first: a refusal reveals nothing about which projects or documents exist, and
    # costs nothing (the embedding model is not even loaded for a non-member).
    ensure_project_access(principal, project_id)
    embedder = embedder_for_search()
    fingerprint = fingerprint_text(body.question)
    try:
        found = PassageSearch(session, embedder).search(
            body.question, [project_id], limit=body.limit
        )
    except Exception as exc:
        audit.failure(
            "PROJECT_SEARCHED",
            "project",
            resource_id=project_id,
            project_id=project_id,
            details={"question": fingerprint, "error_type": type(exc).__name__},
        )
        raise

    audit.success(
        "PROJECT_SEARCHED",
        "project",
        resource_id=project_id,
        project_id=project_id,
        details={
            "question": fingerprint,
            "limit": body.limit,
            "embedding_model": embedder.model_name,
            "result_count": len(found),
            "results": [
                {
                    "chunk_id": str(f.hit.chunk_id),
                    "source_id": f.hit.source_id,
                    "version_id": str(f.hit.version_id),
                }
                for f in found
            ],
        },
    )
    return SearchResponse(
        project_id=project_id,
        question=body.question,
        embedding_model=embedder.model_name,
        results=[_passage_out(rank, f) for rank, f in enumerate(found, start=1)],
    )
