"""Opt-in Azure Document Intelligence Read adapter."""

from __future__ import annotations

import io
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.services.template_ocr import OcrLine, PdfOcrResult, TemplateOcrError


def _location_url(raw: str, endpoint: str) -> str:
    location = urlsplit(raw)
    base = urlsplit(endpoint)
    if (
        location.scheme != "https"
        or location.netloc.lower() != base.netloc.lower()
        or location.username
        or location.password
        or location.fragment
    ):
        raise TemplateOcrError("Azure OCR returned an invalid operation location.")
    return raw


def ocr_pdf_azure(content: bytes, *, max_pages: int = 25) -> PdfOcrResult:
    settings = get_settings()
    endpoint = settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT.rstrip("/")
    api_version = settings.AZURE_DOCUMENT_INTELLIGENCE_API_VERSION
    url = f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
    params = {"api-version": api_version, "pages": f"1-{max(1, min(max_pages, 25))}"}
    headers = {
        "Ocp-Apim-Subscription-Key": settings.AZURE_DOCUMENT_INTELLIGENCE_KEY,
        "Content-Type": "application/pdf",
    }
    timeout = httpx.Timeout(settings.TEMPLATE_OCR_AZURE_TIMEOUT_SECONDS)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, params=params, headers=headers, content=content)
            if response.status_code != 202:
                raise TemplateOcrError(
                    "Azure OCR could not start. Try again or use local OCR."
                )
            operation = response.headers.get("Operation-Location")
            if not operation:
                raise TemplateOcrError("Azure OCR returned no operation location.")
            operation = _location_url(operation, endpoint)
            deadline = time.monotonic() + settings.TEMPLATE_OCR_AZURE_MAX_POLL_SECONDS
            while time.monotonic() < deadline:
                result = client.get(
                    operation,
                    headers={
                        "Ocp-Apim-Subscription-Key": (
                            settings.AZURE_DOCUMENT_INTELLIGENCE_KEY
                        )
                    },
                )
                if result.status_code >= 400:
                    raise TemplateOcrError(
                        "Azure OCR failed while retrieving its result."
                    )
                payload = result.json()
                status = str(payload.get("status") or "").lower()
                if status == "succeeded":
                    parsed_result = _result(payload)
                    try:
                        from pypdf import PdfReader

                        total = len(PdfReader(io.BytesIO(content)).pages)
                    except Exception:
                        total = parsed_result.pages_total
                    return PdfOcrResult(
                        text=parsed_result.text,
                        lines=parsed_result.lines,
                        pages_analyzed=parsed_result.pages_analyzed,
                        pages_total=total,
                        average_confidence=parsed_result.average_confidence,
                        truncated=total > parsed_result.pages_analyzed,
                        provider=parsed_result.provider,
                    )
                if status in {"failed", "canceled"}:
                    raise TemplateOcrError("Azure OCR could not read this document.")
                retry_after = result.headers.get("Retry-After", "2")
                try:
                    delay = max(
                        1.0,
                        min(
                            float(retry_after),
                            settings.TEMPLATE_OCR_AZURE_MAX_POLL_INTERVAL_SECONDS,
                        ),
                    )
                except ValueError:
                    delay = 2.0
                time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
    except TemplateOcrError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise TemplateOcrError("Azure OCR is temporarily unavailable.") from exc
    raise TemplateOcrError("Azure OCR timed out. Try again or use local OCR.")


def _spans(value: dict[str, Any]) -> list[tuple[int, int]]:
    raw_spans = value.get("spans")
    if not isinstance(raw_spans, list):
        raw_span = value.get("span")
        raw_spans = [raw_span] if isinstance(raw_span, dict) else []
    spans: list[tuple[int, int]] = []
    for span in raw_spans:
        if not isinstance(span, dict):
            continue
        try:
            offset = int(span.get("offset"))
            length = int(span.get("length"))
        except (TypeError, ValueError):
            continue
        if offset >= 0 and length > 0:
            spans.append((offset, offset + length))
    return spans


def _line_confidence(line: dict[str, Any], page_words: list[dict[str, Any]]) -> float:
    line_spans = _spans(line)
    scores: list[float] = []
    nested_words = line.get("words")
    candidate_words = (
        nested_words
        if isinstance(nested_words, list)
        else page_words
        if line_spans
        else []
    )
    for word in candidate_words:
        if not isinstance(word, dict) or word.get("confidence") is None:
            continue
        word_spans = _spans(word)
        if line_spans and not any(
            max(line_start, word_start) < min(line_end, word_end)
            for line_start, line_end in line_spans
            for word_start, word_end in word_spans
        ):
            continue
        try:
            scores.append(float(word["confidence"]))
        except (TypeError, ValueError):
            continue
    if scores:
        return max(0.0, min(1.0, sum(scores) / len(scores)))
    try:
        return max(0.0, min(1.0, float(line.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _result(payload: dict[str, Any]) -> PdfOcrResult:
    lines: list[OcrLine] = []
    analyze_result = payload.get("analyzeResult")
    pages = analyze_result.get("pages", []) if isinstance(analyze_result, dict) else []
    if not isinstance(pages, list):
        pages = []
    for fallback_page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        try:
            page_index = max(0, int(page.get("pageNumber")) - 1)
        except (TypeError, ValueError):
            page_index = fallback_page_index
        unit = str(page.get("unit") or "inch").strip().lower()
        if unit in {"inch", "in", "inches"}:
            multiplier = 72.0
        elif unit in {"point", "points", "pt"}:
            multiplier = 1.0
        else:
            # Pixel coordinates cannot be mapped to PDF points without the
            # source raster dimensions; fail closed rather than misplacing
            # handwriting on a generated template.
            continue
        try:
            width = float(page.get("width") or 0) * multiplier
            height = float(page.get("height") or 0) * multiplier
        except (TypeError, ValueError):
            continue
        page_words = page.get("words") if isinstance(page.get("words"), list) else []
        page_lines = page.get("lines") if isinstance(page.get("lines"), list) else []
        for line in page_lines:
            if not isinstance(line, dict):
                continue
            content = " ".join(str(line.get("content") or "").split()).strip()
            if not content:
                continue
            polygon = line.get("polygon") or line.get("boundingPolygon") or []
            if len(polygon) < 8 or width <= 0 or height <= 0:
                continue
            try:
                points = [
                    (float(polygon[i]), float(polygon[i + 1]))
                    for i in range(0, len(polygon) - 1, 2)
                ]
            except (TypeError, ValueError):
                continue
            xs, ys = zip(*points)
            left = max(0.0, min(width, min(xs) * multiplier))
            right = max(0.0, min(width, max(xs) * multiplier))
            bottom = max(0.0, min(height, height - max(ys) * multiplier))
            top = max(0.0, min(height, height - min(ys) * multiplier))
            if right <= left or top <= bottom:
                continue
            lines.append(
                OcrLine(
                    page_index=page_index,
                    text=content,
                    score=_line_confidence(line, page_words),
                    rect=(left, bottom, right, top),
                )
            )
    ordered = tuple(
        sorted(lines, key=lambda line: (line.page_index, -line.rect[3], line.rect[0]))
    )
    confidence = sum(line.score for line in ordered) / len(ordered) if ordered else 0.0
    return PdfOcrResult(
        text="\n".join(line.text for line in ordered),
        lines=ordered,
        pages_analyzed=len(pages),
        pages_total=len(pages),
        average_confidence=round(confidence, 4),
        truncated=False,
        provider="azure",
    )
