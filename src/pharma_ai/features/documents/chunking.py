"""Chunking: cut a document's page text into passages that can be searched and cited (S2-06).

Plain language: a *chunk* is a short passage, a few paragraphs at most, that is stored on its own so
that a question can be matched to the passage that answers it. Each chunk remembers the pages it came
from, because a citation must say "page 12", and the section heading it sits under.

How the text is cut (ADR-008):
1. **Page furniture is removed.** Running headers ("Contains Nonbinding Recommendations") and bare
   page numbers repeat on every page. They are not part of the document's content and would pollute
   every chunk, so they are dropped. Nothing else is rewritten: the words of a chunk are the words
   of the document.
2. **Lines are joined into paragraphs, and paragraphs into sentences.** The PDF text is hard-wrapped
   at the page width, so a sentence is spread over several lines and may run on to the next page.
3. **Headings start a new section.** A chunk never crosses a heading, so the section named on a
   citation is the one the passage is in. This is a heuristic (see ``is_heading``); a heading that
   is not recognised just means the passage carries the previous section name.
4. **Sentences are packed into chunks of at most ``max_tokens`` tokens**, and each chunk starts
   with up to ``overlap_tokens`` tokens repeated from the end of the one before, so a sentence that
   straddles a boundary is whole in at least one chunk.

A *token* is the unit the embedding model reads: a word or a piece of a word. The caller supplies the
counter (the model's own tokenizer), so the limit is exact for the model in use.

Known limits, not hidden: tables come out of the PDF as a run of text, not rows and columns, so a
table is chunked like prose; hyphenated words split across lines are left as they are; and pages
without a text layer (scans) contribute nothing, because OCR is not available.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from pharma_ai.features.documents.extraction import PageText

TokenCounter = Callable[[str], int]

# How many non-empty lines at the top and at the bottom of a page may be page furniture.
EDGE_LINES = 3
# A line must repeat on at least this share of the pages (and on at least 3) to be furniture.
FURNITURE_SHARE = 0.4
FURNITURE_MAX_CHARS = 120

_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TextChunk:
    index: int  # 0-based position in the document
    text: str
    page_start: int
    page_end: int
    section: str | None
    token_count: int
    chunk_type: str = "paragraph"  # tables are not told apart from prose yet (see module note)


# --------------------------------------------------------------------------------------------
# Step 1: page furniture
# --------------------------------------------------------------------------------------------


def _normalise(line: str) -> str:
    """Lower-case, collapse spaces and replace digits, so '6' and '7' compare equal."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip().lower()))


def _edge_indexes(lines: Sequence[str]) -> set[int]:
    non_empty = [i for i, line in enumerate(lines) if line.strip()]
    return set(non_empty[:EDGE_LINES]) | set(non_empty[-EDGE_LINES:])


def _could_be_running_header(line: str) -> bool:
    """Headers and footers are short and do not end like a sentence. A line that does is treated as
    content, so a repeated sentence is kept rather than risk deleting real text."""
    text = line.strip()
    return (
        bool(text)
        and len(text) <= FURNITURE_MAX_CHARS
        and not _PAGE_NUMBER.match(text)
        and not text.endswith((".", "!", "?"))
    )


def strip_page_furniture(pages: Sequence[PageText]) -> list[PageText]:
    """Drop page numbers and running headers/footers from the top and bottom of each page."""
    split = [page.text.split("\n") for page in pages]
    counts: Counter[str] = Counter()
    for lines in split:
        counts.update(
            {
                _normalise(lines[i])
                for i in _edge_indexes(lines)
                if _could_be_running_header(lines[i])
            }
        )
    threshold = max(3, math.ceil(FURNITURE_SHARE * len(pages)))
    repeated = {line for line, n in counts.items() if n >= threshold}

    cleaned = []
    for page, lines in zip(pages, split, strict=True):
        drop = {
            i
            for i in _edge_indexes(lines)
            if _PAGE_NUMBER.match(lines[i])
            or (_could_be_running_header(lines[i]) and _normalise(lines[i]) in repeated)
        }
        kept = [line for i, line in enumerate(lines) if i not in drop]
        cleaned.append(PageText(page.page, "\n".join(kept).strip()))
    return cleaned


