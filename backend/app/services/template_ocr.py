"""Local OCR for image-only PDF template intake.

The OCR result retains PDF-space coordinates so the template pipeline can
create reviewed fields without sending client documents to an external model.
Imports are intentionally lazy: normal DOCX and text-native PDF intake should
not pay the OCR model startup cost.
"""

from __future__ import annotations

import io
import threading
from contextlib import contextmanager
from io import BytesIO
import warnings
from dataclasses import dataclass
from typing import Any, Iterable

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
    page_indexes: tuple[int, ...] = ()

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
    return (
        overlap / height >= 0.35
        or abs((l_bottom + l_top) - (r_bottom + r_top)) <= height * 0.75
    )


def _is_label_fragment(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return False
    if normalized.endswith(":"):
        return True
    # OCR frequently drops the colon on printed labels. Keep this deliberately
    # conservative so ordinary prose is not merged with its neighbour.
    return normalized.lower() in {
        "name",
        "full name",
        "full legal name",
        "legal name",
        "first name",
        "middle name",
        "last name",
        "preferred name",
        "applicant",
        "applicant name",
        "client",
        "client name",
        "address",
        "mailing address",
        "street",
        "street address",
        "city",
        "state",
        "zip",
        "zip code",
        "postal code",
        "country",
        "county",
        "county of residence",
        "phone",
        "phone number",
        "telephone",
        "mobile",
        "email",
        "email address",
        "date",
        "date of birth",
        "dob",
        "date signed",
        "case",
        "case number",
        "case no",
        "file number",
        "file no",
        "matter",
        "court",
        "judge",
        "plaintiff",
        "defendant",
        "petitioner",
        "respondent",
        "opposing party",
        "spouse",
        "signature",
        "amount",
        "fee",
        "fee amount",
        "employer",
        "occupation",
    }


def reconstruct_ocr_lines(
    lines: list[OcrLine] | tuple[OcrLine, ...],
) -> tuple[OcrLine, ...]:
    """Join adjacent same-row label/value OCR fragments.

    OCR engines often return ``Applicant Name:`` and handwritten ``Ada`` as
    separate detections. This joined representation is used for text
    understanding only; ``PdfOcrResult.lines`` retains the original boxes for
    coordinate-sensitive replacement and redaction.
    """
    ordered = sorted(
        lines, key=lambda line: (line.page_index, -line.rect[3], line.rect[0])
    )
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
_ENGINE_POOL = None
_ENGINE_POOL_CONFIG: tuple[int, int] | None = None
_ENGINE_POOL_LOCK = threading.Lock()
_MAX_OCR_PAGES = 25
_MAX_RENDERED_PIXELS = 80_000_000
_MAX_PAGE_PIXELS = 10_000_000
_TARGET_SCALE = 2.25  # 162 DPI: readable scans without unbounded memory use.
_MIN_LINE_CONFIDENCE = 0.35
_MAX_IMAGE_SOURCE_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_SOURCE_PIXELS = 25_000_000
_MAX_IMAGE_INFERENCE_PIXELS = 10_000_000
_ALLOWED_IMAGE_FORMATS = {"BMP", "JPEG", "PNG", "TIFF", "WEBP"}
_MAX_IMAGE_FRAMES = 25
_MAX_IMAGE_PAGE_PIXELS = 30_000_000
_MAX_IMAGE_TOTAL_PIXELS = 80_000_000


@dataclass(frozen=True)
class NormalizedImagePdf:
    """A bounded, inert PDF produced from a standalone image upload."""

    content: bytes
    pages: int
    image_format: str


def _image_dpi(raw_dpi: Any) -> tuple[float, float]:
    try:
        x_dpi, y_dpi = raw_dpi
        x_dpi = float(x_dpi)
        y_dpi = float(y_dpi)
    except (TypeError, ValueError):
        return 150.0, 150.0
    if not (72 <= x_dpi <= 600 and 72 <= y_dpi <= 600):
        return 150.0, 150.0
    return x_dpi, y_dpi


def image_to_pdf(content: bytes) -> NormalizedImagePdf:
    """Validate a standalone image and rasterize it into a safe PDF.

    Template rendering already has a carefully reviewed PDF overlay path. A
    standalone scan is therefore normalized to an inert PDF instead of
    retaining animation, metadata, or format-specific payloads. Pixel and
    frame limits are enforced before OCR or persistence.
    """

    try:
        from PIL import Image, ImageOps
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise TemplateOcrError(
            "Image scanning is not installed on this server. Ask an administrator to enable the document OCR worker."
        ) from exc

    if not content:
        raise TemplateOcrError("The uploaded image is empty.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(content))
    except Exception as exc:
        raise TemplateOcrError(
            "The uploaded file is not a supported document image."
        ) from exc

    image_format = str(source.format or "").upper()
    if image_format not in _ALLOWED_IMAGE_FORMATS:
        source.close()
        raise TemplateOcrError(
            "Unsupported image format. Use PNG, JPEG, TIFF, BMP, or WebP."
        )

    frame_count = int(getattr(source, "n_frames", 1) or 1)
    if frame_count < 1 or frame_count > _MAX_IMAGE_FRAMES:
        source.close()
        raise TemplateOcrError(
            f"Image templates may contain at most {_MAX_IMAGE_FRAMES} pages. Split this file and try again."
        )

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pageCompression=1)
    total_pixels = 0
    try:
        for frame_index in range(frame_count):
            source.seek(frame_index)
            source_width, source_height = source.size
            pixels = int(source_width) * int(source_height)
            if (
                source_width <= 0
                or source_height <= 0
                or pixels > _MAX_IMAGE_PAGE_PIXELS
            ):
                raise TemplateOcrError(
                    "An image page is too large to process safely. Export it at 300 DPI or lower and try again."
                )
            total_pixels += pixels
            if total_pixels > _MAX_IMAGE_TOTAL_PIXELS:
                raise TemplateOcrError(
                    "The combined image pages are too large to process safely. Split the scan and try again."
                )

            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                frame = ImageOps.exif_transpose(source.copy())
                frame.load()
            try:
                width, height = frame.size
                if frame.mode in {"RGBA", "LA"} or "transparency" in frame.info:
                    rgba = frame.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                    rgba.close()
                else:
                    rgb = frame.convert("RGB")
                try:
                    x_dpi, y_dpi = _image_dpi(source.info.get("dpi"))
                    page_width = max(36.0, min(14_400.0, width * 72.0 / x_dpi))
                    page_height = max(36.0, min(14_400.0, height * 72.0 / y_dpi))
                    pdf.setPageSize((page_width, page_height))
                    pdf.drawImage(
                        ImageReader(rgb),
                        0,
                        0,
                        width=page_width,
                        height=page_height,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                    pdf.showPage()
                finally:
                    rgb.close()
            finally:
                frame.close()
        pdf.save()
    except TemplateOcrError:
        raise
    except Exception as exc:
        raise TemplateOcrError("The image could not be normalized for OCR.") from exc
    finally:
        source.close()

    return NormalizedImagePdf(
        content=output.getvalue(),
        pages=frame_count,
        image_format=image_format,
    )


def _new_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise TemplateOcrError(
            "OCR is not installed on this server. Ask an administrator to enable the document OCR worker."
        ) from exc
    try:
        return RapidOCR()
    except Exception as exc:  # pragma: no cover - runtime/model guard
        raise TemplateOcrError(
            "The OCR engine could not start. Try again or contact support."
        ) from exc


def _engine():
    """Return the first local engine for compatibility and pool seeding."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = _new_engine()
    return _ENGINE


class _OcrEnginePool:
    """Lazily create independent inference sessions up to a fixed bound."""

    def __init__(self, size: int, first_engine: Any):
        self.size = size
        self._available = [first_engine]
        self._created = 1
        self._condition = threading.Condition()

    def acquire(self):
        create = False
        with self._condition:
            while not self._available:
                if self._created < self.size:
                    self._created += 1
                    create = True
                    break
                self._condition.wait()
            if not create:
                return self._available.pop()
        try:
            return _new_engine()
        except Exception:
            with self._condition:
                self._created -= 1
                self._condition.notify()
            raise

    def release(self, engine: Any) -> None:
        with self._condition:
            self._available.append(engine)
            self._condition.notify()


def _engine_pool() -> _OcrEnginePool:
    global _ENGINE_POOL, _ENGINE_POOL_CONFIG
    concurrency = get_settings().TEMPLATE_OCR_LOCAL_CONCURRENCY
    # Include the seed identity so tests and controlled runtime resets that
    # replace _ENGINE cannot accidentally lease a stale model session.
    config = (concurrency, id(_ENGINE))
    if _ENGINE_POOL is not None and _ENGINE_POOL_CONFIG == config:
        return _ENGINE_POOL
    with _ENGINE_POOL_LOCK:
        seed = _engine()
        config = (concurrency, id(seed))
        if _ENGINE_POOL is None or _ENGINE_POOL_CONFIG != config:
            _ENGINE_POOL = _OcrEnginePool(concurrency, seed)
            _ENGINE_POOL_CONFIG = config
    return _ENGINE_POOL


@contextmanager
def _lease_engine():
    pool = _engine_pool()
    engine = pool.acquire()
    try:
        yield engine
    finally:
        pool.release(engine)


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


def ocr_pdf(
    content: bytes,
    *,
    max_pages: int = _MAX_OCR_PAGES,
    page_indexes: Iterable[int] | None = None,
) -> PdfOcrResult:
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
    page_limit = max(1, min(int(max_pages), _MAX_OCR_PAGES))
    if page_indexes is None:
        requested_pages = list(range(total_pages))
    else:
        requested_pages = sorted(
            {
                int(page_index)
                for page_index in page_indexes
                if 0 <= int(page_index) < total_pages
            }
        )
    selected_pages = requested_pages[:page_limit]
    remaining_pixels = _MAX_RENDERED_PIXELS
    lines: list[OcrLine] = []
    try:
        for page_index in selected_pages:
            page = document[page_index]
            image = None
            try:
                width, height = (float(value) for value in page.get_size())
                scale = _safe_scale(width, height, remaining_pixels)
                bitmap = page.render(scale=scale, rev_byteorder=True)
                try:
                    rendered_image = bitmap.to_pil()
                    try:
                        image = (
                            rendered_image.copy()
                            if rendered_image.mode == "RGB"
                            else rendered_image.convert("RGB")
                        )
                    finally:
                        rendered_image.close()
                finally:
                    bitmap.close()
                remaining_pixels -= image.width * image.height
                with _lease_engine() as engine:
                    result = engine(image)
            except TemplateOcrError:
                raise
            except Exception as exc:
                raise TemplateOcrError(
                    f"Page {page_index + 1} could not be read by OCR."
                ) from exc
            finally:
                if image is not None:
                    image.close()
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

    source_lines = tuple(
        sorted(lines, key=lambda line: (line.page_index, -line.rect[3], line.rect[0]))
    )
    reconstructed_lines = reconstruct_ocr_lines(source_lines)
    page_text: dict[int, list[str]] = {page_index: [] for page_index in selected_pages}
    for line in reconstructed_lines:
        page_text.setdefault(line.page_index, []).append(line.text)
    text = "\n\n".join(
        "\n".join(page_text[page_index])
        for page_index in selected_pages
        if page_text.get(page_index)
    ).strip()
    confidence = (
        sum(line.score for line in source_lines) / len(source_lines)
        if source_lines
        else 0.0
    )
    return PdfOcrResult(
        text=text,
        # Preserve exact OCR fragments for coordinate-sensitive overlays. The
        # reconstructed text above is intentionally separate from these boxes.
        lines=source_lines,
        pages_analyzed=len(selected_pages),
        pages_total=total_pages,
        average_confidence=round(confidence, 4),
        truncated=len(requested_pages) > len(selected_pages),
        provider="local",
        page_indexes=tuple(selected_pages),
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
        with _lease_engine() as engine:
            result = engine(image)
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
