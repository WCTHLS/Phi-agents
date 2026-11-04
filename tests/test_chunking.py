"""Tests for cutting page text into passages (S2-06, ADR-008). No database and no model needed."""

from __future__ import annotations

import re
from itertools import pairwise

import pytest

from pharma_ai.features.documents.chunking import (
    chunk_pages,
    is_heading,
    split_sentences,
    strip_page_furniture,
)
from pharma_ai.features.documents.extraction import PageText


def count(text: str) -> int:
    return len(text.split())


def pages(*texts: str) -> list[PageText]:
    return [PageText(number, text) for number, text in enumerate(texts, start=1)]


def sentences(text: str) -> list[str]:
    return [text[start:end] for start, end in split_sentences(text)]


def item(number: int) -> str:
    return f"Item {number} states that the value is exactly right here."  # always 10 words


def word(number: int) -> str:
    """A distinct word for each number (lines that differ only in digits look like page furniture)."""
    return "".join(chr(97 + int(digit)) for digit in str(number))


def wrap(text: str, width: int = 8) -> str:
    """Hard-wrap like a PDF does: a line break every few words."""
    words = text.split()
    return "\n".join(" ".join(words[i : i + width]) for i in range(0, len(words), width))


# --- page furniture ---------------------------------------------------------------------------


def test_running_headers_and_page_numbers_are_removed_but_the_text_is_not() -> None:
    body = [f"Body text of page {word(n)} is here." for n in range(1, 7)]
    raw = [
        PageText(n, f"Contains Nonbinding Recommendations\n{n}\n\n{text}\n\n {n + 4}")
        for n, text in enumerate(body, start=1)
    ]
    cleaned = strip_page_furniture(raw)
    assert [page.text for page in cleaned] == body
    assert [page.page for page in cleaned] == [1, 2, 3, 4, 5, 6]


def test_a_repeated_line_that_ends_like_a_sentence_is_kept_as_content() -> None:
    """Regression: the identical last line of six pages was deleted as if it were a footer."""
    raw = pages(
        *[f"Text {word(n)} goes here.\nstates that the value is exactly right." for n in range(6)]
    )
    assert [p.text for p in strip_page_furniture(raw)] == [p.text for p in raw]


def test_a_line_that_appears_on_only_one_page_is_never_treated_as_furniture() -> None:
    raw = pages(*[f"Heading {word(n)}\n\nText {word(n)}." for n in range(1, 7)])
    assert [p.text for p in strip_page_furniture(raw)] == [p.text for p in raw]


def test_a_short_document_loses_page_numbers_but_keeps_a_repeated_line() -> None:
    raw = pages("Same line\n\nOne.\n\n1", "Same line\n\nTwo.\n\n2")
    assert [p.text for p in strip_page_furniture(raw)] == ["Same line\n\nOne.", "Same line\n\nTwo."]


def test_a_number_in_the_middle_of_a_page_is_kept() -> None:
    lines = [f"Line {word(n)} of the page." for n in range(8)]
    raw = pages("\n".join([*lines[:4], "5", *lines[4:]]))
    assert "\n5\n" in strip_page_furniture(raw)[0].text


# --- headings and sentences -------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "4.2 Solvents to Be Limited",
        "8.1        Control of Process Related Impurities",
        "1. Introduction",
        "I. INTRODUCTION",
        "A. General Considerations for Process Validation",
        "1. Building and Capturing Process Knowledge and Understanding",
        "PART VI:",
        "Appendix 2: Case Examples to Illustrate Potential Control Approaches",
        "GLOSSARY",
        "2.1.7.1 Parent vs. Metabolite",
    ],
)
def test_lines_that_are_headings(line: str) -> None:
    assert is_heading(line)


@pytest.mark.parametrize(
    "line",
    [
        "The limit applies to all drug products.",
        "5. DRUG SUBSTANCE AND DRUG PRODUCT IMPURITY ASSESSMENT ........................ 4",
        "PART VI: PDE for 2-Methyltetrahydrofuran, Cyclopentyl Methyl Ether, and Alcohol  35",
        "2. When intermediates and products cannot be highly characterized and well-defined quality",
        "1. International Conference on Harmonisation (2006). Q3A(R2): Impurities in New Drug",
        "5. European Chemicals Agency (ECHA), 2020. Cyclopentyl methyl ether. CASRN",
        "Section 8.5 (Lifecycle Management) contains additional recommendations for products",
        "4 The information included for N -Methylpyrrolidone reflects that included in the Revision",
        "Nitromethane 0.5 50",
        "PDE",
        "6",
        "",
    ],
)
def test_lines_that_are_not_headings(line: str) -> None:
    assert not is_heading(line)


