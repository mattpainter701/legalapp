"""Local OCR for image-only PDF template intake.

The OCR result retains PDF-space coordinates so the template pipeline can
create reviewed fields without sending client documents to an external model.
Imports are intentionally lazy: normal DOCX and text-native PDF intake should
not pay the OCR model startup cost.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


class TemplateOcrError(ValueError):
    """A customer-actionable OCR failure."""


@dataclass(frozen=True)
class OcrLine:
    page_index: int
    text: str
    score: float
    rect: tuple[float, float, float, float]

    def as_pdf_fragment(self) -> dict[str, Any]:
        left, bottom, right, top = self.rect
        height = max(8.0, top - bottom)
        return {
            "page_index": self.page_index,
            "text": self.text,
            "x": left,
            "y": bottom + max(1.0, height * 0.18),
            "font_size": max(6.0, min(24.0, height * 0.78)),
            "text_width": max(1.0, right - left),
            "source_kind": "ocr",
            "ocr_score": self.score,
        }


@dataclass(frozen=True)
class PdfOcrResult:
    text: str
    lines: tuple[OcrLine, ...]
    pages_analyzed: int
    pages_total: int
    average_confidence: float
    truncated: bool

    def fragments(self) -> list[dict[str, Any]]:
        return [line.as_pdf_fragment() for line in self.lines]


_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_MAX_OCR_PAGES = 25
_MAX_RENDERED_PIXELS = 80_000_000
_MAX_PAGE_PIXELS = 10_000_000
_TARGET_SCALE = 2.25  # 162 DPI: readable scans without unbounded memory use.
_MIN_LINE_CONFIDENCE = 0.35


def _engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise TemplateOcrError(
                "OCR is not installed on this server. Ask an administrator to enable the document OCR worker."
            ) from exc
        try:
            _ENGINE = RapidOCR()
        except Exception as exc:  # pragma: no cover - runtime/model guard
            raise TemplateOcrError(
                "The OCR engine could not start. Try again or contact support."
            ) from exc
    return _ENGINE


def _safe_scale(width: float, height: float, remaining_pixels: int) -> float:
    if width <= 0 or height <= 0:
        raise TemplateOcrError("A PDF page has an invalid size and cannot be scanned.")
    scale = _TARGET_SCALE
    page_pixels = width * height * scale * scale
    allowed = min(_MAX_PAGE_PIXELS, max(1, remaining_pixels))
    if page_pixels > allowed:
        scale *= (allowed / page_pixels) ** 0.5
    if scale < 1.0:
        raise TemplateOcrError(
            "This PDF is too large to scan safely. Split it into smaller documents and try again."
        )
    return scale


def ocr_pdf(content: bytes, *, max_pages: int = _MAX_OCR_PAGES) -> PdfOcrResult:
    """OCR a PDF into reading-order text and PDF-coordinate line boxes."""

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise TemplateOcrError(
            "PDF scanning is not installed on this server. Ask an administrator to enable the document OCR worker."
        ) from exc

    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        raise TemplateOcrError("The PDF could not be opened for OCR.") from exc

    total_pages = len(document)
    page_limit = min(total_pages, max(1, min(int(max_pages), _MAX_OCR_PAGES)))
    remaining_pixels = _MAX_RENDERED_PIXELS
    lines: list[OcrLine] = []
    engine = _engine()

    try:
        for page_index in range(page_limit):
            page = document[page_index]
            try:
                width, height = (float(value) for value in page.get_size())
                scale = _safe_scale(width, height, remaining_pixels)
                bitmap = page.render(scale=scale, rev_byteorder=True)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                remaining_pixels -= image.width * image.height
                with _INFERENCE_LOCK:
                    result = engine(image)
            except TemplateOcrError:
                raise
            except Exception as exc:
                raise TemplateOcrError(
                    f"Page {page_index + 1} could not be read by OCR."
                ) from exc
            finally:
                page.close()

            boxes = getattr(result, "boxes", None)
            texts = tuple(getattr(result, "txts", None) or ())
            scores = tuple(getattr(result, "scores", None) or ())
            if boxes is None:
                continue
            page_lines: list[OcrLine] = []
            for box, raw_text, raw_score in zip(boxes, texts, scores):
                text = " ".join(str(raw_text or "").split()).strip()
                score = float(raw_score or 0.0)
                if not text or score < _MIN_LINE_CONFIDENCE:
                    continue
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                left = max(0.0, min(xs) / scale)
                right = min(width, max(xs) / scale)
                top = min(height, height - min(ys) / scale)
                bottom = max(0.0, height - max(ys) / scale)
                if right - left < 1 or top - bottom < 1:
                    continue
                page_lines.append(
                    OcrLine(
                        page_index=page_index,
                        text=text,
                        score=max(0.0, min(1.0, score)),
                        rect=(left, bottom, right, top),
                    )
                )
            page_lines.sort(key=lambda line: (-line.rect[3], line.rect[0]))
            lines.extend(page_lines)
    finally:
        document.close()

    page_text: list[list[str]] = [[] for _ in range(page_limit)]
    for line in lines:
        page_text[line.page_index].append(line.text)
    text = "\n\n".join("\n".join(values) for values in page_text if values).strip()
    confidence = (
        sum(line.score for line in lines) / len(lines) if lines else 0.0
    )
    return PdfOcrResult(
        text=text,
        lines=tuple(lines),
        pages_analyzed=page_limit,
        pages_total=total_pages,
        average_confidence=round(confidence, 4),
        truncated=total_pages > page_limit,
    )
