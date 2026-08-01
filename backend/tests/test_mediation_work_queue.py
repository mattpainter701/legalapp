from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.routers.mediation import _matter_slug
from app.schemas.mediation import MediationCaseCreate, MediationNextActionCreate
from app.services.mediation_service import case_to_response


def test_mediation_create_parses_operational_fields():
    body = MediationCaseCreate(
        case_name="Rivera court-appointed mediation",
        jurisdiction="Ohio",
        court="Franklin County Court of Common Pleas",
        case_number="24 DR 00123",
        fixed_fee="750.00",
        waiting_on="Respondent financial affidavit",
        next_action="Send scheduling poll",
        next_action_due="2026-08-03",
    )

    assert body.fixed_fee == Decimal("750.00")
    assert body.next_action_due == date(2026, 8, 3)
    assert body.mediation_stage == "New Referral"


def test_mediation_next_action_defaults_to_completing_current_task():
    body = MediationNextActionCreate(title="Draft mediator report")

    assert body.priority == "medium"
    assert body.complete_current is True


def test_matter_slug_is_readable_and_unique():
    first = _matter_slug("Rivera v. Rivera — Mediation")
    second = _matter_slug("Rivera v. Rivera — Mediation")

    assert first.startswith("rivera-v-rivera-mediation-")
    assert first != second


def test_case_response_exposes_shared_work_queue_task():
    now = datetime.now(timezone.utc)
    matter_id = uuid4()
    case = SimpleNamespace(
        id=uuid4(),
        case_name="Rivera v. Rivera",
        title="Rivera v. Rivera",
        party_a="Alex Rivera",
        party_b="Jordan Rivera",
        dispute_type="domestic",
        mediation_stage="Scheduling",
        mediator="Morgan Lee",
        attorney=None,
        claim_value=None,
        jurisdiction="Ohio",
        court="Franklin County Court of Common Pleas",
        case_number="24 DR 00123",
        waiting_on="Counsel availability",
        fixed_fee=Decimal("750.00"),
        scheduled_session=None,
        confidentiality_signed=False,
        status="active",
        summary=None,
        matter_id=matter_id,
        client_contact_id=None,
        case_parties=[],
        assets=[],
        created_at=now,
        updated_at=now,
    )
    task = SimpleNamespace(
        title="Send scheduling poll",
        due_date=date(2026, 8, 3),
        priority="high",
    )

    response = case_to_response(case, task)

    assert response.matter_id == str(matter_id)
    assert response.next_action == "Send scheduling poll"
    assert response.next_action_due == date(2026, 8, 3)
    assert response.fixed_fee == Decimal("750.00")