# --------------------------------------------------------------------------------------------
# Step 2: headings, paragraphs, sentences
# --------------------------------------------------------------------------------------------

_HIERARCHICAL = re.compile(r"^\d{1,2}(?:\.\d{1,2}){1,4}\.?\s+[A-Z]")  # 4.2 Solvents to Be Limited
_NUMBER_DOT = re.compile(r"^\d{1,2}\.\s+[A-Z]")  # 1. Introduction
_NUMBER_GAP = re.compile(r"^\d{1,2}\s{2,}[A-Z]")  # 8      Control of Impurities
_ROMAN = re.compile(r"^[IVX]{1,5}\.\s+[A-Z]")  # III. STAGE 1
_LETTER = re.compile(r"^[A-Z]\.\s+[A-Z][a-z]")  # A. Stage 1
_PART = re.compile(r"^(?:PART|APPENDIX|ANNEX)\s+[A-Z0-9]{1,5}(?:\s*:|\s*$)", re.IGNORECASE)
_ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9 ,;:&()/'–-]{3,80}$")
_DOT_LEADER = re.compile(r"\.{4,}")  # "INTRODUCTION ........ 3": a contents-page line
_YEAR_IN_BRACKETS = re.compile(r"\(\d{4}\)")  # "Harmonisation (2006)": a reference entry
_LOWER_CASE_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)

_CONTENTS = re.compile(r"^(?:table of )?contents$", re.IGNORECASE)
_REFERENCES = re.compile(r"^(?:\d{1,2}\.?\s+)?(?:references|bibliography)\b", re.IGNORECASE)

HEADING_MAX_CHARS = 90
HEADING_MAX_WORDS = 12
ALL_CAPS_MIN_LETTERS = 5  # shorter capitals ("PDE", "AUC", "NDMA") are usually abbreviations


def _title_case_share(text: str) -> float:
    """Share of the words (small words like 'of' ignored) that start with a capital letter."""
    words = [
        w for w in re.findall(r"[A-Za-z][A-Za-z-]*", text) if w.lower() not in _LOWER_CASE_WORDS
    ]
    return sum(w[0].isupper() for w in words) / len(words) if words else 0.0


_ONE_LEVEL_NUMBER = re.compile(r"^(?:\d{1,2}|[IVX]{1,5}|[A-Z])\.\s+")


def is_heading(line: str, *, numbered_items: bool = True) -> bool:
    """Does this line look like a section heading? A heuristic, checked against the corpus.

    A heading is short, has no sentence-ending punctuation, and either starts with a section number
    (``4.2``, ``1.``, ``III.``, ``A.``), starts with PART/APPENDIX/ANNEX, or is written in capitals.
    Numbered list items and bibliography entries look alike, so the one-level forms (``1.``,
    ``III.``, ``A.``) must also read like a title: mostly capitalised words, no full stop or year
    in brackets inside. ``numbered_items=False`` turns those one-level forms off altogether (used
    inside a list of references).
    """
    text = line.strip()
    if not text or len(text) > HEADING_MAX_CHARS or len(text.split()) > HEADING_MAX_WORDS:
        return False
    if _DOT_LEADER.search(text):
        return False
    if _PART.match(text):
        return not re.search(r"\s\d{1,3}$", text)  # "PART VI: ... 35" is a contents-page line
    if text.endswith((".", ",", ";", ":")):
        return False
    if _HIERARCHICAL.match(text) or _NUMBER_GAP.match(text):
        return True
    if any(pattern.match(text) for pattern in (_NUMBER_DOT, _ROMAN, _LETTER)):
        title = _ONE_LEVEL_NUMBER.sub("", text)
        return (
            numbered_items
            and not _YEAR_IN_BRACKETS.search(title)
            and ". " not in title
            and ": " not in title
            and (text.isupper() or _title_case_share(title) >= 0.6)
        )
    letters = sum(c.isalpha() for c in text)
    return bool(_ALL_CAPS.match(text)) and letters >= ALL_CAPS_MIN_LETTERS


