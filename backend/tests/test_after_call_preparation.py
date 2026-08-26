import json
import uuid
from datetime import datetime, timezone

import pytest

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.prospect_follow_through import ProspectFollowThrough
from app.services.after_call_preparation import (
    _baseline,
    _contact_name,
    prepare_after_call_handoff,
)
from app.services.ai_request_broker import (
    AIRequestDenied,
    AIRequestError,
    AIResult,
    AITransport,
)
from app.services.llm_routing import LLMRoute


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


def test_baseline_fills_all_missing_fields_and_contact_name_fallback():
    contact = type(
        "ContactStub",
        (),
        {
            "display_name": "",
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "email": None,
            "phone": None,
        },
    )()
    lead = type(
        "LeadStub",
        (),
        {"description": "", "practice_area": None, "conflict_check_status": "not_run"},
    )()
    result = _baseline(lead=lead, contact=contact, communication=None)
    assert _contact_name(contact) == ""
    assert len(result["missing_information"]) == 5


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


@pytest.mark.asyncio
async def test_preparation_requires_tenant_scoped_lead_and_active_contact():
    tenant_id, actor_id, _lead, _contact, _communication, prospect = _subject()
    with pytest.raises(ValueError, match="tenant-scoped lead"):
        await prepare_after_call_handoff(
            _DB([None]), tenant_id=tenant_id, actor_user_id=actor_id, prospect=prospect
        )
    bad_contact = _DB([_lead, None])
    with pytest.raises(ValueError, match="Lead contact"):
        await prepare_after_call_handoff(
            bad_contact, tenant_id=tenant_id, actor_user_id=actor_id, prospect=prospect
        )


@pytest.mark.asyncio
async def test_preparation_handles_provider_error_and_missing_call():
    tenant_id, actor_id, lead, contact, _communication, prospect = _subject()
    prospect.intake_communication_id = None

    class FailingBroker:
        async def execute(self, *_args):
            raise AIRequestError("provider unavailable")

    result = await prepare_after_call_handoff(
        _DB([lead, contact]),
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        prospect=prospect,
        broker=FailingBroker(),
    )
    assert result["inference_error"] == "ai_request_failed"
    assert result["inference"]["error_code"] == "ai_request_failed"
    assert "Conflict review result" in result["suggestion"]["missing_information"]


@pytest.mark.asyncio
async def test_preparation_persists_successful_structured_inference():
    tenant_id, actor_id, lead, contact, communication, prospect = _subject()

    class GoodBroker:
        async def execute(self, _db, request):
            assert request.surface == "after_call_prepare"
            assert contact.email not in request.messages[0]["content"]
            return AIResult(
                value={
                    "brief": "Reviewed",
                    "missing_information": [],
                    "outreach_draft": "Draft",
                    "suggested_next_action": "Call",
                    "needs_attorney_review": True,
                },
                request_id="req-1",
                provider_request_id="provider-1",
                route=LLMRoute(
                    requested_route="standard",
                    resolved_route="standard",
                    gateway_alias="zen",
                ),
                transport=AITransport.CHAT_COMPLETIONS,
                tokens_in=10,
                tokens_out=8,
            )

    result = await prepare_after_call_handoff(
        _DB([lead, contact, communication]),
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        prospect=prospect,
        broker=GoodBroker(),
    )
    assert result["inference_available"] is True
    assert result["inference"]["route"] == "zen"
    assert result["suggestion"]["brief"] == "Reviewed"


@pytest.mark.asyncio
async def test_preparation_converts_unexpected_provider_failure_to_closed_error():
    tenant_id, actor_id, lead, contact, communication, prospect = _subject()

    class ExplodingBroker:
        async def execute(self, *_args):
            raise RuntimeError("boom")

    result = await prepare_after_call_handoff(
        _DB([lead, contact, communication]),
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        prospect=prospect,
        broker=ExplodingBroker(),
    )
    assert result["inference_error"] == "assistant_unavailable"
