from email.message import EmailMessage

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
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
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
    raw = _email(("receipt.txt", "text/plain", b"x" * (receipt.MAX_ATTACHMENT_BYTES + 1)))
    with pytest.raises(receipt.ReceiptExtractionError, match="10 MB"):
        receipt.iter_supported_attachments(raw)


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
    text, state, metadata = receipt.extract_attachment_text({
        "content": b"not-ocr",
        "content_type": "image/jpeg",
        "filename": "receipt.jpg",
    })
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
