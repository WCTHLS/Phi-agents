"""Text extraction from PDFs, page by page (S2-03; ADR-003, ADR-007).

Plain language: a PDF has a *text layer* when its words are real, selectable text. A scanned page is
just a picture of words and has no text layer, so nothing can be extracted without OCR (optical
character recognition, reading words out of a picture). OCR is not built yet. A page without text is
recorded as such, so nobody believes it was searched, and the page number is kept for every page
because a citation must point at an exact page.

Two engines share one interface:
- ``pypdf``: reads the PDF in-process. No server, fast, used by default and in tests.
- ``tika``: asks an Apache Tika server. If Tika cannot be reached the extractor falls back to pypdf
  and says so in the result's notes.
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Literal

from pypdf import PdfReader

from pharma_ai.core.config import settings

logger = logging.getLogger(__name__)

# A page with fewer than this many characters of text is treated as having no text layer.
MIN_PAGE_CHARS = 25

TextLayer = Literal["yes", "partial", "no"]
ExtractionStatus = Literal["ok", "partial", "failed"]


class ExtractionError(Exception):
    """The file cannot be read as a PDF (damaged, encrypted, or not a PDF)."""


class ExtractorUnavailable(Exception):
    """An extraction server could not be reached or gave an unusable answer."""


@dataclass(frozen=True)
class PageText:
    page: int  # 1-based, as printed in a citation
    text: str


@dataclass(frozen=True)
class ExtractionResult:
    method: str  # "pypdf" or "tika"
    pages: tuple[PageText, ...]
    text_layer: TextLayer  # do all, some or none of the pages have selectable text?
    status: ExtractionStatus  # ok = fully usable, partial = some pages unusable, failed = none
    notes: tuple[str, ...]  # human-readable reasons for any uncertainty

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "page_count": self.page_count,
            "text_layer": self.text_layer,
            "status": self.status,
            "notes": list(self.notes),
            "pages": [{"page": p.page, "text": p.text} for p in self.pages],
        }


def clean_text(raw: str) -> str:
    """Normalise line endings and drop characters the database cannot store (NUL)."""
    text = raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def classify(method: str, pages: list[PageText], notes: list[str]) -> ExtractionResult:
    """Decide the text-layer and status from the pages, adding a note for unreadable pages."""
    total = len(pages)
    with_text = sum(1 for page in pages if len(page.text) >= MIN_PAGE_CHARS)
    if total == 0 or with_text == 0:
        text_layer: TextLayer = "no"
    elif with_text == total:
        text_layer = "yes"
    else:
        text_layer = "partial"
    if text_layer != "yes":
        empty = total - with_text
        notes.append(
            f"{empty} of {total} pages have no selectable text. OCR is not available, so they "
            "cannot be searched."
        )
    if text_layer == "no":
        status: ExtractionStatus = "failed"
    elif text_layer == "partial" or notes:
        status = "partial"
    else:
        status = "ok"
    return ExtractionResult(method, tuple(pages), text_layer, status, tuple(notes))


class PdfExtractor(ABC):
    name: str

    @abstractmethod
    def extract(self, data: bytes) -> ExtractionResult:
        """Return the text of every page, or raise ExtractionError / ExtractorUnavailable."""


class PypdfExtractor(PdfExtractor):
    name = "pypdf"

    def extract(self, data: bytes) -> ExtractionResult:
        notes: list[str] = []
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionError("the PDF is password-protected")
            page_objects = list(reader.pages)
        except ExtractionError:
            raise
        except Exception as exc:  # pypdf raises many kinds of errors on damaged files
            raise ExtractionError(f"not a readable PDF ({type(exc).__name__})") from exc

        pages: list[PageText] = []
        for number, page in enumerate(page_objects, start=1):
            try:
                text = clean_text(page.extract_text() or "")
            except Exception as exc:  # noqa: BLE001 - pypdf raises arbitrary errors on damaged pages
                text = ""
                notes.append(f"page {number}: text could not be read ({type(exc).__name__})")
            pages.append(PageText(number, text))
        if not pages:
            raise ExtractionError("the PDF has no pages")
        return classify(self.name, pages, notes)


class _TikaPageParser(HTMLParser):
    """Collects the text of each ``<div class="page">`` in Tika's XHTML answer."""

    _BLOCKS = frozenset({"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[str] = []
        self._depth = 0
        self._page_depth: int | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            self._depth += 1
            classes = (dict(attrs).get("class") or "").split()
            if self._page_depth is None and "page" in classes:
                self._page_depth = self._depth
                self._parts = []
        elif tag == "br" and self._page_depth is not None:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._page_depth == self._depth:
                self.pages.append("".join(self._parts))
                self._page_depth = None
            self._depth -= 1
        elif tag in self._BLOCKS and self._page_depth is not None:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._page_depth is not None:
            self._parts.append(data)


class TikaExtractor(PdfExtractor):
    name = "tika"

    # The plain /tika endpoint has no page boundaries. /rmeta/xml answers with JSON whose first
    # record holds the document as XHTML with one <div class="page"> per page. Tika 4 names the
    # field "tk:content"; earlier versions call it "X-TIKA:content".
    _CONTENT_KEYS = ("tk:content", "X-TIKA:content")

    def __init__(self, url: str, timeout: float = 60.0) -> None:
        self._endpoint = url.rstrip("/") + "/rmeta/xml"
        self._timeout = timeout

    def extract(self, data: bytes) -> ExtractionResult:
        request = urllib.request.Request(
            self._endpoint,
            data=data,
            method="PUT",
            headers={"Content-Type": "application/pdf", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                records = json.loads(response.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ExtractorUnavailable(f"Tika did not answer ({type(exc).__name__})") from exc
        document = records[0] if isinstance(records, list) and records else {}
        body = next(
            (document[k] for k in self._CONTENT_KEYS if isinstance(document.get(k), str)), ""
        )
        parser = _TikaPageParser()
        parser.feed(body)
        parser.close()
        if not parser.pages:
            raise ExtractorUnavailable("Tika's answer had no page structure")
        pages = [PageText(n, clean_text(text)) for n, text in enumerate(parser.pages, start=1)]
        return classify(self.name, pages, [])


def extract_pdf(
    data: bytes, *, engine: str | None = None, tika_url: str | None = None
) -> ExtractionResult:
    """Extract the text of a PDF with the configured engine (Tika falls back to pypdf)."""
    engine = engine or settings.extraction_engine
    if engine == "tika":
        try:
            return TikaExtractor(tika_url or settings.tika_url).extract(data)
        except ExtractorUnavailable as exc:
            logger.warning("Tika extraction unavailable, using pypdf instead: %s", exc)
            fallback = PypdfExtractor().extract(data)
            note = f"Tika was not used ({exc}); text was read with pypdf instead."
            return replace(fallback, notes=(note, *fallback.notes))
    return PypdfExtractor().extract(data)


_PDF_HEADER = re.compile(rb"%PDF-\d\.\d")


def looks_like_pdf(data: bytes) -> bool:
    """True if the file starts like a PDF (the header may follow a few bytes of junk)."""
    return bool(_PDF_HEADER.search(data[:1024]))
