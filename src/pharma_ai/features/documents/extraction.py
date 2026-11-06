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

Superscripts (pypdf only). A PDF draws an exponent as smaller characters set above the line, and
pypdf's text keeps the characters but not their size or height, so "10⁻⁵" read as "10-5" and
"1 in 10⁶" as "1 in 106". The pypdf engine therefore also asks pypdf for every positioned run of
text with its size and height; a run of digits and signs that is clearly smaller than the line and
raised above it is written with Unicode superscript characters. Footnote markers are raised digits
too, so "products.37" becomes "products.³⁷". Raised letters ("4th", "2nd"), the degree sign and
decimal exponents ("kM0.67") are left as they were, because Unicode has no superscript for them all.
Lowered text (subscripts such as "TD50") is not changed.

Symbol fonts (pypdf only). Bullets and some maths signs drawn with the Symbol or Wingdings font
come out of pypdf as private-use characters that display as boxes; they are replaced by the
characters they draw (U+F0B7 in the Symbol font becomes "•"). See ``symbol_font_table``.

If the runs do not add up to exactly the text pypdf gives on its own, the page keeps pypdf's text
(without either conversion) and the notes say so.
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

from pypdf import PageObject, PdfReader
from pypdf.generic import ContentStream, FloatObject, NameObject

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


class _Run:
    """One positioned run of text as pypdf reports it: size and baseline in page units.

    ``text`` is exactly what pypdf read; ``shown`` is the same text with symbol-font characters
    replaced by the characters they draw (see ``symbol_font_table``).
    """

    __slots__ = ("baseline", "shown", "size", "text", "upright")

    def __init__(
        self, text: str, size: float, baseline: float, upright: bool, shown: str | None = None
    ) -> None:
        self.text = text
        self.size = size
        self.baseline = baseline
        self.upright = upright
        self.shown = text if shown is None else shown


# Symbol fonts. Word draws list bullets, and some Greek letters and maths signs, with the Symbol or
# Wingdings font. Their text often comes out as the font's own code plus 0xF000 (a Unicode
# "private use" character that means nothing to anyone else, shown as a box), so a bullet "•" read
# as U+F0B7. Which character a code draws depends on the font, so the font is identified first (by
# its name, or by the name inside the embedded font program when the PDF gives it a generic name
# such as "CIDFont+F5"); a font that is neither is left alone rather than guessed.
_SYMBOL_CODES = (
    # Adobe Symbol encoding: punctuation and maths signs
    (0x22, "∀"), (0x24, "∃"), (0x27, "∋"), (0x2A, "∗"), (0x2D, "−"), (0x40, "≅"),
    (0x5C, "∴"), (0x5E, "⊥"), (0x7E, "∼"),
    (0xA2, "′"), (0xA3, "≤"), (0xA4, "⁄"), (0xA5, "∞"), (0xA6, "ƒ"), (0xA7, "♣"), (0xA8, "♦"),
    (0xA9, "♥"), (0xAA, "♠"), (0xAB, "↔"), (0xAC, "←"), (0xAD, "↑"), (0xAE, "→"), (0xAF, "↓"),
    (0xB0, "°"), (0xB1, "±"), (0xB2, "″"), (0xB3, "≥"), (0xB4, "×"), (0xB5, "∝"), (0xB6, "∂"),
    (0xB7, "•"), (0xB8, "÷"), (0xB9, "≠"), (0xBA, "≡"), (0xBB, "≈"), (0xBC, "…"), (0xBF, "↵"),
    (0xC0, "ℵ"), (0xC4, "⊗"), (0xC5, "⊕"), (0xC6, "∅"), (0xC7, "∩"), (0xC8, "∪"), (0xC9, "⊃"),
    (0xCA, "⊇"), (0xCB, "⊄"), (0xCC, "⊂"), (0xCD, "⊆"), (0xCE, "∈"), (0xCF, "∉"),
    (0xD0, "∠"), (0xD1, "∇"), (0xD2, "®"), (0xD3, "©"), (0xD4, "™"), (0xD5, "∏"), (0xD6, "√"),
    (0xD7, "⋅"), (0xD8, "¬"), (0xD9, "∧"), (0xDA, "∨"), (0xDB, "⇔"), (0xDC, "⇐"), (0xDD, "⇑"),
    (0xDE, "⇒"), (0xDF, "⇓"), (0xE0, "◊"), (0xE1, "〈"), (0xE2, "®"), (0xE3, "©"), (0xE4, "™"),
    (0xE5, "∑"), (0xF1, "〉"), (0xF2, "∫"),
)  # fmt: skip
_SYMBOL_SAME = "!#%&()+,./0123456789:;<=>?[]_{|}"  # drawn as the ASCII character itself
_SYMBOL_GREEK = (  # the letter keys draw Greek letters
    (0x41, "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"),
    (0x61, "αβχδεφγηιϕκλμνοπθρστυϖωξψζ"),
)
_WINGDINGS_CODES = (  # only the list bullets and arrows Word commonly uses
    (0x6C, "●"), (0x6E, "■"), (0x71, "❑"), (0x76, "❖"), (0xA7, "▪"), (0xD8, "➢"), (0xDF, "←"),
    (0xE0, "→"), (0xE1, "↑"), (0xE2, "↓"), (0xFC, "✓"),
)  # fmt: skip


