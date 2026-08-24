"""Portal email templates and the sign-out revocation fallback.

Matter and case names are firm-entered free text that lands directly in an
HTML email body, so the escaping here is the only thing between a stray
``<`` in a matter name and a broken (or injected) message.
"""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.routers.client_portal import _is_revoked_jti, _revoke_jti
from app.services.email import (
    EmailDeliveryResult,
    send_client_portal_invite,
    send_client_portal_message_alert,
    send_portal_invite,
)


def _sent_bodies(mock) -> tuple[str, str]:
    kwargs = mock.await_args.kwargs
    return kwargs["html_body"], kwargs["text_body"]


@pytest.mark.asyncio
async def test_client_invite_email_escapes_the_matter_name():
    with patch(
        "app.services.email.email_service.send_email",
        new_callable=AsyncMock,
        return_value=EmailDeliveryResult.SENT,
    ) as mock:
        result = await send_client_portal_invite(
            to_email="client@example.com",
            matter_name='Ferris & Co <script>alert("x")</script>',
            invite_url="https://app.example.com/portal/client/accept?token=abc&x=1",
        )
    assert result is EmailDeliveryResult.SENT
    html, _text = _sent_bodies(mock)
    assert "<script>" not in html
    assert "Ferris &amp; Co" in html
    # The URL keeps its query separator escaped rather than splitting the attribute.
    assert "token=abc&amp;x=1" in html


@pytest.mark.asyncio
async def test_mediation_invite_email_escapes_the_case_name():
    with patch(
        "app.services.email.email_service.send_email",
        new_callable=AsyncMock,
        return_value=EmailDeliveryResult.SENT,
    ) as mock:
        await send_portal_invite(
            to_email="party@example.com",
            case_name="<b>Doe</b> v. Roe",
            invite_url="https://app.example.com/portal/accept?token=xyz",
        )
    html, _text = _sent_bodies(mock)
    assert "<b>Doe</b>" not in html
    assert "&lt;b&gt;Doe&lt;/b&gt;" in html


@pytest.mark.asyncio
async def test_message_alert_truncates_and_escapes_the_preview():
    long_body = "<i>" + ("word " * 200)
    with patch(
        "app.services.email.email_service.send_email",
        new_callable=AsyncMock,
        return_value=EmailDeliveryResult.SENT,
    ) as mock:
        await send_client_portal_message_alert(
            to_emails=["lead@firm.com", "paralegal@firm.com"],
            matter_name="Rivera v. Northline",
            sender="client@example.com",
            body=long_body,
            matter_url="https://app.example.com/matters/1?tab=portal",
        )
    kwargs = mock.await_args.kwargs
    assert kwargs["to"] == ["lead@firm.com", "paralegal@firm.com"]
    html, text = kwargs["html_body"], kwargs["text_body"]
    assert "<i>" not in html
    # Only a preview leaves the portal — the full privileged message stays put.
    assert "…" in text
    assert len(long_body) > 400
    assert long_body not in text


@pytest.mark.asyncio
async def test_message_alert_preserves_a_short_body_intact():
    with patch(
        "app.services.email.email_service.send_email",
        new_callable=AsyncMock,
        return_value=EmailDeliveryResult.SENT,
    ) as mock:
        await send_client_portal_message_alert(
            to_emails=["lead@firm.com"],
            matter_name="Rivera v. Northline",
            sender="client@example.com",
            body="Any update on the deposition?",
            matter_url="https://app.example.com/matters/1",
        )
    _html, text = _sent_bodies(mock)
    assert "Any update on the deposition?" in text
    assert "…" not in text


# ── Sign-out revocation without Redis ───────────────────────────────────────


def _request_without_redis() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


@pytest.mark.asyncio
async def test_revocation_falls_back_to_the_in_memory_blacklist():
    request = _request_without_redis()
    jti = str(uuid.uuid4())
    assert await _is_revoked_jti(request, jti) is False

    await _revoke_jti(request, jti, int(time.time()) + 600)
    assert await _is_revoked_jti(request, jti) is True


