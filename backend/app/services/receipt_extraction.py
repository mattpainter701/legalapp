"""Bounded, deterministic receipt extraction from quarantined inbound email."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from email.message import Message
from pathlib import PurePath
from typing import Any

from app.utils.text_processing import extract_text

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARS = 50_000

_ALLOWED = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
_DATE_RE = re.compile(
    r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]20\d{2})\b"
)
_NAMED_DATE_RE = re.compile(
    r"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2})\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"(?i)\b(?:grand\s+total|amount\s+due|balance\s+due|total)\s*"
    r"[:#-]?\s*\$?\s*([0-9][0-9,]*(?:\.\d{2})?)"
)
_TAX_RE = re.compile(
    r"(?i)\b(?:sales\s+tax|tax)\s*[:#-]?\s*\$?\s*" r"([0-9][0-9,]*(?:\.\d{2})?)"
)
_INVOICE_RE = re.compile(
    r"(?i)\b(?:invoice|receipt)\s*"
    r"(?:(?:number|no\.?|#)\s*[:#-]?|[:#-])\s*"
    r"([A-Z0-9][A-Z0-9/_-]{2,})"
)
_VENDOR_RE = re.compile(
    r"(?im)^(?:vendor|merchant|supplier|payee)\s*[:#-]\s*(.{2,120})\s*$"
)


class ReceiptExtractionError(ValueError):
    """A receipt cannot safely be accepted for extraction."""


def _safe_filename(part: Message, index: int) -> str:
    filename = part.get_filename() or f"receipt-{index + 1}"
    filename = PurePath(str(filename).replace("\\", "/")).name
    return filename[:240] or f"receipt-{index + 1}"


def _content_type(part: Message, filename: str) -> str | None:
    content_type = (part.get_content_type() or "").lower()
    if content_type in _ALLOWED:
        return content_type
    suffix = PurePath(filename).suffix.lower()
    return next((mime for mime, ext in _ALLOWED.items() if ext == suffix), None)


def iter_supported_attachments(
    raw_message: bytes, attachment_index: int | None = None
) -> list[dict[str, Any]]:
    """Parse only bounded attachments and return metadata plus bytes."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    candidates: list[dict[str, Any]] = []
    attachment_count = 0
    total = 0
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() != "attachment":
            continue
        attachment_count += 1
        if attachment_count > MAX_ATTACHMENTS:
            raise ReceiptExtractionError("Email contains too many attachments")
        filename = _safe_filename(part, attachment_count - 1)
        payload = part.get_payload(decode=True) or b""
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise ReceiptExtractionError("Receipt attachment exceeds the 10 MB limit")
        total += len(payload)
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ReceiptExtractionError(
                "Receipt attachments exceed the 20 MB total limit"
            )
        content_type = _content_type(part, filename)
        if not content_type:
            continue
        candidates.append(
            {
                "index": len(candidates),
                "filename": filename,
                "content_type": content_type,
                "content": payload,
            }
        )
        if len(candidates) > MAX_ATTACHMENTS:
            raise ReceiptExtractionError(
                "Email contains too many supported receipt attachments"
            )
    if attachment_index is not None:
        if attachment_index < 0 or attachment_index >= len(candidates):
            raise ReceiptExtractionError(
                "attachment_index does not identify a supported attachment"
            )
        return [candidates[attachment_index]]
    if len(candidates) > 1:
        raise ReceiptExtractionError(
            "Email contains multiple receipt attachments. Forward one receipt or invoice per email."
        )
    return candidates


def _parse_date(value: str) -> str | None:
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _first_date(value: str) -> str | None:
    matches = [match.group(1) for match in _DATE_RE.finditer(value)]
    matches.extend(match.group(1) for match in _NAMED_DATE_RE.finditer(value))
    return next(
        (parsed for candidate in matches if (parsed := _parse_date(candidate))),
        None,
    )


