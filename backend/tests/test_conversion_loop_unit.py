from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

import app.routers.conversion_loop as conversion
from app.routers.conversion_loop import _attribution, _validate_answers
from app.schemas.conversion_loop import (
    BookingCreate,
    ConsentUpdate,
    FollowUpCreate,
    IntakeSubmissionCreate,
    TriageDecision,
)
from app.services.email import EmailDeliveryResult


class FakeDB:
    def __init__(self, scalar_results=()):
        self.scalar_results = list(scalar_results)
        self.added = []
        self.commit = AsyncMock()
        self.flush = AsyncMock(side_effect=self._flush)

    async def scalar(self, _query):
        return self.scalar_results.pop(0) if self.scalar_results else None

    async def execute(self, _query):
        return SimpleNamespace(all=lambda: [])

    async def scalars(self, _query):
        return SimpleNamespace(all=lambda: [])

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()

    async def _flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = uuid.uuid4()

    async def refresh(self, _obj):
        return None


TENANT_ID = uuid.uuid4()
LEAD_ID = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4(), tenant_id=TENANT_ID)
FORM = SimpleNamespace(
    id=uuid.uuid4(),
    tenant_id=TENANT_ID,
    slug="family",
    name="Family intake",
    version=1,
    schema_json={
        "fields": [{"name": "email", "required": True}],
        "availability": [
            {
                "start_at": "2026-09-01T15:00:00+00:00",
                "end_at": "2026-09-01T15:30:00+00:00",
            }
        ],
    },
)


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="198.51.100.10"))


def _lead(**overrides):
    values = dict(
        id=LEAD_ID,
        tenant_id=TENANT_ID,
        contact_id=uuid.uuid4(),
        source="google",
        status="new",
        conflict_check_status=None,
        declined_reason=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_conditional_required_fields_only_apply_when_visible():
    schema = {
        "fields": [
            {"name": "matter_type", "required": True},
            {
                "name": "employer",
                "required": True,
                "show_if": {"field": "matter_type", "value": "employment"},
            },
        ]
    }
    _validate_answers(schema, {"matter_type": "family"})
    with pytest.raises(Exception, match="employer is required"):
        _validate_answers(schema, {"matter_type": "employment"})


def test_attribution_is_allowlisted_and_bounded():
    result = _attribution(
        {
            "source": "google",
            "campaign": "spring",
            "secret": "drop",
            "referrer": "x" * 800,
        }
    )
    assert result == {"source": "google", "campaign": "spring", "referrer": "x" * 500}


def test_invalid_schema_field_is_rejected():
    with pytest.raises(Exception, match="invalid field"):
        _validate_answers({"fields": [{"name": "bad field"}]}, {})


@pytest.mark.asyncio
async def test_public_form_and_availability_are_slug_scoped(monkeypatch):
    monkeypatch.setattr(conversion, "_public_form", AsyncMock(return_value=FORM))
    assert (await conversion.get_public_form("family", FakeDB()))["version"] == 1
    assert (await conversion.public_availability("family", FakeDB()))["slots"]

    malformed = SimpleNamespace(
        **{**FORM.__dict__, "schema_json": {"availability": {}}}
    )
    monkeypatch.setattr(conversion, "_public_form", AsyncMock(return_value=malformed))
    assert (await conversion.public_availability("family", FakeDB())) == {"slots": []}


@pytest.mark.asyncio
async def test_public_submission_honeypot_and_idempotent_replay(monkeypatch):
    monkeypatch.setattr(conversion, "_public_form", AsyncMock(return_value=FORM))
    honeypot = IntakeSubmissionCreate(
        answers={"email": "a@example.com"}, idempotency_key="honeypot-1", website="bot"
    )
    with pytest.raises(Exception, match="rejected"):
        await conversion.submit_public_form("family", honeypot, _request(), FakeDB())

    existing = SimpleNamespace(id=uuid.uuid4(), lead_id=LEAD_ID)
    replay = IntakeSubmissionCreate(
        answers={"email": "a@example.com"}, idempotency_key="replay-1"
    )
    result = await conversion.submit_public_form(
        "family", replay, _request(), FakeDB([existing])
    )
    assert result["replayed"] is True


@pytest.mark.asyncio
async def test_public_submission_creates_lead_consent_attribution(monkeypatch):
    monkeypatch.setattr(conversion, "_public_form", AsyncMock(return_value=FORM))
    body = IntakeSubmissionCreate(
        answers={"email": "A@example.com"},
        attribution={"source": "google", "secret": "remove"},
        idempotency_key="accepted-1",
        email_consent=True,
        disclosure_version="v1",
    )
    result = await conversion.submit_public_form("family", body, _request(), FakeDB())
    assert result["replayed"] is False


@pytest.mark.asyncio
async def test_public_booking_rejects_missing_slot_and_supports_replay(monkeypatch):
    monkeypatch.setattr(conversion, "_public_form", AsyncMock(return_value=FORM))
    start = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    body = BookingCreate(
        lead_id=LEAD_ID,
        start_at=start,
        end_at=datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc),
        idempotency_key="book-001",
    )
    with pytest.raises(Exception, match="not available"):
        await conversion.book_public("family", body, FakeDB([_lead()]))

    existing = SimpleNamespace(
        id=uuid.uuid4(), status="booked", reminder_status="pending"
    )
    valid_body = body.model_copy(
        update={
            "start_at": datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
            "end_at": datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc),
        }
    )
    replay = await conversion.book_public(
        "family", valid_body, FakeDB([_lead(), existing])
    )
    assert replay["replayed"] is True


