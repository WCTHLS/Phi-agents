"""Tests for finding the page a quote is on (S2-09). No database: pages are written by hand.

The case that prompted this: a passage that starts on page 10 and runs onto page 11, with the quoted
sentence on page 11. The citation must say page 11, not page 10.
"""

from __future__ import annotations

from pharma_ai.features.documents.extraction import PageText
from pharma_ai.features.documents.provenance import EDGE_CHARS, locate_quote

FACTORS = (
    "Factors that should be considered (for liquid and semi-solid dosage forms) include but are "
    "not limited to: Hydrophilicity/hydrophobicity; Ionic content; pH."
)


def pages(*texts: str, first: int = 10) -> list[PageText]:
    return [PageText(first + i, text) for i, text in enumerate(texts)]


def test_a_quote_on_the_second_page_of_a_two_page_passage_is_on_that_page() -> None:
    doc = pages(
        "Contributions from processing equipment would be minimal. The applicant should consider",
        f"ICH Q3D(R2) Guideline\nthe potential for incorporation.\n{FACTORS}",
    )
    assert locate_quote(doc, FACTORS, 10, 11) == (11, 11)


def test_a_quote_on_the_first_page_is_on_that_page() -> None:
    doc = pages(f"Opening text. {FACTORS}", "Something else entirely on the next page.")
    assert locate_quote(doc, FACTORS, 10, 11) == (10, 10)


def test_a_sentence_split_by_a_page_break_is_given_both_pages_even_with_a_header_between() -> None:
    sentence = (
        "When a review of the materials of construction demonstrates that the container closure "
        "system does not contain elemental impurities, no additional risk assessment needs to be "
        "performed."
    )
    cut = sentence.index("does not contain")
    doc = pages(
        sentence[:cut],
        "ICH Q3D(R2) Guideline\n" + sentence[cut:],
    )
    assert locate_quote(doc, sentence, 10, 11) == (10, 11)


def test_line_breaks_and_extra_spaces_in_the_page_text_do_not_matter() -> None:
    quote = "The acceptable intake for NDMA is 96 ng/day."
    doc = pages("Intro.  The acceptable   intake\nfor NDMA is\n96 ng/day.  More text.")
    assert locate_quote(doc, quote, 10, 10) == (10, 10)


def test_only_the_passages_own_pages_are_searched() -> None:
    doc = [PageText(5, FACTORS), PageText(10, "Nothing here."), PageText(11, f"Intro. {FACTORS}")]
    assert locate_quote(doc, FACTORS, 10, 11) == (11, 11)  # not page 5, outside the range


def test_a_quote_that_cannot_be_found_keeps_the_passages_own_range() -> None:
    doc = pages("Unrelated text.", "More unrelated text.")
    assert locate_quote(doc, FACTORS, 10, 11) == (10, 11)


def test_an_empty_quote_keeps_the_passages_own_range() -> None:
    assert locate_quote(pages("text"), "   ", 10, 11) == (10, 11)


def test_a_short_quote_is_found() -> None:
    doc = pages("Nothing.", "Ionic content; pH; temperature.")
    assert locate_quote(doc, "pH; temperature.", 10, 11) == (11, 11)


def test_a_long_quote_is_found_by_its_ends() -> None:
    middle = "x " * 400
    quote = f"Start of the long quote. {middle}End of the long quote."
    assert len(quote) > 4 * EDGE_CHARS
    doc = pages("unrelated", f"Start of the long quote. {middle}End of the long quote.")
    assert locate_quote(doc, quote, 10, 11) == (11, 11)


def test_a_single_page_passage_is_unchanged() -> None:
    doc = pages(f"Text. {FACTORS}")
    assert locate_quote(doc, FACTORS, 10, 10) == (10, 10)


def test_pages_outside_the_stored_text_do_not_cause_an_error() -> None:
    assert locate_quote([], FACTORS, 10, 11) == (10, 11)