def test_numbered_lines_are_not_headings_inside_a_list_of_references() -> None:
    entry = "1. Aycock Solvent Applications Of Methyltetrahydrofuran In Organometallic"
    assert is_heading(entry)
    assert not is_heading(entry, numbered_items=False)
    text = pages("REFERENCES\n\n" + entry + "\nand biphasic reactions were reported.")
    assert {chunk.section for chunk in chunk_pages(text, count)} == {"REFERENCES"}


def test_sentences_are_not_split_at_abbreviations_decimals_or_initials() -> None:
    text = (
        "The limit is 0.5 mg/day, e.g. for lead. See Fig. 3 for details. "
        "Smith et al. reported it. J. Smith agreed. It ended."
    )
    assert sentences(text) == [
        "The limit is 0.5 mg/day, e.g. for lead.",
        "See Fig. 3 for details.",
        "Smith et al. reported it.",
        "J. Smith agreed.",
        "It ended.",
    ]


def test_a_closing_quote_stays_with_its_sentence() -> None:
    assert sentences('He said "stop." Then he left.') == ['He said "stop."', "Then he left."]


# --- chunking ---------------------------------------------------------------------------------


def test_nothing_comes_out_of_empty_pages() -> None:
    assert chunk_pages([], count) == []
    assert chunk_pages(pages("", "   \n  "), count) == []


def test_small_documents_become_one_chunk_with_its_page_range() -> None:
    chunks = chunk_pages(pages("First sentence. Second sentence.", "Third sentence."), count)
    assert len(chunks) == 1
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)
    assert chunks[0].text == "First sentence. Second sentence.\n\nThird sentence."
    assert chunks[0].token_count == 6 and chunks[0].index == 0 and chunks[0].section is None


def test_a_sentence_that_runs_across_a_page_break_is_whole_and_spans_both_pages() -> None:
    raw = pages("Before it.\n\nThe value must be at least", "five percent of the total. After it.")
    chunks = chunk_pages(raw, count, max_tokens=12, overlap_tokens=0)
    spanning = [c for c in chunks if "five percent" in c.text]
    assert len(spanning) == 1
    assert "The value must be at least five percent of the total." in spanning[0].text
    assert (spanning[0].page_start, spanning[0].page_end) == (1, 2)


