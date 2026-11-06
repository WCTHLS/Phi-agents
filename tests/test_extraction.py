"""Tests for PDF text extraction (S2-03, ADR-003, ADR-007)."""

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pdf_factory import make_pdf, make_pdf_from_streams
from pypdf import PdfReader, PdfWriter

from pharma_ai.features.documents import extraction
from pharma_ai.features.documents.extraction import (
    MIN_PAGE_CHARS,
    ExtractionError,
    ExtractorUnavailable,
    PypdfExtractor,
    TikaExtractor,
    clean_text,
    extract_pdf,
    looks_like_pdf,
)

PAGE_ONE = "Acceptable intake limits for nitrosamines\nNDMA: 96 ng/day"
PAGE_TWO = "Root causes and excipient risk factors (see section 4)"
PAGE_THREE = "Dissolution testing of immediate release dosage forms"


# --- pypdf engine ----------------------------------------------------------------------------


def test_every_page_is_returned_with_its_own_number_and_text() -> None:
    result = PypdfExtractor().extract(make_pdf([PAGE_ONE, PAGE_TWO, PAGE_THREE]))
    assert [p.page for p in result.pages] == [1, 2, 3]
    assert "NDMA: 96 ng/day" in result.pages[0].text
    assert "excipient risk factors" in result.pages[1].text
    assert "Dissolution testing" in result.pages[2].text
    assert result.page_count == 3
    assert (result.method, result.text_layer, result.status) == ("pypdf", "yes", "ok")
    assert result.notes == ()


def test_a_page_with_no_text_is_flagged_but_keeps_its_page_number() -> None:
    result = PypdfExtractor().extract(make_pdf([PAGE_ONE, None, PAGE_THREE]))
    assert [p.page for p in result.pages] == [1, 2, 3]
    assert result.pages[1].text == ""
    assert result.text_layer == "partial" and result.status == "partial"
    assert any("1 of 3 pages have no selectable text" in note for note in result.notes)


def test_a_document_with_no_text_at_all_fails_with_a_clear_reason() -> None:
    result = PypdfExtractor().extract(make_pdf([None, None]))
    assert result.text_layer == "no" and result.status == "failed"
    assert any("2 of 2 pages" in note and "OCR" in note for note in result.notes)


def test_a_nearly_empty_page_does_not_count_as_having_text() -> None:
    # A stray page number is not a text layer.
    result = PypdfExtractor().extract(make_pdf(["12", PAGE_TWO]))
    assert len("12") < MIN_PAGE_CHARS
    assert result.text_layer == "partial"


def test_brackets_and_backslashes_in_the_text_survive() -> None:
    result = PypdfExtractor().extract(make_pdf([r"Limit (see Table 2) for C:\data and 5\3 ratio"]))
    assert r"(see Table 2)" in result.pages[0].text
    assert r"C:\data" in result.pages[0].text


@pytest.mark.parametrize(
    "data",
    [b"", b"just some text, not a pdf", b"%PDF-1.4\nnot really\n%%EOF", b"\x00" * 100],
)
def test_something_that_is_not_a_readable_pdf_is_refused(data: bytes) -> None:
    with pytest.raises(ExtractionError):
        PypdfExtractor().extract(data)


