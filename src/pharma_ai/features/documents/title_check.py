"""Does an uploaded PDF look like the register entry it was uploaded under?

Plain language: the register says what ``FDA-NITRO-2024`` is (its title). An administrator could pick
the wrong entry or the wrong file. This looks at the first pages of the PDF's text and counts how many
of the title's distinctive words appear there. It cannot prove a file is right (a scan has no text,
and a title can be printed differently from the register), so it only produces a **warning** that the
upload screen shows next to the result; the upload itself is not refused.
"""

from __future__ import annotations

import re

# Words that appear in many titles and say nothing about which document it is.
_COMMON = frozenset(
    {
        *("guidance", "industry", "guideline", "guidelines", "draft", "final", "with", "from"),
        *("that", "this", "drug", "drugs", "product", "products", "human", "pharmaceutical"),
        *("pharmaceuticals", "applications", "application", "guide", "revision", "revised"),
        "version",
    }
)
MIN_WORD_LENGTH = 4
MIN_TITLE_WORDS = 2  # with fewer distinctive words a comparison would mean little
FIRST_PAGES = 3
MIN_SHARE = 0.5


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= MIN_WORD_LENGTH}


def title_words(title: str) -> set[str]:
    return _words(title) - _COMMON


def title_warning(title: str, first_pages: list[str]) -> str | None:
    """A sentence to show the administrator, or None if the PDF looks like its register entry."""
    wanted = title_words(title)
    if len(wanted) < MIN_TITLE_WORDS:
        return None
    seen: set[str] = set()
    for page in first_pages[:FIRST_PAGES]:
        seen |= _words(page)
    if not any(first_pages[:FIRST_PAGES]):
        return None  # no text on the first pages (a scan): nothing to compare
    found = wanted & seen
    if len(found) / len(wanted) >= MIN_SHARE:
        return None
    missing = ", ".join(sorted(wanted - found)[:5])
    return (
        f"The first pages of this PDF do not seem to mention the register title "
        f'"{title}" (missing: {missing}). Check that this is the right file before indexing it.'
    )
