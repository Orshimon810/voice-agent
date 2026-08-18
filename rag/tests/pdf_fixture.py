"""Builds a minimal, valid single-page PDF from scratch (no PDF-writing
library) so extraction tests can exercise the real pypdf reading path
without adding a new dependency."""

from __future__ import annotations


def build_minimal_pdf(text: str) -> bytes:
    content_stream = f"BT /F1 18 Tf 20 150 Td ({_escape(text)}) Tj ET".encode("latin-1")

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(
        b"<< /Length "
        + str(len(content_stream)).encode("ascii")
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream"
    )

    header = b"%PDF-1.4\n"
    body_parts: list[bytes] = []
    offsets: list[int] = []
    cursor = len(header)
    for i, obj in enumerate(objects, start=1):
        offsets.append(cursor)
        part = f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        body_parts.append(part)
        cursor += len(part)

    body = b"".join(body_parts)
    xref_offset = len(header) + len(body)

    xref_lines = [f"0 {len(objects) + 1}", "0000000000 65535 f "]
    for off in offsets:
        xref_lines.append(f"{off:010d} 00000 n ")
    xref = "xref\n" + "\n".join(xref_lines) + "\n"

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    )

    return header + body + xref.encode("ascii") + trailer.encode("ascii")


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
