from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from app.services import receipt_extraction as receipt


def _email(*attachments: tuple[str, str, bytes]) -> bytes:
    message = EmailMessage()
    message["From"] = "vendor@example.com"
    message["To"] = "matter@example.com"
    message["Subject"] = "Receipt"
    message.set_content("Please reimburse this receipt.")
    for filename, content_type, content in attachments:
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(
            content, maintype=maintype, subtype=subtype, filename=filename
        )
    return message.as_bytes()


def test_supported_attachment_is_bounded_and_explicitly_selectable():
    raw = _email(("one.txt", "text/plain", b"one"), ("two.txt", "text/plain", b"two"))
    with pytest.raises(receipt.ReceiptExtractionError, match="one receipt"):
        receipt.iter_supported_attachments(raw)
    assert receipt.iter_supported_attachments(raw, 0)[0]["filename"] == "one.txt"
    assert receipt.iter_supported_attachments(raw, 1)[0]["filename"] == "two.txt"
    with pytest.raises(receipt.ReceiptExtractionError):
        receipt.iter_supported_attachments(raw, 2)


def test_attachment_size_limit_is_fail_closed():
    raw = _email(
        ("receipt.txt", "text/plain", b"x" * (receipt.MAX_ATTACHMENT_BYTES + 1))
    )
    with pytest.raises(receipt.ReceiptExtractionError, match="10 MB"):
        receipt.iter_supported_attachments(raw)


def test_attachment_parser_skips_unsupported_and_enforces_attachment_count(monkeypatch):
    raw = _email(
        ("receipt.txt", "text/plain", b"ok"),
        ("payload.bin", "application/octet-stream", b"ignored"),
    )
    attachments = receipt.iter_supported_attachments(raw)
    assert len(attachments) == 1
    assert attachments[0]["content_type"] == "text/plain"

    monkeypatch.setattr(receipt, "MAX_ATTACHMENTS", 1)
    too_many = _email(
        ("one.txt", "text/plain", b"1"),
        ("two.txt", "text/plain", b"2"),
    )
    with pytest.raises(receipt.ReceiptExtractionError, match="too many attachments"):
        receipt.iter_supported_attachments(too_many)


def test_attachment_parser_enforces_total_limit(monkeypatch):
    monkeypatch.setattr(receipt, "MAX_TOTAL_ATTACHMENT_BYTES", 3)
    raw = _email(("one.txt", "text/plain", b"1234"))
    with pytest.raises(receipt.ReceiptExtractionError, match="20 MB total"):
        receipt.iter_supported_attachments(raw)


def test_content_type_falls_back_to_safe_filename_suffix_and_empty_filename():
    assert (
        receipt._content_type(
            SimpleNamespace(get_content_type=lambda: "application/octet-stream"),
            "receipt.PDF",
        )
        == "application/pdf"
    )
    part = EmailMessage()
    assert receipt._safe_filename(part, 0) == "receipt-1"
    part.add_header("Content-Disposition", "attachment", filename="../../receipt.txt")
    assert receipt._safe_filename(part, 0) == "receipt.txt"


def test_candidate_extraction_handles_invalid_money_and_fallback_date(monkeypatch):
    class _InvalidMatch:
        def group(self, _index):
            return "not-a-number"

    class _InvalidPattern:
        def finditer(self, _source):
            return [_InvalidMatch()]

    monkeypatch.setattr(receipt, "_MONEY_RE", _InvalidPattern())
    result = receipt.extract_candidates("ACME\nMarch 3, 2026\nTotal: $1.00")
    assert result["values"]["total"] is None
    assert result["values"]["date"] == "2026-03-03"


