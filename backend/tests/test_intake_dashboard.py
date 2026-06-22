import uuid
import csv
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import (
    LegacyCallRecord,
    PartnerAssignmentLog,
    PartnerRotationState,
)
from app.models.tenant import Tenant
from app.models.task import Task
from app.models.user import User
from app.models.plugin import Matter
from app.services.intake_archive_import import (
    import_legacy_call_csv,
    normalize_phone,
    parse_legacy_call_csv,
)
from app.services.zoom_phone import import_zoom_phone_records


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
        "id,name,phone,date,reason\n" "1,,,2024-01-02 09:30:00,Transferred to queue\n"
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
            select(func.count())
            .select_from(Contact)
            .where(Contact.tenant_id == test_tenant.id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Lead)
            .where(Lead.tenant_id == test_tenant.id)
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
    legacy_result = next(
        item for item in data["results"] if item["result_type"] == "legacy_call"
    )
    assert legacy_result["metadata"]["phone_only_match"] is True

    name_resp = await client.get(
        "/api/intake/dashboard/search",
        params={"q": "John Doe"},
    )
    assert name_resp.status_code == 200
    assert name_resp.json()["recommended_attorney_name"] == "Jim Partner"


@pytest.mark.asyncio
async def test_dashboard_search_finds_log_only_callers_by_partial_name_and_phone(
    client, db_session, test_tenant, test_user
):
    db_session.add(
        CommunicationLog(
            tenant_id=test_tenant.id,
            direction="inbound",
            channel="call",
            status="logged",
            subject="Inbound call: Jan Patterson",
            summary="Bad husband",
            body="Bad husband\nPractice area: divorce\nNotes: sounded crazy",
            participants={
                "caller_name": "Jan Patterson",
                "phone": "(701) 555-3333",
                "normalized_phone": "7015553333",
            },
            created_by_user_id=test_user.id,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    full_name = await client.get(
        "/api/intake/dashboard/search",
        params={"q": "jan patterson"},
    )
    assert full_name.status_code == 200
    full_name_results = full_name.json()["results"]
    assert any(
        item["result_type"] == "call_log" and item["title"] == "Jan Patterson"
        for item in full_name_results
    )

    partial_name = await client.get(
        "/api/intake/dashboard/search",
        params={"q": "patt"},
    )
    assert partial_name.status_code == 200
    assert any(
        item["result_type"] == "call_log" and item["title"] == "Jan Patterson"
        for item in partial_name.json()["results"]
    )

    partial_phone = await client.get(
        "/api/intake/dashboard/search",
        params={"q": "5553"},
    )
    assert partial_phone.status_code == 200
    assert partial_phone.json()["normalized_phone"] == "5553"
    assert any(
        item["result_type"] == "call_log" and item["phone"] == "(701) 555-3333"
        for item in partial_phone.json()["results"]
    )


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
async def test_zoom_phone_call_history_imports_idempotently_and_reuses_log(
    client, db_session, test_tenant
):
    records = [
        {
            "id": "zoom-call-1",
            "direction": "inbound",
            "caller_name": "Rita Caller",
            "caller_number": "+1 (701) 555-8181",
            "callee_name": "Reception",
            "callee_number": "+1 (701) 555-0100",
            "start_time": "2026-06-22T14:15:00Z",
            "duration": 93,
            "result": "missed",
            "summary": "Caller asked for a family-law consult.",
            "transcript_download_url": "https://zoom.example/transcript",
            "recording_download_url": "https://zoom.example/recording",
            "transcript": "I need help with a custody issue.",
        }
    ]

    first = await import_zoom_phone_records(
        db_session, tenant_id=str(test_tenant.id), records=records
    )
    second = await import_zoom_phone_records(
        db_session, tenant_id=str(test_tenant.id), records=records
    )
    await db_session.commit()

    assert first.imported == 1
    assert second.updated == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(CommunicationLog)
            .where(
                CommunicationLog.tenant_id == test_tenant.id,
                CommunicationLog.external_ref == "zoom_phone:call:zoom-call-1",
            )
        )
        == 1
    )

    queue = await client.get("/api/intake/dashboard/zoom-phone/calls")
    assert queue.status_code == 200
    call_item = queue.json()["calls"][0]
    assert call_item["caller_name"] == "Rita Caller"
    assert call_item["normalized_phone"] == "7015558181"
    assert call_item["transcript_url"] == "https://zoom.example/transcript"

    capture = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "existing_communication_id": call_item["id"],
            "caller_name": "Rita Caller",
            "phone": "(701) 555-8181",
            "practice_area": "family",
            "purpose": "Needs custody consultation",
            "outcome": "create_lead",
            "task_mode": "none",
            "qualified": True,
        },
    )
    assert capture.status_code == 201
    data = capture.json()
    assert data["communication_id"] == call_item["id"]
    assert data["created_lead"] is True

    linked_log = await db_session.get(CommunicationLog, uuid.UUID(call_item["id"]))
    assert linked_log.contact_id == uuid.UUID(data["contact_id"])
    assert linked_log.external_ref == "zoom_phone:call:zoom-call-1"
    assert "Original Zoom Phone details" in linked_log.body


