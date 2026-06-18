import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import LegacyCallRecord, PartnerRotationState
from app.models.tenant import Tenant
from app.models.task import Task
from app.models.user import User
from app.models.plugin import Matter
from app.services.intake_archive_import import (
    import_legacy_call_csv,
    normalize_phone,
    parse_legacy_call_csv,
)


def test_normalize_phone_strips_formatting_and_country_code():
    assert normalize_phone("+1 (701) 555-1212") == "7015551212"
    assert normalize_phone("701.555.1212") == "7015551212"
    assert normalize_phone("") is None


def test_parse_legacy_call_csv_validates_duplicates_and_dates():
    preview = parse_legacy_call_csv(
        "id,name,phone,date,case_type,attorney,notes\n"
        "1,Jane Doe,(701) 555-1212,2024-01-02,divorce,Jim,prior consult\n"
        "1,Duplicate,(701) 555-1212,2024-01-03,divorce,Jim,duplicate\n"
        "2,Bad Date,7015559999,not-a-date,criminal,Ada,bad date\n"
    )

    assert preview.total_rows == 3
    assert preview.valid_rows == 2
    assert preview.duplicate_source_row_ids == ["1"]
    assert "invalid call_date" in preview.errors[0]
    assert preview.sample[0].normalized_phone == "7015551212"


def test_parse_legacy_call_csv_preserves_nameless_archive_rows():
    preview = parse_legacy_call_csv(
        "id,name,phone,date,reason\n"
        "1,,,2024-01-02 09:30:00,Transferred to queue\n"
    )

    assert preview.total_rows == 1
    assert preview.valid_rows == 1
    assert preview.errors == []
    assert preview.sample[0].caller_name is None
    assert preview.sample[0].purpose == "Transferred to queue"


@pytest.mark.asyncio
async def test_legacy_import_dashboard_search_promote_and_convert_smoke(
    tmp_path, client, db_session, test_tenant
):
    csv_path = tmp_path / "legacy-dashboard-calls.csv"
    csv_path.write_text(
        "source_row_id,caller_name,phone,call_date,purpose,prior_attorney_name,notes,"
        "legacy_call_id,legacy_assigned_to_id\n"
        "1001,John Doe,,2024-01-02 09:30:00,Worked with Jim before,Jim Partner,"
        "Assigned because prior history,1001,42\n"
        "1002,Jane Doe,,2024-01-03 10:45:00,Needs divorce attorney,,No prior history,1002,\n",
        encoding="utf-8",
    )

    result = await import_legacy_call_csv(
        db_session,
        tenant_id=test_tenant.id,
        csv_path=csv_path,
        source_system="professional_services_dashboard",
        dry_run=False,
    )

    assert result.inserted_rows == 2
    assert result.skipped_existing_rows == 0
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LegacyCallRecord)
            .where(LegacyCallRecord.tenant_id == test_tenant.id)
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Contact).where(Contact.tenant_id == test_tenant.id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Lead).where(Lead.tenant_id == test_tenant.id)
        )
        == 0
    )

    history = await client.get("/api/intake/dashboard/search", params={"q": "John Doe"})
    assert history.status_code == 200
    history_data = history.json()
    assert history_data["history_found"] is True
    assert history_data["recommended_attorney_name"] == "Jim Partner"
    assert history_data["results"][0]["result_type"] == "legacy_call"

    call = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Jane Doe",
            "phone": "(701) 555-2222",
            "practice_area": "divorce",
            "purpose": "Needs divorce attorney; no prior history",
            "outcome": "create_lead",
            "qualified": True,
        },
    )
    assert call.status_code == 201
    call_data = call.json()
    assert call_data["created_lead"] is True
    prospect = await db_session.get(Contact, uuid.UUID(call_data["contact_id"]))
    assert prospect.contact_type == "prospect"

    search_after_promote = await client.get(
        "/api/intake/dashboard/search", params={"phone": "(701) 555-2222"}
    )
    assert search_after_promote.status_code == 200
    assert {item["result_type"] for item in search_after_promote.json()["results"]} >= {
        "contact",
        "lead",
    }

    convert = await client.post(
        f"/api/intake/{call_data['lead_id']}/convert",
        json={
            "matter_name": "Jane Doe Divorce",
            "matter_type": "domestic",
            "role": "Petitioner",
            "jurisdiction": "ND",
            "counterparty": "John Doe",
        },
    )
    assert convert.status_code == 200
    matter_id = uuid.UUID(convert.json()["matter_id"])

    lead = await db_session.get(Lead, uuid.UUID(call_data["lead_id"]))
    client_contact = await db_session.get(Contact, uuid.UUID(call_data["contact_id"]))
    matter = await db_session.get(Matter, matter_id)
    assert client_contact.contact_type == "client"
    assert lead.status == "matter_opened"
    assert lead.matter_id == matter.id
    assert matter.client_contact_id == uuid.UUID(call_data["contact_id"])
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LegacyCallRecord)
            .where(LegacyCallRecord.tenant_id == test_tenant.id)
        )
        == 2
    )


