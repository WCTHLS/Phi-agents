"""The question-to-answer steps, without the web or database parts (S2-09).

search results -> an answer writer drafts quotes -> verification keeps only the quotes that are
word for word in a retrieved passage -> the answer text is built from what survived.

If nothing survives, the answer is withheld: the system says it found no evidence instead of
writing something. The route (``api/routes/research.py``) adds who asked, storage and auditing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pharma_ai.features.research.answer_writer import (
    QUOTING_MODE,
    AnswerWriter,
    Quote,
    coverage,
    evidence_gaps,
    render_quotes,
    verify,
)
from pharma_ai.features.research.search import FusedHit

NO_EVIDENCE_ANSWER = (
    "No verified evidence was found in this project's approved documents for this question, so no "
    "answer is given. Try rephrasing, or check that the relevant document has been added."
)


@dataclass(frozen=True)
class GroundedAnswer:
    answer: str
    verified: bool  # at least one quote, and every quote the writer offered was found in a passage
    withheld: bool  # no verified quote: the answer text is the "no evidence" message
    mode: str
    coverage: float  # share of the question's distinctive words found in the quotes (0 to 1)
    evidence_gaps: list[str]
    quotes: tuple[Quote, ...]  # the verified quotes
    rejected_quotes: int  # offered by the writer but not found in the passage they named
    citations: list[dict[str, Any]]  # one per verified quote, everything a citation needs
    sent_to: str | None = None  # the outside provider that saw the question and passages, if any


def _citations(quotes: Sequence[Quote], found: Sequence[FusedHit]) -> list[dict[str, Any]]:
    by_chunk = {f.hit.chunk_id: f.hit for f in found}
    out = []
    for quote in quotes:
        hit = by_chunk[quote.chunk_id]
        out.append(
            {
                "chunk_id": str(hit.chunk_id),
                "source_id": hit.source_id,
                "title": hit.title,
                "version_id": str(hit.version_id),
                "version_number": hit.version_number,
                "version_label": hit.version_label,
                "sha256": hit.sha256,
                "page_start": hit.page_start,
                "page_end": hit.page_end,
                "section": hit.section,
                "attribution": hit.attribution,
                "quoted_evidence": quote.text,
            }
        )
    return out


def answer_question(
    question: str, found: Sequence[FusedHit], writer: AnswerWriter
) -> GroundedAnswer:
    """Write, verify and assemble the answer for a question and the passages found for it."""
    passages = [f.hit for f in found]
    draft = writer.write(question, passages) if passages else None
    checked = verify(draft.quotes if draft else (), passages)
    notes = list(draft.notes) if draft else []
    gaps = [*notes, *evidence_gaps(question, passages)]
    mode = draft.mode if draft else writer.mode
    sent_to = draft.sent_to if draft else None

    if not checked.verified:
        if checked.rejected:
            reason = "None of the quotes offered could be found in the retrieved passages."
        elif mode == QUOTING_MODE:
            reason = "No retrieved passage shares a distinctive word with the question."
        else:
            reason = "The language model chose no passage as an answer to the question."
        return GroundedAnswer(
            answer=NO_EVIDENCE_ANSWER,
            verified=False,
            withheld=True,
            mode=mode,
            coverage=0.0,
            evidence_gaps=[*notes, reason, *gaps[len(notes) :]],
            quotes=(),
            rejected_quotes=len(checked.rejected),
            citations=[],
            sent_to=sent_to,
        )

    if checked.rejected:
        removed = (
            f"{len(checked.rejected)} quote(s) offered by the writer were not found in the "
            "passage they named and were removed."
        )
        gaps = [*notes, removed, *gaps[len(notes) :]]
    return GroundedAnswer(
        answer=render_quotes(checked.verified, passages),  # only verified quotes reach the user
        verified=not checked.rejected,
        withheld=False,
        mode=mode,
        coverage=coverage(question, checked.verified),
        evidence_gaps=gaps,
        quotes=checked.verified,
        rejected_quotes=len(checked.rejected),
        citations=_citations(checked.verified, found),
        sent_to=sent_to,
    )
