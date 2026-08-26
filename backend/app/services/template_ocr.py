"""Local OCR for image-only PDF template intake.

The OCR result retains PDF-space coordinates so the template pipeline can
create reviewed fields without sending client documents to an external model.
Imports are intentionally lazy: normal DOCX and text-native PDF intake should
not pay the OCR model startup cost.
"""

from __future__ import annotations

import threading
from io import BytesIO
from dataclasses import dataclass
from typing import Any

from app.config import get_settings


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
    provider: str = "local"

    def fragments(self) -> list[dict[str, Any]]:
        return [line.as_pdf_fragment() for line in self.lines]


@dataclass(frozen=True)
class ImageOcrResult:
    """Text recovered from one bounded receipt/photo image."""

    text: str
    average_confidence: float
    lines_detected: int

def _same_row(left: OcrLine, right: OcrLine) -> bool:
    """Return whether two OCR boxes plausibly belong to one visual row."""
    if left.page_index != right.page_index:
        return False
    l_bottom, l_top = left.rect[1], left.rect[3]
    r_bottom, r_top = right.rect[1], right.rect[3]
    overlap = max(0.0, min(l_top, r_top) - max(l_bottom, r_bottom))
    height = max(1.0, min(l_top - l_bottom, r_top - r_bottom))
    return overlap / height >= 0.35 or abs((l_bottom + l_top) - (r_bottom + r_top)) <= height * 0.75