def extract_candidates(text: str, body: str | None = None) -> dict[str, Any]:
    """Extract conservative candidates; absent values stay absent for review."""
    source = "\n".join(x for x in (body or "", text) if x)[:MAX_EXTRACTED_CHARS]
    vendor = next((m.group(1).strip() for m in _VENDOR_RE.finditer(source)), None)
    if vendor is None:
        # Most point-of-sale receipts put the merchant name on the first line
        # without a label.  Preserve it only as a review candidate and avoid
        # obvious headings, dates, addresses and totals.
        for line in (" ".join(value.split()) for value in text.splitlines()[:8]):
            lowered_line = line.casefold()
            if (
                2 <= len(line) <= 100
                and any(character.isalpha() for character in line)
                and not any(
                    token in lowered_line
                    for token in (
                        "receipt",
                        "invoice",
                        "total",
                        "amount due",
                        "thank you",
                        "www.",
                        "http",
                        "date:",
                        "cashier",
                        "subtotal",
                        "tax",
                    )
                )
                and not _DATE_RE.search(line)
                and not re.search(r"\b\d{5}(?:-\d{4})?\b", line)
            ):
                vendor = line
                break
    total_match = list(_MONEY_RE.finditer(source))
    total = None
    if total_match:
        try:
            total = str(Decimal(total_match[-1].group(1).replace(",", "")))
        except InvalidOperation:
            pass
    tax_amount = None
    tax_matches = list(_TAX_RE.finditer(source))
    if tax_matches:
        try:
            tax_amount = str(Decimal(tax_matches[-1].group(1).replace(",", "")))
        except InvalidOperation:
            pass
    invoice_match = _INVOICE_RE.search(source)
    source_lines = source.splitlines()
    due_date = next(
        (
            parsed
            for line in source_lines
            if re.search(r"(?i)\b(?:due\s+date|payment\s+due)\b", line)
            if (parsed := _first_date(line))
        ),
        None,
    )
    date_value = next(
        (
            parsed
            for line in source_lines
            if re.search(
                r"(?i)\b(?:invoice|receipt|transaction|purchase)?\s*date\s*:",
                line,
            )
            and "due date" not in line.casefold()
            if (parsed := _first_date(line))
        ),
        None,
    )
    if date_value is None:
        date_value = _first_date(
            "\n".join(
                line
                for line in source_lines
                if not re.search(r"(?i)\b(?:due\s+date|payment\s+due)\b", line)
            )
        )
    lowered = source.lower()
    categories = {
        "court filing": ("filing fee", "court fee", "court services", "clerk of court"),
        "process service": ("process server", "service of process", "legal serving"),
        "certified mail": ("certified mail", "return receipt"),
        "investigator": ("investigator", "investigation services"),
        "expert/consultant": ("expert witness", "consulting fee", "consultant"),
        "records retrieval": ("records retrieval", "medical records", "record copy"),
        "research/database": ("westlaw", "lexisnexis", "legal research"),
        "copies/printing": ("copy service", "printing", "photocopy"),
        "postage/courier": ("courier", "delivery", "shipping", "postage"),
        "lodging": ("lodging", "hotel", "motel"),
        "travel/mileage/parking": ("airfare", "mileage", "parking", "rental car"),
        "meals": ("restaurant", "cafe", "lunch", "dinner"),
        "interpreter/translation": ("interpreter", "translation"),
    }
    category = next(
        (
            key
            for key, words in categories.items()
            if any(word in lowered for word in words)
        ),
        "other",
    )
    values = {
        "vendor": vendor,
        "date": date_value,
        "due_date": due_date,
        "total": total,
        "tax_amount": tax_amount,
        "invoice_number": invoice_match.group(1) if invoice_match else None,
        "category": category,
    }
    confidence_fields = (vendor, date_value, total, values["invoice_number"], category)
    present = sum(value is not None for value in confidence_fields)
    return {
        "values": values,
        "confidence": round(present / len(confidence_fields), 2),
        "text_available": bool(text.strip()),
    }


def extract_attachment_text(
    attachment: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Return text/state/metadata; scanned PDFs use the existing bounded local OCR."""
    if attachment["content_type"] in {"image/jpeg", "image/png"}:
        try:
            from app.services.template_ocr import ocr_image

            result = ocr_image(attachment["content"])
            return (
                result.text[:MAX_EXTRACTED_CHARS],
                "extracted" if result.text.strip() else "needs_review",
                {
                    "ocr_used": True,
                    "ocr_confidence": result.average_confidence,
                    "ocr_lines": result.lines_detected,
                },
            )
        except Exception:
            return "", "needs_review", {"ocr_used": True, "ocr_confidence": None}
    try:
        text = extract_text(
            attachment["content"],
            attachment["content_type"],
            attachment["filename"],
            max_pdf_pages=20,
            max_pdf_chars=MAX_EXTRACTED_CHARS,
        )
    except Exception:
        text = ""
        if attachment["content_type"] != "application/pdf":
            return "", "needs_review", {"ocr_used": False, "ocr_confidence": None}
    if not text.strip() and attachment["content_type"] == "application/pdf":
        try:
            from app.services.template_ocr import ocr_pdf

            result = ocr_pdf(attachment["content"], max_pages=20)
            return (
                result.text[:MAX_EXTRACTED_CHARS],
                "extracted" if result.text.strip() else "needs_review",
                {
                    "ocr_used": True,
                    "ocr_confidence": result.average_confidence,
                    "ocr_pages": result.pages_analyzed,
                    "ocr_truncated": result.truncated,
                },
            )
        except Exception:
            return "", "needs_review", {"ocr_used": True, "ocr_confidence": None}
    return (
        text[:MAX_EXTRACTED_CHARS],
        "extracted" if text.strip() else "needs_review",
        {"ocr_used": False, "ocr_confidence": None},
    )


def attachment_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
