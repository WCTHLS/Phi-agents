"""Finding the page a quoted sentence is on (S2-09).

Plain language: a stored passage can run across a page break, so it carries a page *range* (for
example pages 10 to 11). A citation has to name the page of the quote, not just the first page of
the passage it came from, or a reviewer who opens page 10 will not find the sentence. The original
page text is kept (``extracted_key``), so the quote is searched for there, page by page, but only
inside the passage's own range.

The search compares text with all whitespace collapsed, because the passage was made by joining
wrapped lines. A sentence that starts on one page and ends on the next is found by its first and
last words, since page headers and footers sit between the two halves. If the quote cannot be found
(for example the text was changed by hyphenation), the passage's own page range is returned
unchanged, which is always true even if less exact.
"""

from __future__ import annotations

from collections.abc import Sequence

from pharma_ai.features.documents.extraction import PageText

# How many characters from each end of the quote are searched for.
EDGE_CHARS = 50


def _squash(text: str) -> str:
    return " ".join(text.split())


def locate_quote(
    pages: Sequence[PageText], quote: str, page_start: int, page_end: int
) -> tuple[int, int]:
    """The pages where ``quote`` starts and ends, searched only within ``page_start..page_end``."""
    wanted = _squash(quote)
    if not wanted:
        return page_start, page_end
    candidates = [(p.page, _squash(p.text)) for p in pages if page_start <= p.page <= page_end]
    head, tail = wanted[:EDGE_CHARS], wanted[-EDGE_CHARS:]

    starts = [page for page, text in candidates if head in text]
    if not starts:
        return page_start, page_end  # not found: the passage's own range is still true
    first = starts[0]
    ends = [page for page, text in candidates if page >= first and tail in text]
    return first, ends[0] if ends else first