def test_a_password_protected_pdf_is_refused_with_that_reason() -> None:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([PAGE_ONE]))))
    writer.encrypt("a-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ExtractionError, match="password"):
        PypdfExtractor().extract(buffer.getvalue())


def test_cleaning_removes_nul_normalises_line_ends_and_trims() -> None:
    assert clean_text("a\x00b  \r\nc\rd   \n\n") == "ab\nc\nd"


def test_the_result_can_be_saved_as_plain_data() -> None:
    result = PypdfExtractor().extract(make_pdf([PAGE_ONE, PAGE_TWO]))
    data = result.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["page_count"] == 2 and data["pages"][0]["page"] == 1
    assert data["method"] == "pypdf" and data["text_layer"] == "yes"


def test_pdf_detection_allows_a_little_junk_before_the_header() -> None:
    assert looks_like_pdf(make_pdf([PAGE_ONE]))
    assert looks_like_pdf(b"junk\r\n" + make_pdf([PAGE_ONE]))
    assert not looks_like_pdf(b"<html>%PDF-1.4 inside</html>".rjust(2000))
    assert not looks_like_pdf(b"")


# --- superscripts (pypdf engine) -------------------------------------------------------------
# Each page is drawn exactly: x positions follow Helvetica's character widths so that pypdf puts
# no space where the printed page has none. "Tf" sets the font size, "Tm" places the text (its
# first number scales it, its last is the height of the baseline).

TAIL = b"( and more words after it) Tj"


def _text_of(stream: bytes) -> str:
    result = PypdfExtractor().extract(make_pdf_from_streams([stream]))
    assert result.notes == ()
    return result.pages[0].text


def test_an_exponent_drawn_smaller_and_higher_is_written_as_a_superscript() -> None:
    # As ICH M7 draws it: the font size changes to 8, and "-" and "5" are placed 4.6 higher.
    stream = (
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (a theoretical 10) Tj /F1 8 Tf 1 0 0 1 154.1 704.6 Tm "
        b"(-) Tj 1 0 0 1 156.8 704.6 Tm (5) Tj /F1 12 Tf 1 0 0 1 161.3 700 Tm " + TAIL + b" ET"
    )
    assert _text_of(stream) == "a theoretical 10⁻⁵ and more words after it"


def test_an_exponent_drawn_with_a_smaller_text_matrix_is_found_too() -> None:
    # As the FDA nitrosamine guidance draws it: the font stays the same and the text matrix
    # shrinks the "5". pypdf alone joins it into the line as "105". The PDF has no minus sign
    # here, and none is added.
    stream = (
        b"BT /F1 1 Tf 12 0 0 12 72 700 Tm (cancer risk of 10) Tj 7.98 0 0 7.98 160.1 704.5 Tm "
        b"(5) Tj 12 0 0 12 164.6 700 Tm " + TAIL + b" ET"
    )
    assert _text_of(stream) == "cancer risk of 10⁵ and more words after it"


def test_footnote_markers_and_ion_charges_are_superscripts_as_printed() -> None:
    footnote_list = (
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (therapeutic index.) Tj /F1 8 Tf 1 0 0 1 166.8 704.5 Tm "
        b"(17,) Tj 1 0 0 1 177.9 704.5 Tm (18) Tj /F1 12 Tf 1 0 0 1 186.8 700 Tm " + TAIL + b" ET"
    )
    ion = (
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (and Ca) Tj /F1 8 Tf 1 0 0 1 110.8 704.5 Tm (2+) Tj "
        b"/F1 12 Tf 1 0 0 1 120 700 Tm " + TAIL + b" ET"
    )
    assert _text_of(footnote_list) == "therapeutic index.¹⁷,¹⁸ and more words after it"
    assert _text_of(ion) == "and Ca²⁺ and more words after it"


def test_a_footnote_marker_that_opens_a_line_is_compared_with_the_text_after_it() -> None:
    stream = (
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Body text line of the page.) Tj ET "
        b"BT /F1 1 Tf 6.48 0 0 6.48 72 103.5 Tm (39) Tj 10 0 0 10 79.3 100 Tm "
        b"( See the guidance) Tj ET"
    )
    assert _text_of(stream) == "Body text line of the page.\n³⁹ See the guidance"


@pytest.mark.parametrize(
    "name, stream, expected",
    [
        (
            "raised letters have no superscript form in this rule",
            b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Bldg., 4) Tj /F1 8 Tf 1 0 0 1 112.8 704.5 Tm (th) Tj "
            b"/F1 12 Tf 1 0 0 1 119.5 700 Tm " + TAIL + b" ET",
            "Bldg., 4th and more words after it",
        ),
        (
            "a decimal exponent cannot be written with superscript characters",
            b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (S = kM) Tj /F1 8 Tf 1 0 0 1 109.8 704.5 Tm (0.67) Tj "
            b"/F1 12 Tf 1 0 0 1 124 700 Tm " + TAIL + b" ET",
            "S = kM0.67 and more words after it",
        ),
        (
            "a subscript is lowered, not raised",
            b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (the TD) Tj /F1 8 Tf 1 0 0 1 108.1 697 Tm (50) Tj "
            b"/F1 12 Tf 1 0 0 1 117 700 Tm " + TAIL + b" ET",
            "the TD50 and more words after it",
        ),
        (
            "smaller on the same baseline is not a superscript",
            b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (dose of 10) Tj /F1 8 Tf 1 0 0 1 128.1 700 Tm (5) Tj "
            b"/F1 12 Tf 1 0 0 1 132.6 700 Tm " + TAIL + b" ET",
            "dose of 105 and more words after it",
        ),
        (
            "raised at the same size is not a superscript",
            b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (dose of 10) Tj 1 0 0 1 128.1 704.5 Tm (5) Tj "
            b"1 0 0 1 134.8 700 Tm " + TAIL + b" ET",
            "dose of 105 and more words after it",
        ),
    ],
)
def test_text_that_is_not_a_superscript_is_left_as_it_was(
    name: str, stream: bytes, expected: str
) -> None:
    assert _text_of(stream) == expected, name


