"""System email preflight, suppression enforcement, and the bounce webhook.

System email carries password resets. Two failure modes matter here and both
are silent by construction: a deployment whose relay was never configured
(delivery no-ops while the route still answers "a reset link has been sent"),
and a sending domain whose reputation has been ground down by repeated
delivery to dead addresses. These tests pin the behavior that makes each one
loud.
"""

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
        "EMAIL_HOST": "smtp.postmarkapp.com",
        "EMAIL_PORT": 587,
        "EMAIL_USER": "relay-token",
        "EMAIL_PASS": "relay-token",
        "EMAIL_FROM": "no-reply@getlawhand.com",
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
    [("short", "at least 32 characters"), ("change-me-" + "x" * 30, "placeholder")],
)
def test_weak_webhook_secret_is_rejected(secret, message):
    with pytest.raises(ValueError, match=message):
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


# ── Bounce webhook ───────────────────────────────────────────────────────────


class _WebhookRequest:
    def __init__(self, payload: bytes, token: str | None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}
        self._payload = payload

    async def body(self) -> bytes:
        return self._payload


class _WebhookDB:
    """Records claim attempts; returns None once an event id is already seen."""

    def __init__(self):
        self.claimed: set[str] = set()
        self.commits = 0

    async def execute(self, statement):
        compiled = str(statement)
        # The only INSERT this route issues through the session is the event
        # claim; suppression writes go through record_suppression.
        event_id = statement.compile().params.get("event_id")
        if event_id in self.claimed:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        self.claimed.add(event_id)
        assert "platform_email_webhook_events" in compiled
        return SimpleNamespace(scalar_one_or_none=lambda: "row-id")

    async def commit(self):
        self.commits += 1


_SECRET = "s" * 48


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
async def test_hard_bounce_suppresses_the_address(webhook_env):
    payload = (
        b'{"RecordType":"Bounce","Type":"HardBounce","ID":"12345",'
        b'"Email":"dead@example.com","Description":"mailbox does not exist"}'
    )
    result = await webhook_routes.postmark_delivery_event(
        _WebhookRequest(payload, _SECRET), db=_WebhookDB()
    )

    assert result["status"] == "applied"
    assert result["reason"] == "hard_bounce"
    assert webhook_env == [("dead@example.com", "hard_bounce")]


@pytest.mark.asyncio
async def test_spam_complaint_suppresses_the_address(webhook_env):
    payload = b'{"RecordType":"SpamComplaint","ID":"777","Email":"angry@example.com"}'
    result = await webhook_routes.postmark_delivery_event(
        _WebhookRequest(payload, _SECRET), db=_WebhookDB()
    )

    assert result["reason"] == "spam_complaint"
    assert webhook_env == [("angry@example.com", "spam_complaint")]


@pytest.mark.asyncio
async def test_transient_bounce_never_suppresses(webhook_env):
    """A full mailbox must not cost a user their password reset."""
    payload = (
        b'{"RecordType":"Bounce","Type":"Transient","ID":"999",'
        b'"Email":"busy@example.com"}'
    )
    result = await webhook_routes.postmark_delivery_event(
        _WebhookRequest(payload, _SECRET), db=_WebhookDB()
    )

    assert result["status"] == "ignored"
    assert webhook_env == []


@pytest.mark.asyncio
async def test_redelivered_event_is_applied_only_once(webhook_env):
    payload = (
        b'{"RecordType":"Bounce","Type":"HardBounce","ID":"12345",'
        b'"Email":"dead@example.com"}'
    )
    db = _WebhookDB()

    first = await webhook_routes.postmark_delivery_event(
        _WebhookRequest(payload, _SECRET), db=db
    )
    second = await webhook_routes.postmark_delivery_event(
        _WebhookRequest(payload, _SECRET), db=db
    )

    assert first["status"] == "applied"
    assert second["status"] == "duplicate"
    # The second delivery must not re-suppress an address an operator may have
    # released in between.
    assert len(webhook_env) == 1


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(webhook_env):
    with pytest.raises(HTTPException) as exc:
        await webhook_routes.postmark_delivery_event(
            _WebhookRequest(b"{}", "wrong-secret"), db=_WebhookDB()
        )
    assert exc.value.status_code == 401
    assert webhook_env == []


@pytest.mark.asyncio
async def test_missing_credentials_are_rejected(webhook_env):
    with pytest.raises(HTTPException) as exc:
        await webhook_routes.postmark_delivery_event(
            _WebhookRequest(b"{}", None), db=_WebhookDB()
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unconfigured_webhook_refuses_rather_than_accepting_anything(
    monkeypatch,
):
    monkeypatch.setattr(webhook_routes.settings, "PLATFORM_EMAIL_WEBHOOK_SECRET", "")
    with pytest.raises(HTTPException) as exc:
        await webhook_routes.postmark_delivery_event(
            _WebhookRequest(b"{}", "anything"), db=_WebhookDB()
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_malformed_payload_is_rejected(webhook_env):
    with pytest.raises(HTTPException) as exc:
        await webhook_routes.postmark_delivery_event(
            _WebhookRequest(b"not json", _SECRET), db=_WebhookDB()
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_event_without_a_recipient_is_acknowledged_not_applied(webhook_env):
    payload = b'{"RecordType":"Bounce","Type":"HardBounce","ID":"1"}'
    result = await webhook_routes.postmark_delivery_event(
        _WebhookRequest(payload, _SECRET), db=_WebhookDB()
    )

    assert result["status"] == "ignored"
    assert webhook_env == []
