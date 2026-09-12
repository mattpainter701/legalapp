"""Render the executed copy: filled form values plus stamped signatures.

The executed PDF is what the firm files and the client keeps, so it must be
non-editable and must open in every viewer without depending on a form
engine. Form values are therefore drawn into the page content (reusing the
template flattener, which already knows how to paint each widget type), the
signature, initials and date stamps are drawn on a reportlab overlay merged
into each page, and every widget annotation and the AcroForm dictionary are
dropped from the result.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject


def _pdf_templates():
    """Imported on use: pdf_templates imports the esign package, so a
    module-level import here would be circular."""
    from app.services import pdf_templates

    return pdf_templates


ACROFORM_PREFIX = "acroform:"
CAPTION_MIN_HEIGHT = 26.0
CAPTION_FONT_SIZE = 5.0
STAMP_PADDING = 2.0
SIGNATURE_FONT = "Times-Italic"
DATE_FONT = "Helvetica"
_UNICODE_FONT = "LawHandSigningUnicode"


@dataclass(frozen=True)
class Stamp:
    """One mark to paint: a typed signature, initials or a date."""

    page: int  # one-based
    rect: tuple[float, float, float, float]
    kind: str  # signature | initials | date
    text: str
    caption: str | None = None


def _register_unicode_font() -> str:
    """A TrueType fallback for names the standard fonts cannot encode."""
    from reportlab import __file__ as reportlab_file
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _UNICODE_FONT not in pdfmetrics.getRegisteredFontNames():
        production_font = Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")
        font_path = (
            production_font
            if production_font.is_file()
            else Path(reportlab_file).parent / "fonts" / "Vera.ttf"
        )
        pdfmetrics.registerFont(TTFont(_UNICODE_FONT, str(font_path)))
    return _UNICODE_FONT


def _font_for(text: str, preferred: str) -> str:
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return _register_unicode_font()
    return preferred


def _fit_font_size(text: str, font: str, max_width: float, max_height: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    size = max(4.0, min(max_height, 36.0))
    while size > 4.0 and stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def _draw_stamp(overlay, stamp: Stamp) -> None:
    x0, y0, x1, y1 = stamp.rect
    left, bottom = min(x0, x1), min(y0, y1)
    width, height = abs(x1 - x0), abs(y1 - y0)
    if width <= 2 * STAMP_PADDING or height <= 2 * STAMP_PADDING:
        return
    inner_width = width - 2 * STAMP_PADDING
    text = stamp.text.strip()
    if stamp.kind == "date":
        font = _font_for(text, DATE_FONT)
        size = _fit_font_size(
            text, font, inner_width, min(height - 2 * STAMP_PADDING, 12)
        )
        overlay.setFillColorRGB(0, 0, 0)
        overlay.setFont(font, size)
        overlay.drawString(left + STAMP_PADDING, bottom + (height - size) / 2 + 1, text)
        return
    with_caption = bool(stamp.caption) and height >= CAPTION_MIN_HEIGHT
    caption_band = CAPTION_FONT_SIZE + 3 if with_caption else 0.0
    baseline_y = bottom + STAMP_PADDING + caption_band + 1
    text_height = height - 2 * STAMP_PADDING - caption_band - 3
    font = _font_for(text, SIGNATURE_FONT)
    size = _fit_font_size(text, font, inner_width, text_height)
    overlay.setFillColorRGB(0.05, 0.05, 0.25)
    overlay.setFont(font, size)
    overlay.drawString(left + STAMP_PADDING, baseline_y + 2, text)
    overlay.setStrokeColorRGB(0.2, 0.2, 0.2)
    overlay.setLineWidth(0.5)
    overlay.line(
        left + STAMP_PADDING, baseline_y, left + width - STAMP_PADDING, baseline_y
    )
    if with_caption:
        caption = stamp.caption or ""
        overlay.setFillColorRGB(0.3, 0.3, 0.3)
        overlay.setFont(_font_for(caption, DATE_FONT), CAPTION_FONT_SIZE)
        overlay.drawString(left + STAMP_PADDING, bottom + STAMP_PADDING, caption)


def _acroform_values(field_values: dict[str, str]) -> dict[str, str]:
    return {
        key[len(ACROFORM_PREFIX) :]: "" if value is None else str(value)
        for key, value in (field_values or {}).items()
        if isinstance(key, str) and key.startswith(ACROFORM_PREFIX)
    }


def _strip_widgets(page) -> None:
    annotations = page.get("/Annots")
    if annotations is None:
        return
    resolve = _pdf_templates()._resolve
    retained = ArrayObject(
        ref for ref in resolve(annotations) if resolve(ref).get("/Subtype") != "/Widget"
    )
    if retained:
        page[NameObject("/Annots")] = retained
    else:
        page.pop(NameObject("/Annots"), None)


def render_executed_pdf(
    source: bytes,
    *,
    field_values: dict[str, str],
    stamps: list[Stamp],
) -> bytes:
    """Fill, stamp and flatten ``source``; the result has no form left in it."""
    from reportlab.pdfgen import canvas

    pdf = _pdf_templates()
    reader = pdf._open_pdf(source)
    values = _acroform_values(field_values)
    if pdf._widgets(reader):
        # Paint every widget's value into the page and drop the widgets; the
        # flattener rejects values that do not fit or cannot be displayed.
        flattened = pdf._flatten_with_overlays(reader, values)
        reader = PdfReader(io.BytesIO(flattened), strict=False)
    stamps_by_page: dict[int, list[Stamp]] = {}
    for stamp in stamps:
        if not 1 <= stamp.page <= len(reader.pages):
            raise pdf.TemplatePdfError(
                "A signature stamp targets a page the PDF lacks."
            )
        stamps_by_page.setdefault(stamp.page, []).append(stamp)

    writer = PdfWriter()
    for index, page in enumerate(reader.pages, start=1):
        page_stamps = stamps_by_page.get(index)
        if page_stamps:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            buffer = io.BytesIO()
            overlay = canvas.Canvas(buffer, pagesize=(width, height))
            for stamp in page_stamps:
                _draw_stamp(overlay, stamp)
            overlay.showPage()
            overlay.save()
            buffer.seek(0)
            page.merge_page(PdfReader(buffer).pages[0])
        _strip_widgets(page)
        writer.add_page(page)
    # add_page copies pages, never the catalog, so no /AcroForm survives; be
    # explicit anyway in case a future pypdf carries it across.
    writer._root_object.pop(NameObject("/AcroForm"), None)
    if reader.metadata:
        writer.add_metadata(
            {str(k): str(v) for k, v in reader.metadata.items() if v is not None}
        )
    output = io.BytesIO()
    writer.write(output)
    result = output.getvalue()
    pdf._open_pdf(result)
    return result
