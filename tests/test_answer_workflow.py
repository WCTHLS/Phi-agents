"""Tests for writing a grounded answer: quoting, verification, and withholding (S2-09).

No database and no model: passages are built by hand. The point of these tests is that nothing
reaches the user unless it is word for word in a passage that was retrieved.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest

from pharma_ai.features.research.answer_workflow import NO_EVIDENCE_ANSWER, answer_question
from pharma_ai.features.research.answer_writer import (
    MAX_QUOTES,
    MAX_SENTENCES_PER_PASSAGE,
    AnswerWriter,
    DraftAnswer,
    Quote,
    QuotingAnswerWriter,
    content_terms,
    coverage,
    evidence_gaps,
    normalise,
    verify,
)
from pharma_ai.features.research.search import FusedHit
from pharma_ai.infrastructure.db.repositories import ChunkHit

NDMA = (
    "Table 1 lists the limits. The acceptable intake for NDMA is 96 ng/day. "
    "The acceptable intake for NDEA is 26.5 ng/day. Other nitrosamines follow a different rule."
)
NDMA_SENTENCE = "The acceptable intake for NDMA is 96 ng/day."


def hit(
    text: str,
    *,
    title: str = "FDA Nitrosamine Guidance",
    section: str | None = "APPENDIX B",
    page: int = 43,
) -> ChunkHit:
    return ChunkHit(
        chunk_id=uuid.uuid4(),
        source_id="FDA-NITRO-2024",
        version_id=uuid.uuid4(),
        version_label="Revision 1",
        version_number=1,
        sha256="a" * 64,
        page_start=page,
        page_end=page,
        section=section,
        text=text,
        attribution="U.S. Food and Drug Administration",
        title=title,
    )


def fused(*hits: ChunkHit) -> list[FusedHit]:
    return [
        FusedHit(hit=h, score=1.0 / (60 + i), vector_rank=i, keyword_rank=None)
        for i, h in enumerate(hits, start=1)
    ]


class FixedWriter(AnswerWriter):
    """A writer that offers exactly the quotes it is given, honest or not."""

    mode = "test-writer"

    def __init__(self, *quotes: Quote) -> None:
        self._quotes = quotes

    def write(self, question: str, passages: Sequence[ChunkHit]) -> DraftAnswer:
        return DraftAnswer(quotes=self._quotes, mode=self.mode)


# --- the words of a question --------------------------------------------------------------------


def test_content_terms_drop_filler_fold_plurals_keep_numbers_and_repeats_once() -> None:
    assert content_terms("What are the acceptable limits for NDMA in 2 tablets, NDMA?") == [
        "acceptable",
        "limit",
        "ndma",
        "2",
        "tablet",
    ]


def test_content_terms_of_a_question_with_nothing_distinctive_is_empty() -> None:
    assert content_terms("What is it?") == []


# --- quoting ------------------------------------------------------------------------------------


def test_the_quoting_writer_quotes_the_sentence_that_shares_the_most_words() -> None:
    passage = hit(NDMA)
    draft = QuotingAnswerWriter().write("acceptable intake for NDMA", [passage])
    assert draft.quotes[0].text == NDMA_SENTENCE
    assert all(q.chunk_id == passage.chunk_id for q in draft.quotes)
    assert draft.mode == "quotes-only"


def test_every_quote_is_an_exact_piece_of_its_passage() -> None:
    passage = hit("First line\nwraps here.  The limit for NDMA\nis 96 ng/day.  Last sentence.")
    quotes = QuotingAnswerWriter().write("NDMA limit", [passage]).quotes
    assert quotes
    for quote in quotes:
        assert quote.text in passage.text


def test_at_most_two_sentences_per_passage_and_they_keep_their_order() -> None:
    passage = hit("NDMA is one. Filler here. NDMA limit is two. NDMA limit again is three.")
    quotes = QuotingAnswerWriter().write("NDMA limit again", [passage]).quotes
    assert len(quotes) == MAX_SENTENCES_PER_PASSAGE == 2
    assert [q.text for q in quotes] == ["NDMA limit is two.", "NDMA limit again is three."]


def test_a_passage_that_shares_no_distinctive_word_is_not_cited() -> None:
    draft = QuotingAnswerWriter().write(
        "cadmium exposure", [hit("Nothing relevant here at all."), hit("Cadmium exposure is five.")]
    )
    assert len(draft.quotes) == 1 and "Cadmium" in draft.quotes[0].text


def test_a_question_with_no_distinctive_word_quotes_nothing() -> None:
    assert QuotingAnswerWriter().write("What is it?", [hit(NDMA)]).quotes == ()


def test_the_number_of_quotes_is_capped() -> None:
    passages = [hit("NDMA limit one. NDMA limit two.") for _ in range(10)]
    assert len(QuotingAnswerWriter().write("NDMA limit", passages).quotes) == MAX_QUOTES


# --- verification -------------------------------------------------------------------------------


def test_verify_keeps_a_word_for_word_quote_and_ignores_line_breaks() -> None:
    passage = hit("The limit for NDMA\nis 96 ng/day.")
    ok = Quote(passage.chunk_id, "The limit for NDMA is 96 ng/day.")
    assert verify([ok], [passage]).verified == (ok,)


@pytest.mark.parametrize(
    "altered",
    [
        "The limit for NDMA is 69 ng/day.",  # a number changed
        "The limit for ndma is 96 ng/day.",  # case changed
        "The limit for NDMA is 96 ng/day. And more.",  # something added
        "limit NDMA 96",  # words reordered
        "",
        "   ",
    ],
)
def test_verify_rejects_a_quote_that_is_not_in_the_passage(altered: str) -> None:
    passage = hit("The limit for NDMA is 96 ng/day.")
    checked = verify([Quote(passage.chunk_id, altered)], [passage])
    assert checked.verified == () and len(checked.rejected) == 1


def test_verify_rejects_a_quote_that_names_a_passage_that_was_not_retrieved() -> None:
    passage = hit("The limit for NDMA is 96 ng/day.")
    elsewhere = Quote(uuid.uuid4(), "The limit for NDMA is 96 ng/day.")
    assert verify([elsewhere], [passage]).verified == ()


def test_verify_rejects_a_real_sentence_credited_to_the_wrong_passage() -> None:
    a, b = hit("Alpha says one thing."), hit("Beta says another.")
    assert verify([Quote(b.chunk_id, "Alpha says one thing.")], [a, b]).verified == ()


def test_normalise_collapses_whitespace_only() -> None:
    assert normalise("a \n b\t\tc") == "a b c"


# --- the whole answer ---------------------------------------------------------------------------


def test_a_supported_question_gets_a_verified_answer_with_full_citations() -> None:
    passage = hit(NDMA)
    result = answer_question("acceptable intake for NDMA", fused(passage), QuotingAnswerWriter())
    assert result.verified and not result.withheld and result.rejected_quotes == 0
    assert "According to FDA Nitrosamine Guidance (APPENDIX B, p. 43):" in result.answer
    assert f'"{NDMA_SENTENCE}"' in result.answer
    first = result.citations[0]
    assert first["chunk_id"] == str(passage.chunk_id) and first["source_id"] == "FDA-NITRO-2024"
    assert first["title"] == "FDA Nitrosamine Guidance" and first["sha256"] == "a" * 64
    assert (first["version_number"], first["version_label"]) == (1, "Revision 1")
    assert (first["page_start"], first["page_end"]) == (43, 43)
    assert first["attribution"] == "U.S. Food and Drug Administration"
    assert first["quoted_evidence"] in passage.text
    assert result.coverage == 1.0 and result.mode == "quotes-only"


def test_with_no_passages_the_answer_is_withheld() -> None:
    result = answer_question("acceptable intake for NDMA", [], QuotingAnswerWriter())
    assert result.withheld and not result.verified
    assert result.answer == NO_EVIDENCE_ANSWER
    assert result.citations == [] and result.quotes == () and result.coverage == 0.0


def test_when_no_passage_shares_a_word_with_the_question_the_answer_is_withheld() -> None:
    result = answer_question("capital of France", fused(hit(NDMA)), QuotingAnswerWriter())
    assert result.withheld and result.citations == []
    assert "No retrieved passage shares a distinctive word" in result.evidence_gaps[0]
    assert "NDMA" not in result.answer


def test_a_made_up_quote_is_removed_and_never_reaches_the_answer() -> None:
    passage = hit(NDMA)
    real = Quote(passage.chunk_id, NDMA_SENTENCE)
    invented = Quote(passage.chunk_id, "The acceptable intake for NDMA is 960 ng/day.")
    result = answer_question("NDMA intake", fused(passage), FixedWriter(real, invented))
    assert not result.verified and not result.withheld and result.rejected_quotes == 1
    assert "960" not in result.answer
    assert all("960" not in c["quoted_evidence"] for c in result.citations)
    assert "96 ng/day" in result.answer
    assert "were not found in the passage they named" in result.evidence_gaps[0]


def test_an_answer_made_only_of_made_up_quotes_is_withheld() -> None:
    passage = hit(NDMA)
    result = answer_question(
        "NDMA intake", fused(passage), FixedWriter(Quote(passage.chunk_id, "NDMA is harmless."))
    )
    assert result.withheld and result.rejected_quotes == 1 and result.answer == NO_EVIDENCE_ANSWER


def test_the_answer_text_is_always_built_from_verified_quotes() -> None:
    passage = hit(NDMA)
    result = answer_question(
        "NDMA intake", fused(passage), FixedWriter(Quote(passage.chunk_id, NDMA_SENTENCE))
    )
    assert result.verified and result.mode == "test-writer"
    assert result.answer == (
        f'According to FDA Nitrosamine Guidance (APPENDIX B, p. 43):\n"{NDMA_SENTENCE}"'
    )


# --- coverage and gaps --------------------------------------------------------------------------


def test_coverage_is_the_share_of_the_questions_words_found_in_the_quotes() -> None:
    quotes = [Quote(uuid.uuid4(), NDMA_SENTENCE)]
    assert coverage("acceptable intake NDMA", quotes) == 1.0
    assert coverage("acceptable intake NDMA stability", quotes) == 0.75
    assert coverage("What is it?", quotes) == 0.0
    assert coverage("NDMA", []) == 0.0


def test_a_missing_word_of_three_letters_or_fewer_is_not_reported_as_a_gap() -> None:
    # "dye" is a distinctive word of the question but too short to be worth reporting.
    assert evidence_gaps("NDMA dye", [hit("The limits for NDMA are listed.")]) == []


def test_gaps_name_the_words_as_asked_and_ignore_short_words_and_plurals() -> None:
    passages = [hit("The limits for NDMA are listed.")]
    gaps = evidence_gaps("What are the limit for NDMA stability in tablets?", passages)
    assert gaps == [
        "None of the retrieved passages mentions 'stability'.",
        "None of the retrieved passages mentions 'tablets'.",
    ]