@pytest.mark.asyncio
async def test_dashboard_call_can_assign_general_staff_task_without_lead(
    client, db_session, test_tenant
):
    staff = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="service@testfirm.com",
        full_name="Service Provider",
        role="user",
        is_active=True,
    )
    db_session.add(staff)
    await db_session.commit()

    resp = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Sam Caller",
            "phone": "(701) 555-4444",
            "practice_area": "general",
            "purpose": "Needs a copy provider referral",
            "notes": "Not a legal lead",
            "outcome": "log_only",
            "qualified": False,
            "task_mode": "specific_staff",
            "task_assigned_to_user_id": str(staff.id),
            "task_title": "Route to service provider",
            "task_description": "Send caller details to outside provider.",
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["created_lead"] is False
    assert data["lead_id"] is None
    assert data["task_id"]

    task = await db_session.get(Task, uuid.UUID(data["task_id"]))
    assert task.title == "Route to service provider"
    assert task.assigned_to_user_id == staff.id
    assert task.source == "intake_dashboard"
    assert (
        task.external_ref
        == f"intake-dashboard:call:{data['communication_id']}:general-task"
    )
    assert "Needs a copy provider referral" in task.description

    recent = await client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 10}
    )
    assert recent.status_code == 200
    caller = recent.json()["callers"][0]
    assert caller["caller_name"] == "Sam Caller"
    assert caller["assigned_to_user_id"] == str(staff.id)
    assert caller["assigned_to_name"] == "Service Provider"
    assert caller["task_id"] == data["task_id"]
    assert caller["task_status"] == "pending"

    export = await client.get("/api/intake/dashboard/calls/export")
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    row = next(row for row in rows if row["caller_name"] == "Sam Caller")
    assert row["outcome"] == "log_only"
    assert row["assigned_to_name"] == "Service Provider"
    assert row["task_status"] == "pending"


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

    resp = await client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 10}
    )

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
async def test_export_call_records_filters_dates_and_tenant_scopes(
    client, db_session, test_tenant, test_user
):
    older = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    in_range = datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc)
    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="finance-partner@testfirm.com",
        full_name="Finance Partner",
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
        name="Other Export Firm",
        domain="other-export.example",
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
    completed_at = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Inbound call: Older Caller",
                summary="Older call reason",
                body="Older call reason\nPractice area: criminal\nNotes: old note",
                participants={"caller_name": "Older Caller", "phone": "701-555-1111"},
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
                body="Needs divorce attorney\nPractice area: divorce\nNotes: finance export note",
                participants={
                    "caller_name": "Jane Doe",
                    "phone": "701-555-2222",
                    "normalized_phone": "7015552222",
                },
                contact_id=contact.id,
                created_by_user_id=test_user.id,
                occurred_at=in_range,
            ),
            Task(
                tenant_id=test_tenant.id,
                title="Urgent intake follow-up: Jane Doe",
                description="Call Jane Doe back.",
                task_type="follow_up",
                status="completed",
                priority="urgent",
                due_date=date(2026, 1, 15),
                completed_at=completed_at,
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
                summary="Should not export",
                participants={"caller_name": "Other Tenant"},
                occurred_at=in_range,
            ),
        ]
    )
    await db_session.commit()

    all_resp = await client.get("/api/intake/dashboard/calls/export")
    assert all_resp.status_code == 200
    assert all_resp.headers["content-type"].startswith("text/csv")
    assert "intake-calls-all.csv" in all_resp.headers["content-disposition"]
    all_rows = list(csv.DictReader(io.StringIO(all_resp.text)))
    assert [row["caller_name"] for row in all_rows] == ["Jane Doe", "Older Caller"]
    assert "Other Tenant" not in {row["caller_name"] for row in all_rows}

    range_resp = await client.get(
        "/api/intake/dashboard/calls/export",
        params={"start": "2026-01-10", "end": "2026-01-20"},
    )
    assert range_resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(range_resp.text)))
    assert len(rows) == 1
    row = rows[0]
    assert row["caller_name"] == "Jane Doe"
    assert row["practice_area"] == "divorce"
    assert row["notes"] == "finance export note"
    assert row["outcome"] == "lead"
    assert row["lead_status"] == "qualified"
    assert row["tabs3_partner_name"] == "Finance Partner"
    assert row["assigned_to_name"] == "Finance Partner"
    assert row["task_status"] == "completed"
    assert row["task_completed_at"].startswith("2026-01-15T13:00:00")
    assert row["lead_id"] == str(lead.id)
    assert row["contact_id"] == str(contact.id)

    invalid = await client.get(
        "/api/intake/dashboard/calls/export",
        params={"start": "2026-02-01", "end": "2026-01-01"},
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


@pytest.mark.asyncio
async def test_partner_task_qualifies_lead_and_assigns_attorney_intake(
    client, db_session, test_tenant, test_user
):
    attorney = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="assigned-attorney@testfirm.com",
        full_name="Assigned Attorney",
        role="user",
        is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Qualified",
        phone="701-555-8181",
        created_by_user_id=test_user.id,
    )
    db_session.add_all([attorney, contact])
    await db_session.flush()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="new",
        practice_area="family",
        description="Receptionist said caller needs divorce help.",
        assigned_to_user_id=test_user.id,
        created_by_user_id=test_user.id,
    )
    db_session.add(lead)
    await db_session.flush()
    partner_task = Task(
        tenant_id=test_tenant.id,
        title="Urgent intake follow-up: Jane Qualified",
        description=(
            "Urgent intake follow-up generated by the local intake dashboard.\n"
            "Caller: Jane Qualified\n"
            "Phone: 701-555-8181\n"
            "Lead description: Needs divorce help"
        ),
        task_type="follow_up",
        status="pending",
        priority="urgent",
        due_date=date.today(),
        contact_id=contact.id,
        assigned_to_user_id=test_user.id,
        created_by_user_id=test_user.id,
        source="intake_dashboard",
        external_ref=f"intake-dashboard:lead:{lead.id}:follow-up",
    )
    db_session.add(partner_task)
    await db_session.commit()

    resp = await client.post(
        f"/api/tasks/{partner_task.id}/qualify-intake",
        json={
            "assigned_to_user_id": str(attorney.id),
            "partner_notes": "Good fit. Schedule consult and collect retainer.",
            "case_description": "Divorce with custody and property issues.",
            "estimated_value": 5000,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["lead_id"] == str(lead.id)
    assert data["contact_id"] == str(contact.id)
    assert data["partner_task_id"] == str(partner_task.id)
    assert data["assigned_to_user_id"] == str(attorney.id)
    assert data["lead_status"] == "qualified"

    await db_session.refresh(lead)
    await db_session.refresh(partner_task)
    attorney_task = await db_session.get(Task, uuid.UUID(data["attorney_task_id"]))
    assert lead.status == "qualified"
    assert lead.assigned_to_user_id == attorney.id
    assert lead.estimated_value == 5000
    assert "Partner qualification notes" in lead.description
    assert partner_task.status == "completed"
    assert partner_task.completed_at is not None
    assert attorney_task.task_type == "intake"
    assert attorney_task.priority == "urgent"
    assert attorney_task.assigned_to_user_id == attorney.id
    assert attorney_task.contact_id == contact.id
    assert (
        attorney_task.external_ref == f"intake-dashboard:lead:{lead.id}:attorney-intake"
    )
    assert "Receptionist call/task notes" in attorney_task.description
    assert "Good fit" in attorney_task.description

    convert = await client.post(
        f"/api/intake/{lead.id}/convert",
        json={
            "matter_name": "Jane Qualified Divorce",
            "matter_type": "family",
            "role": "Petitioner",
            "jurisdiction": "ND",
            "counterparty": "John Qualified",
            "description": attorney_task.description,
            "status": "waiting_fee_agreement",
            "attorney_of_record_id": str(attorney.id),
            "budget_amount": 5000,
            "billing_method": "flat_fee",
        },
    )

    assert convert.status_code == 200
    matter = await db_session.get(Matter, uuid.UUID(convert.json()["matter_id"]))
    await db_session.refresh(contact)
    await db_session.refresh(lead)
    assert matter.status == "waiting_fee_agreement"
    assert matter.client_contact_id == contact.id
    assert matter.attorney_of_record_id == attorney.id
    assert matter.budget_amount == 5000
    assert matter.billing_method == "flat_fee"
    assert contact.contact_type == "client"
    assert lead.status == "matter_opened"


@pytest.mark.asyncio
async def test_partner_assignment_is_logged_on_assign_next(
    client, db_session, test_tenant, test_user
):
    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="p@f.com",
        full_name="Pat Partner",
        role="user",
        is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id,
        contact_type="prospect",
        first_name="Lee",
        last_name="Caller",
    )
    db_session.add_all([partner, contact])
    await db_session.commit()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="new",
        source="phone",
        practice_area="divorce",
    )
    db_session.add(lead)
    db_session.add(
        PartnerRotationState(
            tenant_id=test_tenant.id,
            practice_area="divorce",
            eligible_user_ids=[str(partner.id)],
            is_enabled=True,
        )
    )
    await db_session.commit()

    resp = await client.post(f"/api/intake/dashboard/leads/{lead.id}/assign-next")
    assert resp.status_code == 200

    rows = (
        (
            await db_session.execute(
                select(PartnerAssignmentLog).where(
                    PartnerAssignmentLog.tenant_id == test_tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].assignment_method == "partner_rotation"
    assert rows[0].assigned_to_name == "Pat Partner"
    assert rows[0].lead_id == lead.id


@pytest.mark.asyncio
async def test_partner_log_list_and_export(client, db_session, test_tenant, test_user):
    db_session.add(
        PartnerAssignmentLog(
            tenant_id=test_tenant.id,
            assignment_method="partner_rotation",
            assigned_to_name="Pat Partner",
            assigned_by_name="Reception",
            practice_area="divorce",
        )
    )
    await db_session.commit()

    listing = await client.get("/api/intake/dashboard/partner-log")
    assert listing.status_code == 200
    assert listing.json()["entries"][0]["assigned_to_name"] == "Pat Partner"

    export = await client.get("/api/intake/dashboard/partner-log/export")
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert rows[0]["assigned_to_name"] == "Pat Partner"
    assert rows[0]["assignment_method"] == "partner_rotation"