@pytest.mark.asyncio
async def test_dashboard_search_returns_current_and_legacy_history_tenant_scoped(
    client, db_session, test_tenant, test_user
):
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="John",
        last_name="Doe",
        phone="(701) 555-1111",
        created_by_user_id=test_user.id,
    )
    legacy = LegacyCallRecord(
        tenant_id=test_tenant.id,
        source_system="legacy_csv",
        source_row_id="old-1",
        caller_name="John Doe",
        caller_phone="701-555-1111",
        normalized_phone="7015551111",
        practice_area="criminal",
        purpose="Worked with Jim before",
        prior_attorney_name="Jim Partner",
    )
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other.example",
        billing_tier="payg",
        is_active=True,
    )
    other_legacy = LegacyCallRecord(
        tenant_id=other_tenant.id,
        source_system="legacy_csv",
        source_row_id="other-1",
        caller_name="John Doe Secret",
        normalized_phone="7015551111",
        purpose="Should not leak",
    )
    db_session.add_all([contact, legacy, other_tenant, other_legacy])
    await db_session.commit()

    resp = await client.get(
        "/api/intake/dashboard/search",
        params={"phone": "(701) 555-1111"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["history_found"] is True
    assert data["recommended_attorney_name"] is None
    assert data["identity_warning"]
    titles = [item["title"] for item in data["results"]]
    assert "John Doe" in titles
    assert "John Doe Secret" not in titles
    legacy_result = next(item for item in data["results"] if item["result_type"] == "legacy_call")
    assert legacy_result["metadata"]["phone_only_match"] is True

    name_resp = await client.get(
        "/api/intake/dashboard/search",
        params={"q": "John Doe"},
    )
    assert name_resp.status_code == 200
    assert name_resp.json()["recommended_attorney_name"] == "Jim Partner"


@pytest.mark.asyncio
async def test_dashboard_call_can_create_qualified_lead_and_log_call(
    client, db_session, test_tenant
):
    resp = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Jane Doe",
            "phone": "(701) 555-2222",
            "practice_area": "divorce",
            "purpose": "Needs divorce attorney; no prior history",
            "outcome": "create_lead",
            "qualified": True,
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["created_lead"] is True
    assert data["lead_id"]
    assert data["communication_id"]

    lead = await db_session.get(Lead, uuid.UUID(data["lead_id"]))
    contact = await db_session.get(Contact, uuid.UUID(data["contact_id"]))
    assert lead.status == "qualified"
    assert lead.practice_area == "divorce"
    assert contact.display_name == "Jane Doe"


@pytest.mark.asyncio
async def test_recent_callers_returns_recent_dashboard_calls_tenant_scoped(
    client, db_session, test_tenant, test_user
):
    older = datetime.now(timezone.utc) - timedelta(hours=2)
    newer = datetime.now(timezone.utc) - timedelta(minutes=5)
    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="partner@testfirm.com",
        full_name="Partner User",
        role="admin",
        is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Doe",
        phone="701-555-2222",
        created_by_user_id=test_user.id,
    )
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other-recent.example",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add_all([partner, contact, other_tenant])
    await db_session.flush()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="qualified",
        practice_area="divorce",
        description="Needs divorce attorney",
        assigned_to_user_id=partner.id,
        created_by_user_id=test_user.id,
    )
    db_session.add(lead)
    await db_session.flush()
    db_session.add_all(
        [
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Inbound call: Older Caller",
                summary="Older call reason",
                body="Older call reason\nPractice area: divorce\nNotes: left message",
                participants={
                    "caller_name": "Older Caller",
                    "phone": "701-555-1111",
                    "normalized_phone": "7015551111",
                },
                created_by_user_id=test_user.id,
                occurred_at=older,
            ),
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Inbound call: Jane Doe",
                summary="Needs divorce attorney",
                body="Needs divorce attorney\nPractice area: divorce\nNotes: urgent",
                participants={
                    "caller_name": "Jane Doe",
                    "phone": "701-555-2222",
                    "normalized_phone": "7015552222",
                },
                contact_id=contact.id,
                created_by_user_id=test_user.id,
                occurred_at=newer,
            ),
            Task(
                tenant_id=test_tenant.id,
                title="Urgent intake follow-up: Jane Doe",
                description="Call Jane Doe back.",
                task_type="follow_up",
                status="completed",
                priority="urgent",
                due_date=date.today(),
                completed_at=datetime.now(timezone.utc),
                contact_id=contact.id,
                assigned_to_user_id=partner.id,
                created_by_user_id=test_user.id,
                source="intake_dashboard",
                external_ref=f"intake-dashboard:lead:{lead.id}:follow-up",
            ),
            CommunicationLog(
                tenant_id=other_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Inbound call: Other Tenant",
                summary="Should not leak",
                participants={"caller_name": "Other Tenant"},
                occurred_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get("/api/intake/dashboard/recent-callers", params={"limit": 10})

    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 10
    assert [item["caller_name"] for item in data["callers"]] == [
        "Jane Doe",
        "Older Caller",
    ]
    latest = data["callers"][0]
    assert latest["phone"] == "701-555-2222"
    assert latest["normalized_phone"] == "7015552222"
    assert latest["practice_area"] == "divorce"
    assert latest["purpose"] == "Needs divorce attorney"
    assert latest["notes"] == "urgent"
    assert latest["contact_id"] == str(contact.id)
    assert latest["lead_id"] == str(lead.id)
    assert latest["lead_status"] == "qualified"
    assert latest["assigned_to_user_id"] == str(partner.id)
    assert latest["assigned_to_name"] == "Partner User"
    assert latest["task_status"] == "completed"
    assert latest["task_priority"] == "urgent"
    assert latest["task_due_date"] == date.today().isoformat()
    assert latest["task_completed_at"]
    assert latest["created_by_name"] == (test_user.full_name or test_user.email)

    invalid = await client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 25}
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_assignment_availability_reports_missing_and_general_rotation(
    client, db_session, test_tenant, test_user
):
    missing = await client.get(
        "/api/intake/dashboard/assignment-availability",
        params={"practice_area": "criminal"},
    )
    assert missing.status_code == 200
    missing_data = missing.json()
    assert missing_data["practice_area"] == "criminal"
    assert missing_data["can_assign"] is False
    assert missing_data["reason"] == "No practice-specific or firm-wide rotation rule"

    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="general-rotation@testfirm.com",
        full_name="General Rotation Partner",
        role="admin",
        is_active=True,
    )
    rule = PartnerRotationState(
        tenant_id=test_tenant.id,
        practice_area="general",
        eligible_user_ids=[str(partner.id)],
        created_by_user_id=test_user.id,
    )
    db_session.add_all([partner, rule])
    await db_session.commit()

    available = await client.get(
        "/api/intake/dashboard/assignment-availability",
        params={"practice_area": "criminal"},
    )
    assert available.status_code == 200
    data = available.json()
    assert data["can_assign"] is True
    assert data["rule_practice_area"] == "general"
    assert data["eligible_count"] == 1