def _font_table(codes: list[tuple[int, str]]) -> dict[int, str]:
    return {0xF000 + code: char for code, char in codes}


SYMBOL_TABLE = _font_table(
    [*_SYMBOL_CODES, *((ord(c), c) for c in _SYMBOL_SAME)]
    + [(start + i, c) for start, letters in _SYMBOL_GREEK for i, c in enumerate(letters)]
)
WINGDINGS_TABLE = _font_table(list(_WINGDINGS_CODES))
_PRIVATE_USE = re.compile("[-]")


def _font_names(font: Any) -> bytes:
    """The font's names, and the start of its embedded font program, which holds its real name."""
    try:
        font = font.get_object()
        parts = [str(font.get("/BaseFont", ""))]
        descendants = font.get("/DescendantFonts")
        inner = descendants[0].get_object() if descendants else font
        parts.append(str(inner.get("/BaseFont", "")))
        descriptor = inner.get("/FontDescriptor")
        program = b""
        if descriptor is not None:
            descriptor = descriptor.get_object()
            parts.append(str(descriptor.get("/FontName", "")))
            for key in ("/FontFile", "/FontFile2", "/FontFile3"):
                if key in descriptor:
                    program = descriptor[key].get_object().get_data()[:65536]
                    break
        return " ".join(parts).encode("latin-1", "replace") + b" " + program
    except Exception:  # noqa: BLE001 - a damaged font record just means "unknown font"
        return b""


def symbol_font_table(font: Any) -> dict[int, str] | None:
    """The table for a Symbol or Wingdings font, or None for any other (or unknown) font."""
    if font is None:
        return None
    names = _font_names(font)
    if b"Wingdings" in names:
        return WINGDINGS_TABLE
    if b"Symbol" in names:
        return SYMBOL_TABLE
    return None


# A superscript is at most this share of the line's size, and its baseline sits between these two
# shares of the line's size above the line's baseline. Measured on the corpus: exponents and
# footnote markers are 0.65 to 0.67 of the size and 0.35 to 0.38 of it above the line.
SUPERSCRIPT_MAX_SIZE = 0.85
SUPERSCRIPT_MIN_RISE = 0.15
SUPERSCRIPT_MAX_RISE = 0.7
_TO_SUPERSCRIPT = str.maketrans("0123456789+-−–=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁻⁼⁽⁾")
# Digits and signs, with commas and spaces between them ("17,18"); at least one digit or sign.
_CONVERTIBLE = re.compile(r"(?=.*[0-9+\-−–])[0-9+\-−–=()]+(?:[, ]+[0-9+\-−–=()]+)*")
# pypdf reports a run only when the font changes, so a do-nothing transform (the identity matrix)
# is placed before every operator that moves the text position, making each placed run its own.
_POSITION_OPERATORS = frozenset({b"Tm", b"Td", b"TD", b"T*", b"'", b'"'})
_IDENTITY = [FloatObject(v) for v in (1, 0, 0, 1, 0, 0)]


def _multiply(m: list[float], n: list[float]) -> list[float]:
    return [
        m[0] * n[0] + m[1] * n[2],
        m[0] * n[1] + m[1] * n[3],
        m[2] * n[0] + m[3] * n[2],
        m[2] * n[1] + m[3] * n[3],
        m[4] * n[0] + m[5] * n[2] + n[4],
        m[4] * n[1] + m[5] * n[3] + n[5],
    ]


