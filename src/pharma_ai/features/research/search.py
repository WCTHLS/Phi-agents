"""Hybrid passage search: meaning and keywords together (S2-07).

Plain language: two searches run over the same passages the caller may see.
- **Search by meaning** (embeddings) finds passages about the same thing even when the words differ.
- **Keyword search** finds passages that contain the question's exact terms: a guideline code, a
  substance name, a number.
Each gives its own ranked list. They cannot be added up directly, because their scores are on
different scales, so the lists are merged by *rank* (reciprocal rank fusion): a passage earns
``1 / (60 + rank)`` from each list it appears in, and the passages are ordered by the total. A
passage near the top of both lists wins; one that only a single search finds still gets in.

Everything the caller may see is decided in SQL, inside the two searches (see
``ChunkRepository._visible_chunks``): their projects, eligible sources, the named embedding model
and the newest version of each document. This module only merges what those searches return, so
it cannot widen what the caller sees.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from pharma_ai.infrastructure.db.repositories import ChunkHit, ChunkRepository
from pharma_ai.infrastructure.embeddings import Embedder

# The usual constant for reciprocal rank fusion. A larger value makes the lists count more equally;
# 60 comes from the original method and has not been tuned on this corpus.
RRF_K = 60
# How many passages each search contributes before merging.
CANDIDATE_DEPTH = 20


@dataclass(frozen=True)
class FusedHit:
    """A passage with the reasons it ranked where it did (for explanation and for the audit trail)."""

    hit: ChunkHit  # carries ``distance`` if search by meaning found it, ``keyword_score`` if keywords did
    score: float  # the fused score; only comparable within one search
    vector_rank: int | None  # 1 = best, None = search by meaning did not return it
    keyword_rank: int | None


def _ranks(hits: Sequence[ChunkHit]) -> dict[object, int]:
    """1-based rank of each passage in one list. If a passage were listed twice, its best rank counts."""
    ranks: dict[object, int] = {}
    for rank, hit in enumerate(hits, start=1):
        ranks.setdefault(hit.chunk_id, rank)
    return ranks


def fuse(
    vector_hits: Sequence[ChunkHit],
    keyword_hits: Sequence[ChunkHit],
    *,
    limit: int,
    k: int = RRF_K,
) -> list[FusedHit]:
    """Merge two ranked lists of passages into one, best first. The same input gives the same order."""
    vector_rank = _ranks(vector_hits)
    keyword_rank = _ranks(keyword_hits)

    merged: dict[object, ChunkHit] = {}
    for hit in vector_hits:
        merged[hit.chunk_id] = hit
    for hit in keyword_hits:
        known = merged.get(hit.chunk_id)
        # A passage found by both searches keeps both of its scores.
        merged[hit.chunk_id] = (
            replace(known, keyword_score=hit.keyword_score) if known is not None else hit
        )

    fused = []
    for chunk_id, hit in merged.items():
        v, w = vector_rank.get(chunk_id), keyword_rank.get(chunk_id)
        score = sum(1.0 / (k + rank) for rank in (v, w) if rank is not None)
        fused.append(FusedHit(hit=hit, score=score, vector_rank=v, keyword_rank=w))
    # Ties (the same total) go to the passage that ranked higher in either list. Any remaining tie
    # keeps the order the lists came in (the sort is stable), so the same input always gives the
    # same output.
    fused.sort(
        key=lambda f: (
            -f.score,
            min(r for r in (f.vector_rank, f.keyword_rank) if r is not None),
        )
    )
    return fused[:limit]


def hybrid_search(
    session: Session,
    *,
    question: str,
    vector: Sequence[float],
    projects: Iterable[str],
    embedding_model: str,
    limit: int = 5,
    latest_only: bool = True,
    depth: int = CANDIDATE_DEPTH,
) -> list[FusedHit]:
    """Run both searches for an already-embedded question and merge them.

    With no allowed projects, or a question with no words, the result is empty.
    """
    allowed = list(projects)
    if not allowed or not question.strip() or limit < 1:
        return []
    depth = max(depth, limit)
    repo = ChunkRepository(session)
    by_meaning = repo.search_nearest(
        vector, allowed, embedding_model=embedding_model, limit=depth, latest_only=latest_only
    )
    by_keywords = repo.search_keywords(
        question, allowed, embedding_model=embedding_model, limit=depth, latest_only=latest_only
    )
    return fuse(by_meaning, by_keywords, limit=limit)


class PassageSearch:
    """The search the answering workflow calls: a question in, ranked citable passages out."""

    def __init__(
        self, session: Session, embedder: Embedder, *, depth: int = CANDIDATE_DEPTH
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._depth = depth

    def search(
        self,
        question: str,
        allowed_project_ids: Iterable[str],
        *,
        limit: int = 5,
        latest_only: bool = True,
    ) -> list[FusedHit]:
        projects = list(allowed_project_ids)
        if not projects or not question.strip():
            return []  # nothing to search for or nowhere allowed to look: do not even embed
        vector = self._embedder.embed([question])[0]
        return hybrid_search(
            self._session,
            question=question,
            vector=vector,
            projects=projects,
            embedding_model=self._embedder.model_name,
            limit=limit,
            latest_only=latest_only,
            depth=self._depth,
        )