@pytest.mark.asyncio
async def test_public_booking_creates_local_event_and_appointment(monkeypatch):
    monkeypatch.setattr(conversion, "_public_form", AsyncMock(return_value=FORM))
    start = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    body = BookingCreate(
        lead_id=LEAD_ID,
        start_at=start,
        end_at=datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc),
        idempotency_key="book-002",
    )
    db = FakeDB([_lead(), None, None])
    result = await conversion.book_public("family", body, db)
    assert result["status"] == "booked"
    assert any(type(row).__name__ == "ScheduledEvent" for row in db.added)


@pytest.mark.asyncio
async def test_consent_and_triage_cover_revocation_and_review(monkeypatch):
    monkeypatch.setattr(conversion, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(conversion, "record_operator_audit", AsyncMock())
    db = FakeDB([_lead(), None])
    revoked = await conversion.update_consent(
        LEAD_ID,
        ConsentUpdate(disclosure_version="v1"),
        _request(),
        USER,
        db,
    )
    assert revoked["revoked_at"] is not None

    monkeypatch.setattr(conversion, "set_tenant_context", AsyncMock())
    with pytest.raises(Exception, match="Lead not found"):
        await conversion.triage(
            LEAD_ID, TriageDecision(decision="clear"), USER, FakeDB([None])
        )
    for decision in ("clear", "hold", "decline"):
        lead = _lead()
        result = await conversion.triage(
            LEAD_ID,
            TriageDecision(decision=decision, note="review"),
            USER,
            FakeDB([lead]),
        )
        assert result["lead_id"] == str(LEAD_ID)


@pytest.mark.asyncio
async def test_follow_up_enforces_consent_sms_and_replay(monkeypatch):
    monkeypatch.setattr(conversion, "set_tenant_context", AsyncMock())
    body = FollowUpCreate(
        channel="email", subject="Hello", body="Body", idempotency_key="follow-001"
    )
    lead = _lead()
    contact = SimpleNamespace(id=lead.contact_id, email="a@example.com")
    consent = SimpleNamespace(email_allowed=True, sms_allowed=False, revoked_at=None)
    with pytest.raises(Exception, match="SMS"):
        await conversion.send_follow_up(
            LEAD_ID,
            body.model_copy(update={"channel": "sms"}),
            USER,
            FakeDB([lead, contact, consent]),
        )
    replay_log = SimpleNamespace(status="sent")
    assert (
        await conversion.send_follow_up(
            LEAD_ID, body, USER, FakeDB([lead, contact, consent, replay_log])
        )
    )["replayed"]

    monkeypatch.setattr(
        conversion.email_service,
        "send_email",
        AsyncMock(return_value=EmailDeliveryResult.SENT),
    )
    result = await conversion.send_follow_up(
        LEAD_ID, body, USER, FakeDB([lead, contact, consent, None])
    )
    assert result["status"] == "sent"


@pytest.mark.asyncio
async def test_follow_up_records_provider_failure_and_reminder_guards(monkeypatch):
    monkeypatch.setattr(conversion, "set_tenant_context", AsyncMock())
    lead = _lead()
    contact = SimpleNamespace(id=lead.contact_id, email="a@example.com")
    consent = SimpleNamespace(email_allowed=True, sms_allowed=False, revoked_at=None)
    body = FollowUpCreate(
        channel="email", subject="Hello", body="Body", idempotency_key="follow-002"
    )
    monkeypatch.setattr(
        conversion.email_service,
        "send_email",
        AsyncMock(return_value=EmailDeliveryResult.FAILED),
    )
    with pytest.raises(Exception, match="did not accept"):
        await conversion.send_follow_up(
            LEAD_ID, body, USER, FakeDB([lead, contact, consent, None])
        )

    appointment = SimpleNamespace(id=uuid.uuid4(), lead_id=LEAD_ID, tenant_id=TENANT_ID)
    with pytest.raises(Exception, match="not consented"):
        await conversion.send_appointment_reminder(
            appointment.id, USER, FakeDB([appointment, None, None, None])
        )


@pytest.mark.asyncio
async def test_funnel_abandonment_and_recovery_are_review_only(monkeypatch):
    monkeypatch.setattr(conversion, "set_tenant_context", AsyncMock())
    db = FakeDB()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [("intake_submitted", 2)])
    )
    assert (await conversion.funnel(USER, db))["events"]["intake_submitted"] == 2
    db.scalars = AsyncMock(
        return_value=SimpleNamespace(
            all=lambda: [_lead(source="website", created_at=datetime.now(timezone.utc))]
        )
    )
    assert len((await conversion.abandoned_leads(USER, db))["candidates"]) == 1
    assert (await conversion.recover_lead(LEAD_ID, USER, FakeDB([_lead()])))[
        "requires_authored_follow_up"
    ]