@pytest.mark.asyncio
async def test_revoking_an_already_expired_token_is_a_no_op():
    request = _request_without_redis()
    jti = str(uuid.uuid4())
    await _revoke_jti(request, jti, int(time.time()) - 1)
    # Nothing to revoke: the token is unusable on its own expiry claim.
    assert await _is_revoked_jti(request, jti) is False


@pytest.mark.asyncio
async def test_revocation_is_skipped_for_a_token_with_no_jti():
    request = _request_without_redis()
    await _revoke_jti(request, None, int(time.time()) + 600)
    assert await _is_revoked_jti(request, None) is False


@pytest.mark.asyncio
async def test_revocation_uses_redis_when_it_is_available():
    redis = SimpleNamespace(
        setex=AsyncMock(), exists=AsyncMock(return_value=1)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    jti = str(uuid.uuid4())
    await _revoke_jti(request, jti, int(time.time()) + 600)
    redis.setex.assert_awaited_once()
    assert redis.setex.await_args.args[0] == f"jti:{jti}"
    assert await _is_revoked_jti(request, jti) is True


# ── Key-date parsing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-03-14", "2026-03-14"),
        ("2026-03-14T09:30:00Z", "2026-03-14"),
        ("2026-03-14 09:30", "2026-03-14"),
        ("2026/03/14", "2026-03-14"),
        ("03/14/2026", "2026-03-14"),
        ("14 March 2026", "2026-03-14"),
        ("March 14, 2026", "2026-03-14"),
    ],
)
def test_key_dates_parse_the_formats_firm_staff_actually_type(raw, expected):
    from app.routers.client_portal import _parse_key_date

    assert _parse_key_date(raw).isoformat() == expected


@pytest.mark.parametrize("raw", ["sometime in spring", "", "   ", None, 42, ["a"]])
def test_unparseable_key_dates_are_kept_but_undated(raw):
    from app.routers.client_portal import _parse_key_date

    assert _parse_key_date(raw) is None


def test_key_date_list_keeps_undated_notes_and_drops_empty_values():
    import datetime as dt

    from app.routers.client_portal import _build_key_dates

    entries = _build_key_dates(
        {
            "filing_deadline": dt.date(2026, 3, 14),
            "venue_note": "Cook County",
            "blank": "",
            "missing": None,
            "empty_list": [],
        }
    )
    labels = [e.label for e in entries]
    # Blank firm fields never reach the client as empty rows.
    assert labels == ["Filing deadline", "Venue note"]
    assert entries[0].iso_date == dt.date(2026, 3, 14)
    assert entries[1].iso_date is None


def test_key_dates_tolerate_a_non_mapping_value():
    from app.routers.client_portal import _build_key_dates

    assert _build_key_dates(None) == []
    assert _build_key_dates("not a mapping") == []


# ── Upload filename validation ──────────────────────────────────────────────


def test_upload_rejects_a_filename_that_is_only_a_path():
    from fastapi import HTTPException

    from app.routers.client_portal import _validate_upload_filename

    for name in ("../", "./", "/"):
        with pytest.raises(HTTPException) as exc:
            _validate_upload_filename(name)
        assert exc.value.status_code == 400


def test_upload_rejects_an_overlong_filename():
    from fastapi import HTTPException

    from app.routers.client_portal import _validate_upload_filename

    with pytest.raises(HTTPException) as exc:
        _validate_upload_filename("a" * 300 + ".pdf")
    assert "too long" in exc.value.detail.lower()


def test_upload_accepts_an_allowlisted_extension_case_insensitively():
    from app.routers.client_portal import _validate_upload_filename

    assert _validate_upload_filename("Statement.PDF") == "Statement.PDF"
    # A Windows client can submit a full path; only the basename is stored.
    assert _validate_upload_filename("C:\\Users\\me\\scan.JPEG") == "scan.JPEG"
    assert _validate_upload_filename("/home/me/Docs/scan.jpeg") == "scan.jpeg"
