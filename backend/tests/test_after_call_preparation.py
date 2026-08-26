import json
import uuid
from datetime import datetime, timezone

import pytest

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.prospect_follow_through import ProspectFollowThrough
from app.services.after_call_preparation import prepare_after_call_handoff
from app.services.ai_request_broker import AIRequestDenied


class _DB:
    def __init__(self, values):
        self.values = list(values)
        self.commits = 0

    async def scalar(self, _query):
        return self.values.pop(0)

    async def commit(self):
        self.commits += 1


class _DeniedBroker:
    def __init__(self):
        self.calls = 0
        self.request = None

    async def execute(self, _db, _request):
        self.calls += 1
        self.request = _request
        raise AIRequestDenied("route data policy blocks prospect inference")


def _subject():
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        first_name="Alex",
        last_name="Prospect",
        email="alex@example.invalid",
        phone=None,
        is_active=True,
    )
    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        contact_id=contact.id,
        status="new",
        practice_area="estate_planning",
        description="Needs help updating an estate plan",
        conflict_check_status="not_run",
        created_by_user_id=actor_id,
        updated_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    communication = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        direction="inbound",
        channel="call",
        status="logged",
        subject="Inbound call: Alex Prospect",
        body="Caller wants a consultation next week.",
        summary="Estate plan update",
        contact_id=contact.id,
        created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    prospect = ProspectFollowThrough(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        lead_id=lead.id,
        contact_id=contact.id,
        intake_communication_id=communication.id,
        idempotency_key=f"lead:{lead.id}",
        status="attorney_review",
        version=1,
        created_by_user_id=actor_id,
    )
    return tenant_id, actor_id, lead, contact, communication, prospect


@pytest.mark.asyncio
async def test_route_denial_degrades_to_useful_deterministic_handoff():
    tenant_id, actor_id, lead, contact, communication, prospect = _subject()
    db = _DB([lead, contact, communication])
    broker = _DeniedBroker()

    result = await prepare_after_call_handoff(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        prospect=prospect,
        broker=broker,
    )

    assert result["inference_available"] is False
    assert result["inference_error"] == "ai_request_denied"
    assert "Alex Prospect" in result["suggestion"]["brief"]
    assert "Prospect phone number" in result["suggestion"]["missing_information"]
    assert "does not confirm" in result["suggestion"]["outreach_draft"]
    assert result["provenance"]["human_confirmation_required"] is True
    assert prospect.metadata_json["assistant_preparation"] == result
    assert db.commits == 1
    assert broker.calls == 1
    inference_input = json.loads(broker.request.messages[0]["content"])
    serialized = json.dumps(inference_input)
    assert "Alex Prospect" not in serialized
    assert "alex@example.invalid" not in serialized
    assert "conflict_check_status" not in serialized
    assert "assigned_attorney_user_id" not in serialized


@pytest.mark.asyncio
async def test_unchanged_source_uses_persisted_preparation_without_respending():
    tenant_id, actor_id, lead, contact, communication, prospect = _subject()
    first_db = _DB([lead, contact, communication])
    first_broker = _DeniedBroker()
    expected = await prepare_after_call_handoff(
        first_db,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        prospect=prospect,
        broker=first_broker,
    )
    second_db = _DB([lead, contact, communication])

    class _MustNotRun:
        async def execute(self, *_args):
            raise AssertionError("cached preparation must not call the model")

    actual = await prepare_after_call_handoff(
        second_db,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        prospect=prospect,
        broker=_MustNotRun(),
    )
    assert actual == expected
    assert second_db.commits == 0