def test_plain_pages_read_exactly_as_before() -> None:
    data = make_pdf([PAGE_ONE, PAGE_TWO, PAGE_THREE])
    plain = [clean_text(page.extract_text()) for page in PdfReader(io.BytesIO(data)).pages]
    assert [page.text for page in PypdfExtractor().extract(data).pages] == plain


@pytest.mark.parametrize("failure", ["different text", "an error"])
def test_if_marking_is_unsure_the_page_keeps_pypdfs_text_and_the_notes_say_so(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def broken(_reader: object, _page: object) -> list[object]:
        if failure == "an error":
            raise RuntimeError("boom")
        return [extraction._Run("not what pypdf read", 12.0, 700.0, True)]

    monkeypatch.setattr(extraction, "_runs", broken)
    result = PypdfExtractor().extract(make_pdf([PAGE_ONE, PAGE_TWO]))
    assert "NDMA: 96 ng/day" in result.pages[0].text
    assert "not what pypdf read" not in result.pages[0].text
    assert result.status == "ok"  # the text is complete; only exponents may be flat
    note = (
        "superscripts and symbol-font characters could not be converted on page(s) 1, 2; there "
        "an exponent such as 10⁻⁵ reads as 10-5 and a bullet may show as a box"
    )
    assert result.notes == (note,)


# --- symbol-font characters (pypdf engine) ---------------------------------------------------
# The /F2 font of these PDFs reads code 0xB7 as U+F0B7 and so on, the way bullets drawn with Word's
# Symbol font come out of the corpus PDFs (ICH Q3D and Q9 show 142 of them as boxes otherwise).

LIST_PAGE = (
    b"BT /F1 12 Tf 1 0 0 1 72 720 Tm (Considerations, in order of relevance:) Tj ET "
    b"BT /F2 12 Tf 1 0 0 1 72 700 Tm <B7> Tj /F1 12 Tf 1 0 0 1 80 700 Tm "
    b"( The likely oxidation state of the element) Tj ET "
    b"BT /F1 12 Tf 1 0 0 1 72 680 Tm (21.3 mg/kg ) Tj /F2 12 Tf 1 0 0 1 132 680 Tm <B8> Tj "
    b"/F1 12 Tf 1 0 0 1 139 680 Tm ( 50,000 = 0.42 ) Tj /F2 12 Tf 1 0 0 1 220 680 Tm <6D> Tj "
    b"/F1 12 Tf 1 0 0 1 227 680 Tm (g/kg) Tj ET"
)
ARROW_PAGE = (
    b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Efflux ratio = PappB) Tj /F2 12 Tf 1 0 0 1 192.1 700 Tm "
    b"<E0> Tj /F1 12 Tf 1 0 0 1 204 700 Tm (A over the other way) Tj ET"
)


def _symbol_text(stream: bytes, font: str, program: bytes | None = None) -> str:
    data = make_pdf_from_streams([stream], symbol_font=font, symbol_program=program)
    result = PypdfExtractor().extract(data)
    assert result.notes == ()
    return result.pages[0].text


@pytest.mark.parametrize(
    "font, program",
    [
        ("SymbolMT", None),
        # The corpus's own case: a generic name, with "Symbol" inside the embedded font program.
        ("CIDFont+F5", b"%!PS-AdobeFont-1.0: Symbol 001.008"),
    ],
)
def test_symbol_font_bullets_and_signs_become_the_characters_they_draw(
    font: str, program: bytes | None
) -> None:
    text = _symbol_text(LIST_PAGE, font, program)
    assert text.splitlines()[1].startswith("• ") and "The likely oxidation state" in text
    assert "21.3 mg/kg ÷ 50,000 = 0.42 μg/kg" in text
    assert not any("" <= ch <= "" for ch in text)


def test_a_wingdings_character_uses_the_wingdings_table() -> None:
    # In Wingdings code 0xE0 is an arrow; in Symbol the same code is a lozenge.
    assert "PappB → A" in _symbol_text(ARROW_PAGE, "AZGAMO+Wingdings-Regular")


def test_a_private_use_character_from_an_unknown_font_is_left_alone() -> None:
    # Guessing could show the wrong symbol, so the character stays as pypdf read it.
    text = _symbol_text(LIST_PAGE, "CIDFont+F9", b"%!PS-AdobeFont-1.0: SomethingElse")
    assert "" in text and "" in text and "•" not in text


def test_the_symbol_table_follows_the_symbol_fonts_layout() -> None:
    table = extraction.SYMBOL_TABLE
    samples = {0xB7: "•", 0xB8: "÷", 0x6D: "μ", 0xB3: "≥", 0xE5: "∑", 0x3D: "=", 0x41: "Α"}
    for code, char in samples.items():
        assert table[0xF000 + code] == char
    assert extraction.WINGDINGS_TABLE[0xF0E0] == "→"
    assert extraction.symbol_font_table(None) is None
    assert extraction.symbol_font_table(object()) is None  # a damaged font record: unknown


