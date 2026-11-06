"""Builds tiny valid PDFs for tests, so no real (and not-in-Git) regulatory documents are needed.

A page given as text gets a real text layer. A page given as ``None`` is blank, which behaves like a
scanned page: it shows nothing to extract. ``make_pdf_from_streams`` takes each page's drawing
instructions as written, for tests that need exact sizes and positions (superscripts); the font is
Helvetica, named /F1. With ``symbol_font`` it also has a font /F2 of that name whose characters
read as Word's symbol fonts read: code 0xB7 as U+F0B7, and so on (0xF000 plus the code), the way a
bullet drawn with the Symbol font comes out of a real PDF. ``symbol_program`` is stored as that
font's embedded font program, which is where a PDF that names the font "CIDFont+F5" keeps its real
name.
"""

from __future__ import annotations

# Codes the /F2 font maps to the private-use range: Symbol's bullet, divide, mu and Wingdings' arrow.
SYMBOL_CODES = (0x6D, 0xB7, 0xB8, 0xE0)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str | None]) -> bytes:
    """Return the bytes of a PDF with one page per item."""
    if not pages:
        raise ValueError("a PDF needs at least one page")
    streams = []
    for text in pages:
        if text is None:
            streams.append(b"")
            continue
        lines = text.split("\n")
        body = "BT /F1 11 Tf 72 720 Td 14 TL\n"
        body += "\n".join(f"({_escape(line)}) Tj T*" for line in lines)
        body += "\nET"
        streams.append(body.encode("latin-1"))
    return make_pdf_from_streams(streams)


def _stream(data: bytes) -> bytes:
    return b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


def make_pdf_from_streams(
    streams: list[bytes], *, symbol_font: str | None = None, symbol_program: bytes | None = None
) -> bytes:
    """Return the bytes of a PDF whose pages draw exactly the given content streams."""
    if not streams:
        raise ValueError("a PDF needs at least one page")
    pages = streams
    objects: dict[int, bytes] = {}
    page_ids = [4 + 2 * index for index in range(len(pages))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    fonts = "/F1 3 0 R"

    if symbol_font is not None:
        font_id = page_ids[-1] + 2
        cmap_id, descriptor_id, program_id = font_id + 1, font_id + 2, font_id + 3
        pairs = "\n".join(f"<{code:02X}> <F0{code:02X}>" for code in SYMBOL_CODES)
        cmap = (
            "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            "1 begincodespacerange <00> <FF> endcodespacerange\n"
            f"{len(SYMBOL_CODES)} beginbfchar\n{pairs}\nendbfchar\n"
            "endcmap CMapName currentdict /CMap defineresource pop end end"
        ).encode()
        objects[font_id] = (
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{symbol_font} "
            f"/FontDescriptor {descriptor_id} 0 R /ToUnicode {cmap_id} 0 R >>"
        ).encode()
        objects[cmap_id] = _stream(cmap)
        program = f"/FontFile3 {program_id} 0 R " if symbol_program is not None else ""
        objects[descriptor_id] = (
            f"<< /Type /FontDescriptor /FontName /{symbol_font} /Flags 4 {program}"
            "/FontBBox [0 0 1000 1000] /ItalicAngle 0 /Ascent 1000 /Descent 0 /CapHeight 1000 "
            "/StemV 80 >>"
        ).encode()
        if symbol_program is not None:
            objects[program_id] = _stream(symbol_program)
        fonts += f" /F2 {font_id} 0 R"

    for page_id, stream in zip(page_ids, pages, strict=True):
        content_id = page_id + 1
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << {fonts} >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = _stream(stream)

    output = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(output)
        output += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref_at = len(output)
    size = max(objects) + 1
    output += f"xref\n0 {size}\n".encode()
    output += b"0000000000 65535 f \n"
    for number in range(1, size):
        output += f"{offsets[number]:010d} 00000 n \n".encode()
    output += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(output)
