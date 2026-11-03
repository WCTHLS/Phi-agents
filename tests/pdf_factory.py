"""Builds tiny valid PDFs for tests, so no real (and not-in-Git) regulatory documents are needed.

A page given as text gets a real text layer. A page given as ``None`` is blank, which behaves like a
scanned page: it shows nothing to extract.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str | None]) -> bytes:
    """Return the bytes of a PDF with one page per item."""
    if not pages:
        raise ValueError("a PDF needs at least one page")

    objects: dict[int, bytes] = {}
    page_ids = [4 + 2 * index for index in range(len(pages))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for page_id, text in zip(page_ids, pages, strict=True):
        content_id = page_id + 1
        if text is None:
            stream = b""
        else:
            lines = text.split("\n")
            body = "BT /F1 11 Tf 72 720 Td 14 TL\n"
            body += "\n".join(f"({_escape(line)}) Tj T*" for line in lines)
            body += "\nET"
            stream = body.encode("latin-1")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

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