# --- Tika engine, against a stand-in server ---------------------------------------------------

XHTML = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Doc title</title></head><body>'
    '<div class="page"><div class="sect"><p>First &amp; foremost</p><p>NDMA 96 ng/day</p></div>'
    '<div class="artifact"><p>footer 1</p></div></div>'
    '<div class="page"><p>Second page has enough text to count as text.</p><ul><li>item</li></ul>'
    "</div></body></html>"
)


class _FakeTika:
    """A tiny HTTP server that answers like Tika, and remembers what it was asked."""

    def __init__(self, status: int, body: bytes) -> None:
        self.requests: list[tuple[str, str, str, int]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                outer.requests.append(
                    (
                        self.command,
                        self.path,
                        self.headers.get("Accept", ""),
                        length,
                    )
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def fake_tika() -> Iterator[Callable[[int, object], _FakeTika]]:
    servers: list[_FakeTika] = []

    def start(status: int, payload: object) -> _FakeTika:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        server = _FakeTika(status, body)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


@pytest.mark.parametrize("key", ["tk:content", "X-TIKA:content"])
def test_tika_pages_are_read_from_either_field_name(
    fake_tika: Callable[[int, object], _FakeTika], key: str
) -> None:
    server = fake_tika(200, [{key: XHTML}, {key: "<html>an embedded file</html>"}])
    result = TikaExtractor(server.url).extract(b"%PDF-1.4 anything")
    assert [p.page for p in result.pages] == [1, 2]
    assert result.pages[0].text == "First & foremost\nNDMA 96 ng/day\nfooter 1"
    assert "Second page" in result.pages[1].text and "item" in result.pages[1].text
    assert "Doc title" not in "".join(p.text for p in result.pages)
    assert result.method == "tika"
    assert server.requests == [("PUT", "/rmeta/xml", "application/json", 17)]


@pytest.mark.parametrize(
    "status, payload",
    [
        (500, {"error": "boom"}),
        (422, b"unprocessable"),
        (200, b"this is not json"),
        (200, []),
        (200, [{"tk:content": "<html><body>no page markers</body></html>"}]),
    ],
)
def test_tika_problems_are_reported_as_unavailable(
    fake_tika: Callable[[int, object], _FakeTika], status: int, payload: object
) -> None:
    server = fake_tika(status, payload)
    with pytest.raises(ExtractorUnavailable):
        TikaExtractor(server.url).extract(b"%PDF-1.4 anything")


def test_an_unreachable_tika_is_reported_as_unavailable() -> None:
    with pytest.raises(ExtractorUnavailable):
        TikaExtractor("http://127.0.0.1:1", timeout=2).extract(b"%PDF-1.4 anything")


# --- choosing the engine ---------------------------------------------------------------------


def test_the_default_engine_is_pypdf() -> None:
    assert extract_pdf(make_pdf([PAGE_ONE])).method == "pypdf"


def test_if_tika_is_down_pypdf_is_used_and_the_result_says_so() -> None:
    result = extract_pdf(make_pdf([PAGE_ONE]), engine="tika", tika_url="http://127.0.0.1:1")
    assert result.method == "pypdf"
    assert result.notes and result.notes[0].startswith("Tika was not used")
    assert "NDMA" in result.pages[0].text


def test_tika_is_used_when_it_answers(fake_tika: Callable[[int, object], _FakeTika]) -> None:
    server = fake_tika(200, [{"tk:content": XHTML}])
    result = extract_pdf(make_pdf([PAGE_ONE]), engine="tika", tika_url=server.url)
    assert result.method == "tika" and result.notes == ()


# --- the real Tika server (runs where one is reachable, for example in CI) -------------------


def _real_tika_url() -> str | None:
    url = os.environ.get("TIKA_URL")
    if not url:
        return None
    for _ in range(30):
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/version", timeout=2):
                return url
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return None


def test_a_real_tika_server_gives_the_same_pages_as_pypdf() -> None:
    url = _real_tika_url()
    if url is None:
        pytest.skip("no Tika server reachable (set TIKA_URL to run this test)")
    data = make_pdf([PAGE_ONE, PAGE_TWO, PAGE_THREE])
    tika = TikaExtractor(url, timeout=60).extract(data)
    local = PypdfExtractor().extract(data)
    assert tika.page_count == local.page_count == 3
    for tika_page, local_page in zip(tika.pages, local.pages, strict=True):
        assert tika_page.page == local_page.page
        assert local_page.text.split()[0] in tika_page.text
    assert tika.text_layer == "yes"
