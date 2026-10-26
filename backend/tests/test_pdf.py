import io

import pytest
from PIL import Image

from app.media import pdf


def text_pdf(pages: list[list[str]]) -> bytes:
    """A minimal digital PDF: each page's lines as real text (Helvetica), like a lab report."""
    objects: list[bytes] = []
    kids = []
    for i, lines in enumerate(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_no} 0 R")
        body = "BT /F1 12 Tf 50 780 Td 16 TL " + " ".join(
            "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '"
            for ln in lines) + " ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_no} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        objects.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream".encode())
    head = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(head + objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{o:010d} 00000 n \n" for o in offsets).encode())
    out.write(f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
              .encode())
    return out.getvalue()


def scanned_pdf(n: int = 1) -> bytes:
    """A PDF of page images with no text layer, like a scanned or photographed document."""
    images = [Image.new("RGB", (400, 560), "white") for _ in range(n)]
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


REPORT = ["Synthetic Diagnostics Lab", "Patient: Asha Verma   Date: 12/10/2026",
          "Haemoglobin 11.2 g/dL (12.0 - 15.0)", "Platelets 2.1 lakh/cumm"]


def test_text_pages_are_used_as_they_are():
    (page,) = pdf.pages(text_pdf([REPORT]))
    assert page.jpeg is None
    assert "Haemoglobin 11.2 g/dL" in page.text and "Platelets" in page.text


def test_scanned_pages_are_rendered_for_the_model():
    pages = pdf.pages(scanned_pdf(2))
    assert [p.number for p in pages] == [1, 2]
    assert all(p.text is None and p.jpeg[:2] == b"\xff\xd8" for p in pages)  # JPEG


def test_mixed_pdf_reads_each_page_its_own_way():
    data = text_pdf([REPORT, ["x"]])  # page 2 has almost no text: treated as a scan
    first, second = pdf.pages(data)
    assert first.text and second.jpeg


def test_page_limit_and_broken_files():
    assert pdf.page_count(text_pdf([REPORT] * pdf.MAX_PAGES)) == pdf.MAX_PAGES
    with pytest.raises(pdf.PdfError, match="11 pages"):
        pdf.page_count(text_pdf([REPORT] * (pdf.MAX_PAGES + 1)))
    with pytest.raises(pdf.PdfError, match="could not be opened"):
        pdf.page_count(b"%PDF-1.4 not really")
