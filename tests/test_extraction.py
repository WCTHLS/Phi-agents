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
from pdf_factory import make_pdf
from pypdf import PdfReader, PdfWriter

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
