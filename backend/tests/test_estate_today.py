from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.routers.estates import _estate_to_response


def test_estate_response_prioritizes_overdue_deadline_and_attention_counts():
    now = datetime.now(timezone.utc)
    overdue = SimpleNamespace(
        title="File inventory",
        due_date=date.today() - timedelta(days=2),
        status="pending",
    )
    estate = SimpleNamespace(
        id=uuid4(),
        estate_name="Estate of Morgan Lee",
        title="Estate of Morgan Lee",
        estate_type="probate",
        representative_type=None,
        grantor="Morgan Lee",
        status="in_probate",
        summary=None,
        jurisdiction="Ohio",
        domicile_state="Ohio",
        date_of_death=None,
        court_name="Franklin County Probate Court",
        case_number=None,
        gross_estate_value=None,
        net_estate_value=None,
        matter_id=None,
        client_contact_id=None,
        client=None,
        beneficiaries=[],
        deadlines=[overdue],
        assets=[SimpleNamespace(date_of_death_value=None, current_value=None)],
        liabilities=[SimpleNamespace(status="pending")],
        distributions=[SimpleNamespace(status="planned")],
        events=[],
        created_at=now,
        updated_at=now,
    )

    response = _estate_to_response(estate)

    assert response.next_action == "Complete overdue deadline: File inventory"
    assert response.overdue_deadlines_count == 1
    assert response.unvalued_assets_count == 1
    assert response.unresolved_claims_count == 1
    assert response.pending_distributions_count == 1
    assert "Date of death" in response.missing_facts
    assert response.attention_count == 7