def _continues_heading(heading: str, line: str) -> bool:
    """Is ``line`` the second line of a heading that wrapped? Two cases are recognised."""
    if heading.rstrip(" ,").rsplit(" ", 1)[-1].lower() in _LOWER_CASE_WORDS:
        return len(line) <= HEADING_MAX_CHARS and not line.endswith(".")  # "... Manufacturing, and"
    unnumbered_caps = line.isupper() and not re.match(r"^(?:\d|[IVX]{1,5}\.|[A-Z]\.)", line)
    return heading.isupper() and unnumbered_caps and is_heading(line)


@dataclass
class _Paragraph:
    lines: list[tuple[int, str]]  # (page, text) for each line
    heading: bool = False


def _paragraphs(pages: Sequence[PageText]) -> list[_Paragraph]:
    """Join the wrapped lines into paragraphs. Headings are paragraphs of their own."""
    result: list[_Paragraph] = []
    current: _Paragraph | None = None
    open_heading: _Paragraph | None = None  # the heading on the line just before, if any
    in_references = False  # numbered lines in a list of references are entries, not headings
    for page in pages:
        open_heading = None
        if current is not None and current.lines[-1][1].rstrip().endswith((".", "!", "?")):
            current = None  # a page that ends a sentence also ends the paragraph
        for raw in page.text.split("\n"):
            line = raw.strip()
            if not line:
                current = open_heading = None
            elif _DOT_LEADER.search(line):
                current = open_heading = None  # a line of a table of contents: skip it
            elif open_heading is not None and _continues_heading(
                " ".join(text for _, text in open_heading.lines), line
            ):
                open_heading.lines.append((page.page, line))
            elif is_heading(line, numbered_items=not in_references):
                open_heading = _Paragraph([(page.page, line)], heading=True)
                result.append(open_heading)
                current = None
                in_references = bool(_REFERENCES.match(line))
            else:
                open_heading = None
                if current is None:
                    current = _Paragraph([(page.page, line)])
                    result.append(current)
                else:
                    current.lines.append((page.page, line))
    return result