def _runs(reader: PdfReader, page: PageObject) -> list[_Run]:
    """Every positioned run of the page's text, in pypdf's order. Changes the page in memory."""
    stream = ContentStream(page.get_contents(), reader, "bytes")
    operations = []
    for operands, operator in stream.operations:
        if operator in _POSITION_OPERATORS:
            operations.append((_IDENTITY, b"cm"))
        operations.append((operands, operator))
    stream.operations = operations
    page[NameObject("/Contents")] = stream
    runs: list[_Run] = []
    tables: dict[int, dict[int, str] | None] = {}  # per font record, looked up once

    def collect(text: str, cm: list[float], tm: list[float], font: Any, size: float) -> None:
        m = _multiply([float(v) for v in tm], [float(v) for v in cm])
        upright = abs(m[1]) < 1e-6 and abs(m[2]) < 1e-6 and m[0] > 0 and m[3] > 0
        shown = text
        if _PRIVATE_USE.search(text):
            if id(font) not in tables:
                tables[id(font)] = symbol_font_table(font)
            table = tables[id(font)]
            shown = text.translate(table) if table else text
        runs.append(_Run(text, float(size) * abs(m[3]), m[5], upright, shown))

    page.extract_text(visitor_text=collect)
    return runs


def _is_raised(run: _Run, line: tuple[float, float] | None) -> bool:
    if line is None:
        return False
    size, baseline = line
    rise = run.baseline - baseline
    return (
        run.size <= SUPERSCRIPT_MAX_SIZE * size
        and SUPERSCRIPT_MIN_RISE * size <= rise <= SUPERSCRIPT_MAX_RISE * size
    )


def raised_runs(runs: list[_Run]) -> list[bool]:
    """Which runs are superscripts: smaller than the line they sit on and raised above it.

    The line is the last ordinary run on about the same baseline; a raised run that opens a line
    (a footnote marker) is compared with the next larger run instead.
    """
    flags: list[bool] = []
    line: tuple[float, float] | None = None
    for i, run in enumerate(runs):
        if not run.text.strip() or not run.upright:
            flags.append(False)
            continue
        reference = line
        if reference is None or abs(run.baseline - reference[1]) > reference[0]:
            following = (
                r for r in runs[i + 1 : i + 6] if r.text.strip() and r.upright and r.size > run.size
            )
            nxt = next(following, None)
            reference = (nxt.size, nxt.baseline) if nxt else None
        raised = _is_raised(run, reference)
        flags.append(raised)
        if not raised:
            line = (run.size, run.baseline)
    return flags


def mark_superscripts(runs: list[_Run], flags: list[bool]) -> str:
    """The joined text as shown, with each group of raised digits and signs as superscripts."""
    parts: list[str] = []
    i = 0
    while i < len(runs):
        if not flags[i]:
            parts.append(runs[i].shown)
            i += 1
            continue
        j = i + 1  # a group: raised runs, and blank runs between them on the same line
        while j < len(runs) and (
            flags[j]
            or (
                not runs[j].text.strip()
                and "\n" not in runs[j].text
                and j + 1 < len(runs)
                and flags[j + 1]
            )
        ):
            j += 1
        group = "".join(run.shown for run in runs[i:j])
        core = group.strip()
        if _CONVERTIBLE.fullmatch(core):
            start = group.index(core)
            group = group[:start] + core.translate(_TO_SUPERSCRIPT) + group[start + len(core) :]
        parts.append(group)
        i = j
    return "".join(parts)


def _page_text(reader: PdfReader, page: PageObject) -> tuple[str, bool]:
    """The page's text with superscripts and symbol-font characters converted, and False if
    that had to be given up (the page then keeps pypdf's own text)."""
    plain = page.extract_text() or ""
    if page.get_contents() is None:
        return plain, True
    try:
        runs = _runs(reader, page)
    except Exception:  # noqa: BLE001 - keep pypdf's text if the second reading fails in any way
        return plain, False
    if "".join(run.text for run in runs) != plain:
        return plain, False
    return mark_superscripts(runs, raised_runs(runs)), True


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
        unmarked: list[int] = []
        for number, page in enumerate(page_objects, start=1):
            try:
                raw, marked = _page_text(reader, page)
                text = clean_text(raw)
            except Exception as exc:  # noqa: BLE001 - pypdf raises arbitrary errors on damaged pages
                text, marked = "", True
                notes.append(f"page {number}: text could not be read ({type(exc).__name__})")
            if not marked:
                unmarked.append(number)
            pages.append(PageText(number, text))
        if not pages:
            raise ExtractionError("the PDF has no pages")
        result = classify(self.name, pages, notes)
        if unmarked:
            # The text is complete, so the status is not lowered; only exponents may be flat and
            # symbol-font bullets may show as boxes.
            listed = ", ".join(str(n) for n in unmarked)
            note = (
                f"superscripts and symbol-font characters could not be converted on page(s) "
                f"{listed}; there an exponent such as 10⁻⁵ reads as 10-5 and a bullet may show "
                "as a box"
            )
            result = replace(result, notes=(*result.notes, note))
        return result


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
