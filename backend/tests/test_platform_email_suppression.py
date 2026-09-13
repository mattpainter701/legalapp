"""System email preflight, suppression enforcement, and the bounce webhook.

System email carries password resets. Two failure modes matter here and both
are silent by construction: a deployment whose relay was never configured
(delivery no-ops while the route still answers "a reset link has been sent"),
and a sending domain whose reputation has been ground down by repeated
delivery to dead addresses. These tests pin the behavior that makes each one
loud.
"""

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import validate_platform_email_settings
from app.routers import platform_email_webhooks as webhook_routes
from app.services import email as email_module
from app.services import email_suppression as suppression_module
from app.services.email import EmailDeliveryResult, EmailService
from app.services.email_suppression import filter_suppressed, normalize_address


# ── Preflight ────────────────────────────────────────────────────────────────


def _email_settings(**overrides):
    values = {
        "EMAIL_ENABLED": True,
        "EMAIL_REQUIRED": False,
        "EMAIL_SUPPRESSION_ENABLED": True,
        "EMAIL_HOST": "smtp.resend.com",
        "EMAIL_PORT": 587,
        "EMAIL_USER": "resend",
        "EMAIL_PASS": "re_relay_api_key",
        "EMAIL_FROM": "support@getlawhand.com",
        "PLATFORM_EMAIL_WEBHOOK_SECRET": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fully_configured_relay_passes_preflight():
    validate_platform_email_settings(_email_settings(EMAIL_REQUIRED=True))


def test_required_email_refuses_to_boot_when_delivery_is_disabled():
    """The silent-no-op failure mode: reset routes answer success regardless."""
    with pytest.raises(ValueError, match="EMAIL_REQUIRED=true but EMAIL_ENABLED=false"):
        validate_platform_email_settings(
            _email_settings(EMAIL_REQUIRED=True, EMAIL_ENABLED=False)
        )


def test_required_email_demands_authenticated_submission():
    # A hosted relay is reached across the network, so a missing credential is
    # a forgotten secret rather than a trusted loopback relay.
    with pytest.raises(ValueError, match="authenticated SMTP"):
        validate_platform_email_settings(
            _email_settings(EMAIL_REQUIRED=True, EMAIL_USER="", EMAIL_PASS="")
        )


def test_half_supplied_credentials_are_rejected():
    with pytest.raises(ValueError, match="EMAIL_USER and EMAIL_PASS"):
        validate_platform_email_settings(_email_settings(EMAIL_PASS=""))


def test_placeholder_relay_password_is_rejected():
    with pytest.raises(ValueError, match="EMAIL_PASS is still a placeholder"):
        validate_platform_email_settings(
            _email_settings(EMAIL_PASS="change-me-in-prod")
        )


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"EMAIL_HOST": ""}, "EMAIL_HOST"),
        ({"EMAIL_FROM": "not-an-address"}, "EMAIL_FROM"),
        ({"EMAIL_PORT": 0}, "EMAIL_PORT"),
    ],
)
def test_enabled_email_requires_a_complete_configuration(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_platform_email_settings(_email_settings(**overrides))


def test_disabled_email_does_not_police_unused_settings():
    """dev1 and CI deliberately run with no outbound mail at all."""
    validate_platform_email_settings(
        _email_settings(EMAIL_ENABLED=False, EMAIL_HOST="", EMAIL_FROM="")
    )


@pytest.mark.parametrize(
    "secret,message",
    [
        # An undecodable value would fail every signature check at runtime and
        # reject every bounce silently, so it is caught at boot instead.
        ("not-valid-base64!!", "base64 Svix signing secret"),
        ("whsec_also-not-base64!!", "base64 Svix signing secret"),
        ("change-me-" + "x" * 30, "placeholder"),
        (base64.b64encode(b"too short").decode(), "fewer than 24 bytes"),
    ],
)
def test_unusable_webhook_secret_is_rejected(secret, message):
    with pytest.raises(ValueError, match=message):
        validate_platform_email_settings(
            _email_settings(PLATFORM_EMAIL_WEBHOOK_SECRET=secret)
        )


@pytest.mark.parametrize(
    "secret",
    [
        "whsec_" + base64.b64encode(bytes(range(32))).decode(),
        # Resend publishes the prefixed form, but the bare secret is the same key.
        base64.b64encode(bytes(range(32))).decode(),
    ],
)
def test_real_svix_signing_secret_is_accepted(secret):
    validate_platform_email_settings(
        _email_settings(PLATFORM_EMAIL_WEBHOOK_SECRET=secret)
    )


# ── Address normalization ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Person@Example.COM ", "person@example.com"),
        ("Jane Doe <Jane@Example.com>", "jane@example.com"),
        ("", ""),
    ],
)
def test_addresses_normalize_for_comparison(raw, expected):
    assert normalize_address(raw) == expected


