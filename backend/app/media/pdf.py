"""PDF documents, in memory only (pypdfium2, the PDF engine Chrome uses).

A PDF is a document like a photo. Pages with a real text layer (most lab reports and digital
bills) give their text as is: exact, instant, no model call. Scanned pages have no text, so
each is rendered to a JPEG in memory and read by the model like a photo. Nothing is written to
disk.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pypdfium2 as pdfium

MAX_PAGES = 10  # per PDF (decided 2026-10-12)
RENDER_SCALE = 2.0  # 144 dpi: an A4 page is about 1190 x 1684 pixels
MIN_TEXT_CHARS = 40  # fewer letters than this on a page: treat it as a scan


class PdfError(ValueError):
    """The file is not a PDF we can read (damaged, password-protected, too many pages)."""


@dataclass
class Page:
    number: int  # from 1
    text: str | None  # the page's own text layer, if it has one
    jpeg: bytes | None  # the rendered page, when there is no usable text layer


def _open(data: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise PdfError("This PDF could not be opened (damaged or password-protected)") from exc


def page_count(data: bytes) -> int:
    """Pages in the PDF; raises PdfError if it can't be opened or has more than MAX_PAGES."""
    pdf = _open(data)
    try:
        n = len(pdf)
    finally:
        pdf.close()
    if n == 0:
        raise PdfError("This PDF has no pages")
    if n > MAX_PAGES:
        raise PdfError(f"This PDF has {n} pages; at most {MAX_PAGES} can be read")
    return n


def has_text(text: str) -> bool:
    return len(re.sub(r"\W", "", text)) >= MIN_TEXT_CHARS


def pages(data: bytes) -> list[Page]:
    """Each page as its text layer, or as a rendered image when it has none."""
    page_count(data)
    pdf = _open(data)
    out: list[Page] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().replace("\r\n", "\n").strip()
                textpage.close()
                if has_text(text):
                    out.append(Page(i + 1, text, None))
                    continue
                image = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=90)
                out.append(Page(i + 1, None, buf.getvalue()))
            finally:
                page.close()
    finally:
        pdf.close()
    return out