def _is_label_fragment(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return False
    if normalized.endswith(":"):
        return True
    # OCR frequently drops the colon on printed labels. Keep this deliberately
    # conservative so ordinary prose is not merged with its neighbour.
    return normalized.lower() in {
        "name", "full name", "full legal name", "legal name", "first name",
        "middle name", "last name", "preferred name", "applicant",
        "applicant name", "client", "client name", "address",
        "mailing address", "street", "street address", "city", "state",
        "zip", "zip code", "postal code", "country", "county",
        "county of residence", "phone", "phone number", "telephone",
        "mobile", "email", "email address", "date", "date of birth", "dob",
        "date signed", "case", "case number", "case no", "file number",
        "file no", "matter", "court", "judge", "plaintiff", "defendant",
        "petitioner", "respondent", "opposing party", "spouse", "signature",
        "amount", "fee", "fee amount", "employer", "occupation",
    }


def reconstruct_ocr_lines(lines: list[OcrLine] | tuple[OcrLine, ...]) -> tuple[OcrLine, ...]:
    """Join adjacent same-row label/value OCR fragments.

    OCR engines often return ``Applicant Name:`` and handwritten ``Ada`` as
    separate detections. This joined representation is used for text
    understanding only; ``PdfOcrResult.lines`` retains the original boxes for
    coordinate-sensitive replacement and redaction.
    """
    ordered = sorted(lines, key=lambda line: (line.page_index, -line.rect[3], line.rect[0]))
    output: list[OcrLine] = []
    index = 0
    while index < len(ordered):
        current = ordered[index]
        if index + 1 < len(ordered):
            candidate = ordered[index + 1]
            gap = candidate.rect[0] - current.rect[2]
            height = max(1.0, current.rect[3] - current.rect[1])
            if (
                _is_label_fragment(current.text)
                and not _is_label_fragment(candidate.text)
                and candidate.rect[0] >= current.rect[2]
                and gap <= max(36.0, height * 12.0)
                and _same_row(current, candidate)
            ):
                text = f"{current.text.rstrip(':').strip()}: {candidate.text.strip()}"
                output.append(
                    OcrLine(
                        page_index=current.page_index,
                        text=text,
                        score=min(current.score, candidate.score),
                        rect=(
                            min(current.rect[0], candidate.rect[0]),
                            min(current.rect[1], candidate.rect[1]),
                            max(current.rect[2], candidate.rect[2]),
                            max(current.rect[3], candidate.rect[3]),
                        ),
                    )
                )
                index += 2
                continue
        output.append(current)
        index += 1
    return tuple(output)


def reconstruct_ocr_text(lines: list[OcrLine] | tuple[OcrLine, ...]) -> str:
    """Build reading-order text from OCR fragments, joining form rows."""
    joined = reconstruct_ocr_lines(lines)
    pages: dict[int, list[str]] = {}
    for line in joined:
        pages.setdefault(line.page_index, []).append(line.text)
    return "\n\n".join("\n".join(values) for _, values in sorted(pages.items())).strip()


_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_MAX_OCR_PAGES = 25
_MAX_RENDERED_PIXELS = 80_000_000
_MAX_PAGE_PIXELS = 10_000_000
_TARGET_SCALE = 2.25  # 162 DPI: readable scans without unbounded memory use.
_MIN_LINE_CONFIDENCE = 0.35
_MAX_IMAGE_SOURCE_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_SOURCE_PIXELS = 25_000_000
_MAX_IMAGE_INFERENCE_PIXELS = 10_000_000


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

    # Cloud OCR is deliberately opt-in; local RapidOCR remains the default.
    if get_settings().TEMPLATE_OCR_PROVIDER.strip().lower() == "azure":
        from app.services.template_ocr_azure import ocr_pdf_azure
        return ocr_pdf_azure(content, max_pages=max_pages)

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
            lines.extend(page_lines)
    finally:
        document.close()

    source_lines = tuple(sorted(lines, key=lambda line: (line.page_index, -line.rect[3], line.rect[0])))
    reconstructed_lines = reconstruct_ocr_lines(source_lines)
    page_text: list[list[str]] = [[] for _ in range(page_limit)]
    for line in reconstructed_lines:
        page_text[line.page_index].append(line.text)
    text = "\n\n".join("\n".join(values) for values in page_text if values).strip()
    confidence = sum(line.score for line in source_lines) / len(source_lines) if source_lines else 0.0
    return PdfOcrResult(
        text=text,
        # Preserve exact OCR fragments for coordinate-sensitive overlays. The
        # reconstructed text above is intentionally separate from these boxes.
        lines=source_lines,
        pages_analyzed=page_limit,
        pages_total=total_pages,
        average_confidence=round(confidence, 4),
        truncated=total_pages > page_limit,
        provider="local",
    )


def ocr_image(content: bytes) -> ImageOcrResult:
    """OCR a JPEG/PNG receipt without unbounded image allocation.

    Dimensions are checked from the image header before pixel data is loaded,
    then large-but-valid photos are downsampled for inference.  The original
    bytes remain the audit attachment; only the OCR working copy is resized.
    """

    if not content:
        raise TemplateOcrError("The receipt image is empty.")
    if len(content) > _MAX_IMAGE_SOURCE_BYTES:
        raise TemplateOcrError("The receipt image exceeds the 10 MB OCR limit.")
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise TemplateOcrError(
            "Image scanning is not installed on this server."
        ) from exc

    try:
        with Image.open(BytesIO(content)) as source:
            width, height = (int(value) for value in source.size)
            if width <= 0 or height <= 0:
                raise TemplateOcrError("The receipt image has invalid dimensions.")
            source_pixels = width * height
            if source_pixels > _MAX_IMAGE_SOURCE_PIXELS:
                raise TemplateOcrError(
                    "This receipt image is too large to scan safely. Resize it and try again."
                )
            image = source.convert("RGB")
            if source_pixels > _MAX_IMAGE_INFERENCE_PIXELS:
                scale = (_MAX_IMAGE_INFERENCE_PIXELS / source_pixels) ** 0.5
                target = (max(1, int(width * scale)), max(1, int(height * scale)))
                image = image.resize(target, Image.Resampling.LANCZOS)
    except TemplateOcrError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise TemplateOcrError(
            "The receipt image could not be opened for OCR."
        ) from exc

    try:
        with _INFERENCE_LOCK:
            result = _engine()(image)
    except TemplateOcrError:
        raise
    except Exception as exc:  # pragma: no cover - engine/runtime guard
        raise TemplateOcrError("The receipt image could not be read by OCR.") from exc

    texts = tuple(getattr(result, "txts", None) or ())
    scores = tuple(getattr(result, "scores", None) or ())
    retained: list[tuple[str, float]] = []
    for raw_text, raw_score in zip(texts, scores):
        text = " ".join(str(raw_text or "").split()).strip()
        score = max(0.0, min(1.0, float(raw_score or 0.0)))
        if text and score >= _MIN_LINE_CONFIDENCE:
            retained.append((text, score))
    confidence = (
        sum(score for _, score in retained) / len(retained) if retained else 0.0
    )
    return ImageOcrResult(
        text="\n".join(text for text, _ in retained),
        average_confidence=round(confidence, 4),
        lines_detected=len(retained),
    )