# Words that end in a full stop without ending the sentence.
_ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "vs", "fig", "figs", "no", "nos", "approx", "al", "ref", "refs", "eq", "sec"}
    | {"dr", "mr", "mrs", "ms", "st", "cf", "viz", "ca", "inc", "ltd", "co", "pp", "vol"}
)
_BOUNDARY = re.compile(r"[.!?][\"')\]]*\s+(?=[A-Z0-9(\[\"'])")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` offsets of the sentences in ``text`` (which has no line breaks)."""
    spans = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        word = re.findall(r"[A-Za-z.]+$", text[start : match.start() + 1].rstrip(".!?\"')]"))
        last = word[0].lower().strip(".") if word else ""
        if match.group().startswith(".") and (last in _ABBREVIATIONS or len(last) == 1):
            continue  # an abbreviation or an initial, not the end of a sentence
        spans.append((start, match.start() + len(match.group().rstrip())))  # keep a closing quote
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


@dataclass(frozen=True)
class _Unit:
    """One sentence (or heading, or a piece of an over-long sentence) with where it came from."""

    text: str
    tokens: int
    page_start: int
    page_end: int
    paragraph: int
    heading: bool


def _split_long(text: str, count: TokenCounter, max_tokens: int) -> list[str]:
    """Cut a sentence that alone exceeds the limit into pieces of whole words."""
    pieces: list[str] = []
    words: list[str] = []
    used = 0
    for word in text.split():
        cost = count(word)
        if words and used + cost > max_tokens:
            pieces.append(" ".join(words))
            words, used = [], 0
        words.append(word)
        used += cost
    if words:
        pieces.append(" ".join(words))
    return pieces


def _units(paragraph: _Paragraph, number: int, count: TokenCounter, max_tokens: int) -> list[_Unit]:
    if paragraph.heading:
        text = " ".join(line for _, line in paragraph.lines)
        first, last = paragraph.lines[0][0], paragraph.lines[-1][0]
        return [_Unit(text, count(text), first, last, number, heading=True)]
    text = " ".join(line for _, line in paragraph.lines)
    line_starts = []
    offset = 0
    for _, line in paragraph.lines:
        line_starts.append(offset)
        offset += len(line) + 1

    def page_at(position: int) -> int:
        return paragraph.lines[bisect_right(line_starts, position) - 1][0]

    units = []
    for start, end in split_sentences(text):
        sentence = text[start:end].strip()
        if not sentence:
            continue
        first = page_at(start)
        last = page_at(max(start, end - 1))
        tokens = count(sentence)
        pieces = [sentence] if tokens <= max_tokens else _split_long(sentence, count, max_tokens)
        for piece in pieces:
            units.append(_Unit(piece, count(piece), first, last, number, heading=False))
    return units


# --------------------------------------------------------------------------------------------
# Step 3 and 4: sections, then packing
# --------------------------------------------------------------------------------------------


@dataclass
class _Section:
    label: str | None  # the heading the section starts with; None before the first heading
    units: list[_Unit]


def _sections(
    paragraphs: Sequence[_Paragraph], count: TokenCounter, max_tokens: int
) -> list[_Section]:
    sections: list[_Section] = []
    for number, paragraph in enumerate(paragraphs):
        units = _units(paragraph, number, count, max_tokens)
        if not units:
            continue
        if paragraph.heading and _CONTENTS.match(units[0].text):
            # "TABLE OF CONTENTS" is not a section of the document. Its lines are skipped
            # earlier; whatever follows keeps the section it was already in.
            continue
        if paragraph.heading:
            sections.append(_Section(units[0].text, list(units)))
        else:
            if not sections:
                sections.append(_Section(None, []))
            sections[-1].units.extend(units)
    return sections


def _join(units: Sequence[_Unit]) -> str:
    parts = [units[0].text]
    for previous, unit in pairwise(units):
        parts.append(("\n\n" if unit.paragraph != previous.paragraph else " ") + unit.text)
    return "".join(parts)


def _pack(units: Sequence[_Unit], max_tokens: int, overlap_tokens: int) -> list[list[_Unit]]:
    groups = []
    start = 0
    while start < len(units):
        end = start
        total = 0
        while end < len(units) and (end == start or total + units[end].tokens <= max_tokens):
            total += units[end].tokens
            end += 1
        groups.append(list(units[start:end]))
        if end >= len(units):
            break
        # The next chunk repeats whole sentences from the end of this one, up to the overlap,
        # but always moves forward by at least one sentence.
        back = end
        carried = 0
        while back - 1 > start and carried + units[back - 1].tokens <= overlap_tokens:
            back -= 1
            carried += units[back].tokens
        start = back
    return groups


def chunk_pages(
    pages: Sequence[PageText],
    count_tokens: TokenCounter,
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 100,
) -> list[TextChunk]:
    """Cut the pages of one document version into chunks. Same input, same chunks, always."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if not 0 <= overlap_tokens < max_tokens:
        raise ValueError("overlap_tokens must be at least 0 and less than max_tokens")

    paragraphs = _paragraphs(strip_page_furniture(pages))
    chunks: list[TextChunk] = []
    for section in _sections(paragraphs, count_tokens, max_tokens):
        for group in _pack(section.units, max_tokens, overlap_tokens):
            text = _join(group)
            if all(unit.heading for unit in group) or not any(c.isalnum() for c in text):
                continue  # a heading with no text under it says nothing a question could match
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    text=text,
                    page_start=min(unit.page_start for unit in group),
                    page_end=max(unit.page_end for unit in group),
                    section=section.label,
                    token_count=sum(unit.tokens for unit in group),
                )
            )
    return chunks