@pytest.mark.asyncio
async def test_assign_next_partner_wraps_practice_rotation(
    client, db_session, test_tenant, test_user
):
    partner_a = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="ada@testfirm.com",
        full_name="Ada Partner",
        role="admin",
        is_active=True,
    )
    partner_b = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="jim@testfirm.com",
        full_name="Jim Partner",
        role="admin",
        is_active=True,
    )
    contact_1 = Contact(
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Doe",
        created_by_user_id=test_user.id,
    )
    contact_2 = Contact(
        tenant_id=test_tenant.id,
        first_name="Janet",
        last_name="Doe",
        created_by_user_id=test_user.id,
    )
    db_session.add_all([partner_a, partner_b, contact_1, contact_2])
    await db_session.flush()
    lead_1 = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact_1.id,
        practice_area="divorce",
        created_by_user_id=test_user.id,
    )
    lead_2 = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact_2.id,
        practice_area="divorce",
        created_by_user_id=test_user.id,
    )
    rule = PartnerRotationState(
        tenant_id=test_tenant.id,
        practice_area="divorce",
        eligible_user_ids=[str(partner_a.id), str(partner_b.id)],
        last_assigned_user_id=partner_b.id,
        created_by_user_id=test_user.id,
    )
    db_session.add_all([lead_1, lead_2, rule])
    await db_session.commit()

    first = await client.post(f"/api/intake/dashboard/leads/{lead_1.id}/assign-next")
    second = await client.post(f"/api/intake/dashboard/leads/{lead_2.id}/assign-next")

    assert first.status_code == 200
    assert first.json()["assigned_to_user_id"] == str(partner_a.id)
    assert first.json()["assigned_to_name"] == "Ada Partner"
    assert first.json()["task_id"]
    assert second.status_code == 200
    assert second.json()["assigned_to_user_id"] == str(partner_b.id)
    first_task = await db_session.get(Task, uuid.UUID(first.json()["task_id"]))
    assert first_task.assigned_to_user_id == partner_a.id
    assert first_task.priority == "urgent"
    assert first_task.task_type == "follow_up"
    assert first_task.contact_id == contact_1.id