def test_chunks_respect_the_limit_and_repeat_the_overlap() -> None:
    body = " ".join(item(n) for n in range(1, 41))
    chunks = chunk_pages(pages(wrap(body)), count, max_tokens=50, overlap_tokens=20)

    assert all(chunk.token_count <= 50 for chunk in chunks)
    assert all(count(chunk.text) == chunk.token_count for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    numbers = [[int(n) for n in re.findall(r"Item (\d+)", chunk.text)] for chunk in chunks]
    assert all(len(group) == 5 for group in numbers[:-1])
    for earlier, later in pairwise(numbers):
        # Two whole sentences (20 tokens) are repeated at the start of the next chunk.
        assert later[:2] == earlier[-2:]
        assert later[2] == earlier[-1] + 1
    assert sorted({n for group in numbers for n in group}) == list(range(1, 41))


@pytest.mark.parametrize(("limit", "overlap"), [(30, 0), (47, 10), (60, 25), (101, 100)])
def test_no_chunk_is_over_the_limit_whatever_the_sentence_lengths(limit: int, overlap: int) -> None:
    """Sentences of 3 to 14 words, so the limit is hit at every possible remainder."""
    body = " ".join(
        "Word " + " ".join(f"w{word(k)}" for k in range(2 + n % 12)) + " end." for n in range(60)
    )
    chunks = chunk_pages(pages(wrap(body)), count, max_tokens=limit, overlap_tokens=overlap)
    assert len(chunks) > 3
    assert all(chunk.token_count <= limit for chunk in chunks)
    assert all(count(chunk.text) == chunk.token_count for chunk in chunks)
    # And they are full: the next sentence would not have fitted (so the limit is really used).
    assert max(chunk.token_count for chunk in chunks) >= limit - 14


def test_a_short_chunk_followed_by_a_long_sentence_does_not_stall_the_window() -> None:
    """Regression guard: with a large overlap the window must still move on (else: endless loop)."""
    short = "Five words are in here."
    long_sentence = "Alpha " + "beta " * 26 + "end."  # 28 words
    chunks = chunk_pages(
        pages(short + " " + long_sentence), count, max_tokens=30, overlap_tokens=29
    )
    assert [chunk.text for chunk in chunks] == [short, long_sentence]


def test_the_overlap_never_stops_the_chunks_moving_forward() -> None:
    body = " ".join(item(n) for n in range(1, 6))
    chunks = chunk_pages(pages(body), count, max_tokens=30, overlap_tokens=29)
    assert len(chunks) < 20  # would loop for ever if the window did not advance
    assert " ".join(c.text for c in chunks).count(item(5)) >= 1


def test_a_sentence_longer_than_the_limit_is_cut_into_pieces_of_whole_words() -> None:
    long_sentence = " ".join(f"word{n}" for n in range(120))
    chunks = chunk_pages(pages(long_sentence), count, max_tokens=50, overlap_tokens=0)
    assert [chunk.token_count for chunk in chunks] == [50, 50, 20]
    assert " ".join(chunk.text.replace("\n\n", " ") for chunk in chunks) == long_sentence


def test_a_chunk_never_crosses_a_heading_and_names_its_section() -> None:
    text = (
        "I. INTRODUCTION\n\nThe first section says one thing. It says two things.\n\n"
        "II. BACKGROUND\n\nThe second section says another thing."
    )
    chunks = chunk_pages(pages(text), count)
    assert [c.section for c in chunks] == ["I. INTRODUCTION", "II. BACKGROUND"]
    assert chunks[0].text.startswith("I. INTRODUCTION\n\nThe first section")
    assert "BACKGROUND" not in chunks[0].text and "first section" not in chunks[1].text


def test_a_heading_that_wraps_onto_two_lines_is_one_heading() -> None:
    capitals = (
        "III. STATUTORY AND REGULATORY REQUIREMENTS FOR PROCESS\nVALIDATION\n\nBody text here."
    )
    lower = "4.1 Post-Approval Changes to the Drug Substance, and\nControls\n\nBody text here."
    assert chunk_pages(pages(capitals), count)[0].section == (
        "III. STATUTORY AND REGULATORY REQUIREMENTS FOR PROCESS VALIDATION"
    )
    assert chunk_pages(pages(lower), count)[0].section == (
        "4.1 Post-Approval Changes to the Drug Substance, and Controls"
    )


def test_a_heading_with_nothing_under_it_makes_no_chunk() -> None:
    chunks = chunk_pages(pages("1. INTRODUCTION\n\nSome text.\n\n2. SCOPE"), count)
    assert [c.section for c in chunks] == ["1. INTRODUCTION"]


def test_contents_lines_are_dropped_and_do_not_swallow_the_text_that_follows() -> None:
    """Regression: dropping the contents 'section' also dropped the title block after it."""
    contents = "TABLE OF CONTENTS\nI. INTRODUCTION .......... 1\nII. BACKGROUND .......... 2"
    front = "Guidance for Industry\n\nThis guidance represents the current thinking.\n\n"
    chunks = chunk_pages(pages(contents, front + "I. INTRODUCTION\n\nBody text here."), count)
    assert not any("...." in c.text or "CONTENTS" in c.text for c in chunks)
    front_chunk = next(c for c in chunks if "current thinking" in c.text)
    assert front_chunk.section is None and front_chunk.page_start == 2
    assert chunks[-1].section == "I. INTRODUCTION"


def test_no_words_are_lost_apart_from_page_furniture() -> None:

    def claim(n: int, k: int) -> str:
        return f"Claim {word(n)}{word(k)} states that the value is exactly right here."

    raw = [
        PageText(n, f"Running Header\n\n{n}\n\n" + wrap(" ".join(claim(n, k) for k in range(4))))
        for n in range(1, 7)
    ]
    chunks = chunk_pages(raw, count, max_tokens=60, overlap_tokens=10)
    joined = " ".join(c.text.replace("\n\n", " ") for c in chunks)
    assert "Running Header" not in joined
    assert all(claim(n, k) in joined for n in range(1, 7) for k in range(4))


def test_the_same_input_always_gives_the_same_chunks() -> None:
    raw = pages("1. SCOPE\n\n" + wrap(" ".join(item(n) for n in range(30))))
    assert chunk_pages(raw, count, max_tokens=40, overlap_tokens=10) == chunk_pages(
        raw, count, max_tokens=40, overlap_tokens=10
    )


@pytest.mark.parametrize(("limit", "overlap"), [(0, 0), (10, 10), (10, -1), (10, 11)])
def test_nonsense_limits_are_refused(limit: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="max_tokens|overlap_tokens"):
        chunk_pages(pages("Some text."), count, max_tokens=limit, overlap_tokens=overlap)