def test_plus_addressing_is_not_collapsed():
    # Providers disagree about whether these are the same mailbox, so a bounce
    # for one must never suppress the other.
    assert normalize_address("a+tag@example.com") != normalize_address("a@example.com")


# ── Suppression filtering ────────────────────────────────────────────────────


class _SuppressionResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal stand-in for the suppression SELECT; no database required."""

    def __init__(self, blocked=(), error=None):
        self.blocked = list(blocked)
        self.error = error

    async def execute(self, statement):
        if self.error:
            raise self.error
        return _SuppressionResult(self.blocked)


@pytest.mark.asyncio
async def test_suppressed_recipient_is_dropped_and_others_survive(monkeypatch):
    monkeypatch.setattr(suppression_module.settings, "EMAIL_SUPPRESSION_ENABLED", True)
    session = _FakeSession(blocked=["dead@example.com"])

    deliverable, suppressed = await filter_suppressed(
        ["Dead@example.com", "live@example.com"], db=session
    )

    assert deliverable == ["live@example.com"]
    assert suppressed == ["Dead@example.com"]


@pytest.mark.asyncio
async def test_suppression_lookup_failure_allows_the_send(monkeypatch):
    """Fails open: an unreachable table must not block account recovery."""
    monkeypatch.setattr(suppression_module.settings, "EMAIL_SUPPRESSION_ENABLED", True)
    session = _FakeSession(error=RuntimeError("database down"))

    deliverable, suppressed = await filter_suppressed(["user@example.com"], db=session)

    assert deliverable == ["user@example.com"]
    assert suppressed == []


@pytest.mark.asyncio
async def test_disabled_suppression_skips_the_lookup_entirely(monkeypatch):
    monkeypatch.setattr(suppression_module.settings, "EMAIL_SUPPRESSION_ENABLED", False)
    session = _FakeSession(error=AssertionError("must not be queried"))

    deliverable, suppressed = await filter_suppressed(["user@example.com"], db=session)

    assert deliverable == ["user@example.com"]
    assert suppressed == []


# ── EmailService integration ─────────────────────────────────────────────────


def _configure_smtp(monkeypatch) -> None:
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", True)
    monkeypatch.setattr(email_module.settings, "EMAIL_HOST", "smtp.test")
    monkeypatch.setattr(email_module.settings, "EMAIL_PORT", 587)
    monkeypatch.setattr(email_module.settings, "EMAIL_USER", "mailer@testfirm.com")
    monkeypatch.setattr(email_module.settings, "EMAIL_PASS", "test-password")
    monkeypatch.setattr(email_module.settings, "EMAIL_FROM", "mailer@testfirm.com")


@pytest.mark.asyncio
async def test_fully_suppressed_send_never_reaches_the_relay(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(suppression_module.settings, "EMAIL_SUPPRESSION_ENABLED", True)

    async def fail_send(message, **kwargs):
        raise AssertionError("SMTP must not be contacted for a suppressed recipient")

    monkeypatch.setattr(email_module.aiosmtplib, "send", fail_send)

    result = await EmailService().send_email(
        ["dead@example.com"],
        "Reset your LawHand password",
        "<p>Reset</p>",
        "Reset",
        db=_FakeSession(blocked=["dead@example.com"]),
    )

    assert result is EmailDeliveryResult.SUPPRESSED
    assert not result
    # Not a configuration error: nothing is misconfigured and a retry is futile.
    assert not result.is_configuration_error


@pytest.mark.asyncio
async def test_partial_suppression_still_delivers_to_the_rest(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(suppression_module.settings, "EMAIL_SUPPRESSION_ENABLED", True)
    sent = []

    async def fake_send(message, **kwargs):
        sent.append(message)

    monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)

    result = await EmailService().send_email(
        ["dead@example.com", "live@example.com"],
        "Case update",
        "<p>Update</p>",
        "Update",
        db=_FakeSession(blocked=["dead@example.com"]),
    )

    assert result is EmailDeliveryResult.SENT
    assert len(sent) == 1
    assert sent[0]["To"] == "live@example.com"


def test_suppressed_result_maps_to_an_actionable_api_error():
    status_code, detail = email_module.email_delivery_http_error(
        EmailDeliveryResult.SUPPRESSED, action="Sending the notice"
    )
    assert status_code == 422
    assert "suppression" in detail.lower()


# ── Bounce webhook (Resend, Svix-signed) ─────────────────────────────────────


# A real Svix secret is base64 over >= 24 bytes, published with a whsec_ prefix.
_SECRET_BYTES = bytes(range(32))
_SECRET = "whsec_" + base64.b64encode(_SECRET_BYTES).decode()


def _sign(body: bytes, *, svix_id: str, timestamp: int, secret_bytes=_SECRET_BYTES):
    signed = b".".join((svix_id.encode(), str(timestamp).encode(), body))
    digest = hmac.new(secret_bytes, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


class _WebhookRequest:
    def __init__(
        self, payload: bytes, *, svix_id="msg_1", timestamp=None, signature=None
    ):
        self._payload = payload
        timestamp = int(time.time()) if timestamp is None else timestamp
        if signature is None:
            signature = _sign(payload, svix_id=svix_id, timestamp=timestamp)
        self.headers = {
            "svix-id": svix_id,
            "svix-timestamp": str(timestamp),
            "svix-signature": signature,
        }

    async def body(self) -> bytes:
        return self._payload


class _WebhookDB:
    """Records claim attempts; returns None once an event id is already seen."""

    def __init__(self):
        self.claimed: set[str] = set()
        self.commits = 0

    async def execute(self, statement):
        compiled = str(statement)
        assert "platform_email_webhook_events" in compiled
        event_id = statement.compile().params.get("event_id")
        if event_id in self.claimed:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        self.claimed.add(event_id)
        return SimpleNamespace(scalar_one_or_none=lambda: "row-id")

    async def commit(self):
        self.commits += 1


def _event(event_type: str, *, to=("dead@example.com",), bounce=None) -> bytes:
    data = {"email_id": "email-1", "to": list(to)}
    if bounce is not None:
        data["bounce"] = bounce
    return json.dumps(
        {"type": event_type, "created_at": "2026-09-13T10:00:00Z", "data": data}
    ).encode()


@pytest.fixture
def webhook_env(monkeypatch):
    monkeypatch.setattr(
        webhook_routes.settings, "PLATFORM_EMAIL_WEBHOOK_SECRET", _SECRET
    )
    recorded = []

    async def fake_record(email, **kwargs):
        recorded.append((email, kwargs.get("reason")))
        return True

    monkeypatch.setattr(webhook_routes, "record_suppression", fake_record)
    return recorded


@pytest.mark.asyncio
async def test_permanent_bounce_suppresses_the_address(webhook_env):
    payload = _event(
        "email.bounced",
        bounce={"type": "Permanent", "subType": "General", "message": "no such user"},
    )
    result = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload), db=_WebhookDB()
    )

    assert result["status"] == "applied"
    assert result["reason"] == "hard_bounce"
    assert webhook_env == [("dead@example.com", "hard_bounce")]


@pytest.mark.asyncio
async def test_nonexistent_mailbox_is_recorded_as_a_bad_mailbox(webhook_env):
    payload = _event(
        "email.bounced", bounce={"type": "Permanent", "subType": "NoEmail"}
    )
    result = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload), db=_WebhookDB()
    )

    assert result["reason"] == "bad_mailbox"


@pytest.mark.asyncio
async def test_spam_complaint_suppresses_the_address(webhook_env):
    payload = _event("email.complained", to=("angry@example.com",))
    result = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload), db=_WebhookDB()
    )

    assert result["reason"] == "spam_complaint"
    assert webhook_env == [("angry@example.com", "spam_complaint")]


@pytest.mark.asyncio
@pytest.mark.parametrize("bounce_type", ["Transient", "Undetermined", ""])
async def test_non_permanent_bounce_never_suppresses(webhook_env, bounce_type):
    """A full mailbox must not cost a user their password reset."""
    payload = _event(
        "email.bounced", bounce={"type": bounce_type, "subType": "MailboxFull"}
    )
    result = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload), db=_WebhookDB()
    )

    assert result["status"] == "ignored"
    assert webhook_env == []


@pytest.mark.asyncio
async def test_unrelated_event_types_are_ignored(webhook_env):
    for event_type in ("email.sent", "email.delivered", "email.opened"):
        result = await webhook_routes.resend_delivery_event(
            _WebhookRequest(_event(event_type)), db=_WebhookDB()
        )
        assert result["status"] == "ignored"
    assert webhook_env == []


@pytest.mark.asyncio
async def test_multi_recipient_bounce_suppresses_nobody(webhook_env):
    """The event does not say which of several recipients failed.

    Suppressing all of them would lock working mailboxes out of password reset
    over somebody else's dead address.
    """
    payload = _event(
        "email.bounced",
        to=("dead@example.com", "live@example.com"),
        bounce={"type": "Permanent", "subType": "General"},
    )
    result = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload), db=_WebhookDB()
    )

    assert result["status"] == "ignored"
    assert webhook_env == []


@pytest.mark.asyncio
async def test_redelivered_event_is_applied_only_once(webhook_env):
    payload = _event(
        "email.bounced", bounce={"type": "Permanent", "subType": "General"}
    )
    db = _WebhookDB()

    first = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload, svix_id="msg_dup"), db=db
    )
    second = await webhook_routes.resend_delivery_event(
        _WebhookRequest(payload, svix_id="msg_dup"), db=db
    )

    assert first["status"] == "applied"
    assert second["status"] == "duplicate"
    # The redelivery must not re-suppress an address an operator has released.
    assert len(webhook_env) == 1


@pytest.mark.asyncio
async def test_a_bounce_and_a_later_complaint_are_distinct_events(webhook_env):
    """Both carry the same Resend email_id, so svix-id must be the dedupe key."""
    db = _WebhookDB()

    await webhook_routes.resend_delivery_event(
        _WebhookRequest(
            _event("email.bounced", bounce={"type": "Permanent", "subType": "General"}),
            svix_id="msg_bounce",
        ),
        db=db,
    )
    second = await webhook_routes.resend_delivery_event(
        _WebhookRequest(_event("email.complained"), svix_id="msg_complaint"), db=db
    )

    assert second["status"] == "applied"
    assert len(webhook_env) == 2


# ── Webhook authentication ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forged_signature_is_rejected(webhook_env):
    payload = _event(
        "email.bounced", bounce={"type": "Permanent", "subType": "General"}
    )
    wrong = _sign(
        payload, svix_id="msg_1", timestamp=int(time.time()), secret_bytes=b"x" * 32
    )

    with pytest.raises(HTTPException) as exc:
        await webhook_routes.resend_delivery_event(
            _WebhookRequest(payload, signature=wrong), db=_WebhookDB()
        )

    assert exc.value.status_code == 401
    assert webhook_env == []


@pytest.mark.asyncio
async def test_tampered_body_is_rejected(webhook_env):
    """The signature covers the body, so an edited payload cannot be replayed."""
    original = _event(
        "email.bounced",
        to=("dead@example.com",),
        bounce={"type": "Permanent", "subType": "General"},
    )
    timestamp = int(time.time())
    signature = _sign(original, svix_id="msg_1", timestamp=timestamp)
    tampered = original.replace(b"dead@example.com", b"ceo@example.com")

    with pytest.raises(HTTPException) as exc:
        await webhook_routes.resend_delivery_event(
            _WebhookRequest(tampered, timestamp=timestamp, signature=signature),
            db=_WebhookDB(),
        )

    assert exc.value.status_code == 401
    assert webhook_env == []


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [-3600, 3600])
async def test_stale_timestamp_is_rejected(webhook_env, age):
    payload = _event(
        "email.bounced", bounce={"type": "Permanent", "subType": "General"}
    )
    stale = int(time.time()) + age

    with pytest.raises(HTTPException) as exc:
        await webhook_routes.resend_delivery_event(
            _WebhookRequest(payload, timestamp=stale), db=_WebhookDB()
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_headers_are_rejected(webhook_env):
    request = _WebhookRequest(b"{}")
    request.headers = {}

    with pytest.raises(HTTPException) as exc:
        await webhook_routes.resend_delivery_event(request, db=_WebhookDB())

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unconfigured_webhook_refuses_rather_than_accepting_anything(
    monkeypatch,
):
    monkeypatch.setattr(webhook_routes.settings, "PLATFORM_EMAIL_WEBHOOK_SECRET", "")

    with pytest.raises(HTTPException) as exc:
        await webhook_routes.resend_delivery_event(
            _WebhookRequest(b"{}"), db=_WebhookDB()
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_malformed_payload_is_rejected_after_the_signature_passes(webhook_env):
    with pytest.raises(HTTPException) as exc:
        await webhook_routes.resend_delivery_event(
            _WebhookRequest(b"not json"), db=_WebhookDB()
        )

    assert exc.value.status_code == 400


def test_signature_verification_accepts_a_rotated_secret_pair():
    """Svix sends both signatures during a rotation; either must authenticate."""
    body = b'{"type":"email.bounced"}'
    timestamp = int(time.time())
    old = _sign(body, svix_id="msg_1", timestamp=timestamp, secret_bytes=b"o" * 32)
    new = _sign(body, svix_id="msg_1", timestamp=timestamp)

    assert webhook_routes.verify_svix_signature(
        secret=_SECRET,
        svix_id="msg_1",
        svix_timestamp=str(timestamp),
        svix_signature=f"{old} {new}",
        body=body,
    )


def test_signature_verification_rejects_an_undecodable_secret():
    body = b"{}"
    timestamp = int(time.time())
    assert not webhook_routes.verify_svix_signature(
        secret="whsec_not-valid-base64!!",
        svix_id="msg_1",
        svix_timestamp=str(timestamp),
        svix_signature=_sign(body, svix_id="msg_1", timestamp=timestamp),
        body=body,
    )
