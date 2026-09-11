"""Attachment extraction from inbound mail, and the SMS consent categories.

Attachments used to live only inside the stored `.eml`, so finding a filed
order meant downloading the message and opening it in a mail client. The
filename is the sharp edge here: it is attacker-controlled text that becomes a
path, so the tests below are mostly about what a sender cannot make it do.
"""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from types import SimpleNamespace

from app.services.inbound_email import (
    MAX_ATTACHMENTS_PER_EMAIL,
    MAX_ATTACHMENT_BYTES,
    _attachment_filename,
    email_attachments,
)
from app.services.sms_categories import (
    DISCLOSURE_CASE_UPDATES,
    DISCLOSURE_INTAKE_ONLY,
    SMS_CATEGORY_CASE_UPDATES,
    SMS_CATEGORY_INTAKE,
    allows_case_updates,
    disclosure_version,
    granted_categories,
)


def build(*attachments, body="See attached."):
    message = EmailMessage()
    message["Subject"] = "Filed order"
    message["From"] = "clerk@court.example.gov"
    message["To"] = "firm@example.com"
    message.set_content(body)
    for filename, content_type, payload in attachments:
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            payload, maintype=maintype, subtype=subtype, filename=filename
        )
    return BytesParser(policy=policy.default).parsebytes(message.as_bytes())


def test_each_attachment_comes_back_with_its_bytes():
    found = email_attachments(
        build(
            ("order.pdf", "application/pdf", b"%PDF-1.4 order"),
            ("exhibit.png", "image/png", b"\x89PNG exhibit"),
        )
    )

    assert [(name, kind) for name, kind, _ in found] == [
        ("order.pdf", "application/pdf"),
        ("exhibit.png", "image/png"),
    ]
    assert [payload for _, _, payload in found] == [b"%PDF-1.4 order", b"\x89PNG exhibit"]


def test_a_message_with_no_attachments_yields_nothing():
    assert email_attachments(build()) == []


def test_a_plain_single_part_message_yields_nothing():
    message = EmailMessage()
    message.set_content("Just a note.")
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())

    assert email_attachments(parsed) == []


def test_an_oversized_attachment_is_skipped_without_losing_the_others():
    found = email_attachments(
        build(
            ("huge.bin", "application/octet-stream", b"x" * (MAX_ATTACHMENT_BYTES + 1)),
            ("small.pdf", "application/pdf", b"%PDF small"),
        )
    )

    assert [name for name, _, _ in found] == ["small.pdf"]


def test_no_more_than_the_cap_is_taken():
    many = [
        (f"page-{i}.pdf", "application/pdf", f"page {i}".encode())
        for i in range(MAX_ATTACHMENTS_PER_EMAIL + 5)
    ]

    assert len(email_attachments(build(*many))) == MAX_ATTACHMENTS_PER_EMAIL


def test_a_sender_cannot_put_a_path_in_the_filename():
    part = SimpleNamespace(get_filename=lambda: "../../etc/passwd")

    assert _attachment_filename(part, 1) == "passwd"


def test_a_windows_path_is_stripped_too():
    part = SimpleNamespace(get_filename=lambda: r"C:\Users\victim\notes.docx")

    assert _attachment_filename(part, 1) == "notes.docx"


def test_control_characters_and_separators_are_replaced():
    part = SimpleNamespace(get_filename=lambda: 'we\x00ird:name*?"<>|.pdf')

    cleaned = _attachment_filename(part, 1)

    assert "\x00" not in cleaned and ":" not in cleaned and "*" not in cleaned
    assert cleaned.endswith(".pdf")


def test_a_nameless_attachment_is_given_one():
    part = SimpleNamespace(get_filename=lambda: None)

    assert _attachment_filename(part, 4) == "attachment-4"


def test_a_name_that_cleans_away_to_nothing_falls_back():
    part = SimpleNamespace(get_filename=lambda: "///")

    assert _attachment_filename(part, 2) == "attachment-2"


def test_an_encoded_word_filename_is_decoded():
    part = SimpleNamespace(get_filename=lambda: "=?utf-8?q?resum=C3=A9.pdf?=")

    assert _attachment_filename(part, 1) == "resumé.pdf"


def test_an_undecodable_filename_does_not_raise():
    part = SimpleNamespace(get_filename=lambda: "=?bogus-charset?q?x?=.pdf")

    assert _attachment_filename(part, 1)


def test_a_very_long_filename_is_truncated():
    part = SimpleNamespace(get_filename=lambda: "a" * 500 + ".pdf")

    assert len(_attachment_filename(part, 1)) == 200


def test_intake_consent_does_not_cover_the_rest_of_the_case():
    assert granted_categories(case_updates=False) == [SMS_CATEGORY_INTAKE]
    assert disclosure_version(case_updates=False) == DISCLOSURE_INTAKE_ONLY
    # This is the whole point: an intake grant refuses case updates on its own,
    # which is why existing consents need no migration.
    assert allows_case_updates(
        SimpleNamespace(allowed_categories=[SMS_CATEGORY_INTAKE])
    ) is False


def test_a_wider_consent_records_both_categories():
    assert granted_categories(case_updates=True) == [
        SMS_CATEGORY_INTAKE,
        SMS_CATEGORY_CASE_UPDATES,
    ]
    assert disclosure_version(case_updates=True) == DISCLOSURE_CASE_UPDATES
    assert allows_case_updates(
        SimpleNamespace(allowed_categories=[SMS_CATEGORY_INTAKE, SMS_CATEGORY_CASE_UPDATES])
    ) is True


def test_a_malformed_or_missing_consent_refuses():
    assert allows_case_updates(SimpleNamespace(allowed_categories=None)) is False
    assert allows_case_updates(SimpleNamespace(allowed_categories="case_updates")) is False
    assert allows_case_updates(SimpleNamespace()) is False