@pytest.mark.asyncio
async def test_assign_next_partner_falls_back_to_firmwide_general_rotation(
    client, db_session, test_tenant, test_user
):
    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="general@testfirm.com",
        full_name="General Partner",
        role="admin",
        is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Casey",
        last_name="Caller",
        phone="701-555-3030",
        created_by_user_id=test_user.id,
    )
    db_session.add_all([partner, contact])
    await db_session.flush()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        practice_area="criminal",
        description="Needs criminal defense consult",
        created_by_user_id=test_user.id,
    )
    rule = PartnerRotationState(
        tenant_id=test_tenant.id,
        practice_area="general",
        eligible_user_ids=[str(partner.id)],
        created_by_user_id=test_user.id,
    )
    db_session.add_all([lead, rule])
    await db_session.commit()

    resp = await client.post(f"/api/intake/dashboard/leads/{lead.id}/assign-next")

    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned_to_user_id"] == str(partner.id)
    assert data["practice_area"] == "general"
    assert data["task_id"]
    task = await db_session.get(Task, uuid.UUID(data["task_id"]))
    assert task.assigned_to_user_id == partner.id
    assert task.priority == "urgent"
    assert task.external_ref == f"intake-dashboard:lead:{lead.id}:follow-up"


@pytest.mark.asyncio
async def test_prior_history_match_routes_lead_to_partner_task(
    client, db_session, test_tenant
):
    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="jim@testfirm.com",
        full_name="Jim Partner",
        role="admin",
        is_active=True,
    )
    legacy = LegacyCallRecord(
        tenant_id=test_tenant.id,
        source_system="legacy_csv",
        source_row_id="prior-1",
        caller_name="John Doe",
        practice_area="criminal",
        purpose="Worked with Jim before",
        prior_attorney_name="Jim Partner",
    )
    db_session.add_all([partner, legacy])
    await db_session.commit()

    search = await client.get("/api/intake/dashboard/search", params={"q": "John Doe"})
    assert search.status_code == 200
    search_data = search.json()
    assert search_data["recommended_attorney_name"] == "Jim Partner"
    assert search_data["recommended_attorney_user_id"] == str(partner.id)

    call = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "John Doe",
            "practice_area": "criminal",
            "purpose": "Needs criminal defense; worked with Jim before",
            "outcome": "create_lead",
            "qualified": True,
            "assigned_to_user_id": search_data["recommended_attorney_user_id"],
        },
    )

    assert call.status_code == 201
    call_data = call.json()
    assert call_data["task_id"]
    lead = await db_session.get(Lead, uuid.UUID(call_data["lead_id"]))
    task = await db_session.get(Task, uuid.UUID(call_data["task_id"]))
    assert lead.assigned_to_user_id == partner.id
    assert task.assigned_to_user_id == partner.id
    assert task.priority == "urgent"