def test_candidate_extraction_handles_invalid_tax_and_unparseable_dates(monkeypatch):
    class _InvalidMatch:
        def group(self, _index):
            return "not-a-number"

    class _InvalidPattern:
        def finditer(self, _source):
            return [_InvalidMatch()]

    monkeypatch.setattr(receipt, "_TAX_RE", _InvalidPattern())
    assert receipt._parse_date("not-a-date") is None
    result = receipt.extract_candidates("Vendor: ACME\nTax: $1.00")
    assert result["values"]["tax_amount"] is None


def test_extract_attachment_text_pdf_ocr_and_failure_paths(monkeypatch):
    monkeypatch.setattr(receipt, "extract_text", lambda *args, **kwargs: "")
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.services.template_ocr",
        SimpleNamespace(
            ocr_pdf=lambda *args, **kwargs: SimpleNamespace(
                text="OCR text",
                average_confidence=0.91,
                pages_analyzed=2,
                truncated=True,
            )
        ),
    )
    text, state, metadata = receipt.extract_attachment_text(
        {"content": b"pdf", "content_type": "application/pdf", "filename": "r.pdf"}
    )
    assert (text, state) == ("OCR text", "extracted")
    assert metadata["ocr_pages"] == 2

    monkeypatch.setattr(
        receipt,
        "extract_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    text, state, metadata = receipt.extract_attachment_text(
        {"content": b"doc", "content_type": "text/plain", "filename": "r.txt"}
    )
    assert (text, state, metadata) == (
        "",
        "needs_review",
        {"ocr_used": False, "ocr_confidence": None},
    )


def test_extract_attachment_text_image_success_and_pdf_ocr_failure(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.services.template_ocr",
        SimpleNamespace(
            ocr_image=lambda content: SimpleNamespace(
                text="image text", average_confidence=0.88, lines_detected=3
            ),
            ocr_pdf=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
        ),
    )
    text, state, metadata = receipt.extract_attachment_text(
        {"content": b"image", "content_type": "image/png", "filename": "r.png"}
    )
    assert (text, state) == ("image text", "extracted")
    assert metadata["ocr_lines"] == 3

    monkeypatch.setattr(receipt, "extract_text", lambda *args, **kwargs: "")
    text, state, metadata = receipt.extract_attachment_text(
        {"content": b"pdf", "content_type": "application/pdf", "filename": "r.pdf"}
    )
    assert (text, state) == ("", "needs_review")
    assert metadata["ocr_used"] is True


def test_attachment_hash_is_stable():
    assert receipt.attachment_hash(b"receipt") == receipt.attachment_hash(b"receipt")
    assert len(receipt.attachment_hash(b"receipt")) == 64


def test_candidate_extraction_is_deterministic_and_defaults_category():
    result = receipt.extract_candidates(
        "Vendor: ACME Court Services\nInvoice #: INV-42\nDate: 08/25/2026\nTotal: $1,234.50"
    )
    assert result["values"] == {
        "vendor": "ACME Court Services",
        "date": "2026-08-25",
        "due_date": None,
        "total": "1234.50",
        "tax_amount": None,
        "invoice_number": "INV-42",
        "category": "court filing",
    }
    assert result["confidence"] == 1.0


def test_unreadable_image_receipt_needs_review_after_ocr_attempt():
    text, state, metadata = receipt.extract_attachment_text(
        {
            "content": b"not-ocr",
            "content_type": "image/jpeg",
            "filename": "receipt.jpg",
        }
    )
    assert text == ""
    assert state == "needs_review"
    assert metadata == {"ocr_used": True, "ocr_confidence": None}


def test_invoice_date_and_subtotal_are_not_misread_as_reference_or_total():
    result = receipt.extract_candidates(
        "ACME Services\nInvoice Date: 08/25/2026\nDue Date: 09/24/2026\nSubtotal: $80.00\nTax: $5.00"
    )
    assert result["values"]["date"] == "2026-08-25"
    assert result["values"]["due_date"] == "2026-09-24"
    assert result["values"]["invoice_number"] is None
    assert result["values"]["total"] is None
    assert result["values"]["tax_amount"] == "5.00"
