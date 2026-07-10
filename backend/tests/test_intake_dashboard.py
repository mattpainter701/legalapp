import asyncio
import csv
import hashlib
import hmac
import io
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import (
    IntakeCallDraft,
    LegacyCallRecord,
    PartnerAssignmentLog,
    PartnerRotationState,
)
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.models.durable_job import DurableJob
from app.models.task import Task
from app.models.user import User
from app.models.plugin import Matter
from app.routers import intake_dashboard as intake_dashboard_router
from app.schemas.intake_dashboard import IntakeDashboardCallCreate
from app.services.intake_archive_import import (
    import_legacy_call_csv,
    normalize_phone,
    parse_legacy_call_csv,
)
from app.services.zoom_phone import (
    extract_zoom_phone_webhook_call_logs,
    import_zoom_phone_records,
    normalize_zoom_phone_record,
    verify_zoom_webhook_signature,
    zoom_webhook_validation_response,
)
from app.services.token_vault import encrypt_token
from app.services.durable_job_worker import process_job


def test_normalize_phone_strips_formatting_and_country_code():
    assert normalize_phone("+1 (701) 555-1212") == "7015551212"
    assert normalize_phone("701.555.1212") == "7015551212"
    assert normalize_phone("") is None


def test_zoom_phone_normalizer_keeps_only_inbound_and_extracts_nested_phone():
    inbound = normalize_zoom_phone_record(
        {
            "id": "nested-inbound-1",
            "direction": "incoming",
            "caller_number": "SCHMIDT JOANN",
            "caller": {"phone_number": "+1 701-555-0199"},
            "callee": {"display_name": "Main - Receptionist"},
            "start_time": "2026-06-22T14:15:00Z",
            "result": "answered",
        }
    )
    outbound = normalize_zoom_phone_record(
        {
            "id": "outbound-1",
            "direction": "outbound",
            "caller": {"display_name": "Main - Receptionist"},
            "callee": {"phone_number": "+1 701-555-0100"},
        }
    )

    assert inbound is not None
    assert inbound["direction"] == "inbound"
    assert inbound["participants"]["phone"] == "+1 701-555-0199"
    assert inbound["participants"]["normalized_phone"] == "7015550199"
    assert "SCHMIDT JOANN" in inbound["subject"]
    assert outbound is None


def test_zoom_webhook_crc_and_signature_helpers():
    validation = zoom_webhook_validation_response("plain-token", secret="zoom-secret")
    assert validation == {
        "plainToken": "plain-token",
        "encryptedToken": "d83eef2ba06385e69f487c6fe8949751a8c039fd802f4bc14c765398750528ac",
    }

    body = b'{"event":"phone.callee_call_history_completed"}'
    timestamp = "1710000000"
    signature = "v0=" "ec914a6f28f9db2fdf91b4993df12354403b78ea150c45531c540726289afb4e"
    assert verify_zoom_webhook_signature(
        body,
        timestamp,
        signature,
        secret="zoom-secret",
        tolerance_seconds=0,
    )
    assert not verify_zoom_webhook_signature(
        body,
        timestamp,
        "v0=bad",
        secret="zoom-secret",
        tolerance_seconds=0,
    )


def test_extract_zoom_phone_webhook_call_logs_keeps_completed_inbound_callee_records():
    event = {
        "event": "phone.callee_call_history_completed",
        "payload": {
            "account_id": "acct-1",
            "object": {
                "call_logs": [
                    {
                        "id": "history-detail-1",
                        "direction": "inbound",
                        "caller_name": "Jane Caller",
                    },
                    {
                        "id": "outbound-1",
                        "direction": "outbound",
                        "caller_name": "Main - Receptionist",
                    },
                ],
            },
        },
    }

    logs = extract_zoom_phone_webhook_call_logs(event)

    assert logs == [
        {
            "id": "history-detail-1",
            "direction": "inbound",
            "caller_name": "Jane Caller",
        }
    ]


def test_extract_zoom_phone_v3_call_element_webhook():
    event = {
        "event": "phone.callee_call_element_completed",
        "payload": {
            "account_id": "acct-1",
            "object": {
                "call_elements": [
                    {
                        "call_element_id": "element-1",
                        "call_history_uuid": "history-1",
                        "call_id": "call-1",
                        "direction": "inbound",
                        "caller_name": "First Customer Caller",
                        "caller_did_number": "+1 701-555-0101",
                        "result": "answered",
                    }
                ]
            },
        },
    }

    logs = extract_zoom_phone_webhook_call_logs(event)

    assert len(logs) == 1
    assert logs[0]["call_element_id"] == "element-1"
    assert logs[0]["call_history_uuid"] == "history-1"


@pytest.mark.asyncio
async def test_zoom_ingress_to_intake_to_assigned_task_e2e(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """Prove the first-customer path from Zoom webhook through the Tasks API."""
    webhook_secret = "zoom-first-customer-webhook-secret"
    account_id = "zoom-first-customer-account"
    staff = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="first-customer-staff@testfirm.com",
        full_name="First Customer Staff",
        role="user",
        is_active=True,
    )
    db_session.add_all(
        [
            staff,
            TenantOAuthApp(
                tenant_id=test_tenant.id,
                provider="zoom_phone",
                encrypted_client_id=encrypt_token("zoom-client"),
                encrypted_client_secret=encrypt_token("zoom-client-secret"),
                encrypted_webhook_secret_token=encrypt_token(webhook_secret),
                zoom_account_id=account_id,
                configured_by_user_id=test_user.id,
                is_active=True,
            ),
            TenantCredential(
                tenant_id=test_tenant.id,
                provider="zoom_phone",
                encrypted_access_token=encrypt_token("zoom-access-token"),
                encrypted_refresh_token=encrypt_token("zoom-refresh-token"),
                service_account_email=account_id,
                is_active=True,
            ),
        ]
    )
    await db_session.commit()
    staff_id = staff.id

    async def fake_call_detail(*_args, **_kwargs):
        return {
            "call_element_id": "first-customer-element-1",
            "call_history_uuid": "first-customer-call-1",
            "direction": "inbound",
            "caller_name": "First Customer Caller",
            "caller_number": "+1 701-555-0110",
            "callee_name": "Reception",
            "start_time": "2026-07-09T14:15:00Z",
            "result": "answered",
        }

    monkeypatch.setattr(
        "app.services.zoom_phone.fetch_zoom_phone_call_history_detail",
        fake_call_detail,
    )

    event = {
        "event": "phone.callee_call_element_completed",
        "payload": {
            "account_id": account_id,
            "object": {
                "call_elements": [
                    {
                        "call_element_id": "first-customer-element-1",
                        "call_history_uuid": "first-customer-call-1",
                        "direction": "inbound",
                        "caller_name": "First Customer Caller",
                        "caller_number": "+1 701-555-0110",
                        "callee_name": "Reception",
                        "start_time": "2026-07-09T14:15:00Z",
                        "result": "answered",
                    }
                ]
            },
        },
    }
    body = json.dumps(event, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(
        webhook_secret.encode(),
        b"v0:" + timestamp.encode() + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    ingress = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=body,
        headers={
            "content-type": "application/json",
            "x-zm-request-timestamp": timestamp,
            "x-zm-signature": f"v0={digest}",
        },
    )
    assert ingress.status_code == 200, ingress.text
    assert ingress.json() == {"status": "accepted", "queued": 1}
    job = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == test_tenant.id,
            DurableJob.kind == "zoom_phone_call_import",
        )
    )
    assert job is not None
    assert await process_job(job.id, test_tenant.id)
    await db_session.rollback()

    queue = await client.get("/api/intake/dashboard/zoom-phone/calls")
    assert queue.status_code == 200
    zoom_call = next(
        call
        for call in queue.json()["calls"]
        if call["external_ref"] == "zoom_phone:call:first-customer-call-1"
    )

    captured = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "existing_communication_id": zoom_call["id"],
            "caller_name": "First Customer Caller",
            "phone": "+1 701-555-0110",
            "practice_area": "family",
            "purpose": "Needs an attorney callback",
            "outcome": "create_lead",
            "qualified": True,
            "task_mode": "specific_staff",
            "task_assigned_to_user_id": str(staff_id),
            "task_title": "Return intake call",
        },
    )
    assert captured.status_code == 201, captured.text
    task_id = captured.json()["task_id"]

    tasks = await client.get("/api/tasks", params={"limit": 200})
    assert tasks.status_code == 200
    task = next(item for item in tasks.json()["items"] if item["id"] == task_id)
    assert task["source"] == "intake_dashboard"
    assert task["assigned_to_user_id"] == str(staff_id)
    assert task["contact_id"] == captured.json()["contact_id"]
    assert "First Customer Caller" in task["title"]


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
                "callee_name": "Front Desk",
                "result": "answered",
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
    match = next(
        item
        for item in full_name_results
        if item["result_type"] == "call_log" and item["title"] == "Jan Patterson"
    )
    # History matches must surface who answered the call (callee_name), not just
    # the caller-history name match.
    assert match["answered_by"] == "Front Desk"
    assert match["result"] == "answered"

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
    assert second.skipped == 1
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
async def test_zoom_call_capture_retry_reuses_contact_lead_and_task(
    client, db_session, test_tenant, monkeypatch
):
    notified_task_ids: list[uuid.UUID] = []

    async def capture_notification(_db, notified_task, _tenant_id):
        notified_task_ids.append(notified_task.id)
        return True

    monkeypatch.setattr(
        intake_dashboard_router, "notify_task_created", capture_notification
    )
    staff = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="retry-staff@testfirm.com",
        full_name="Retry Staff",
        role="user",
        is_active=True,
    )
    source_log = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        direction="inbound",
        channel="call",
        status="received",
        subject="Zoom Phone inbound call: Retry Caller",
        body="Provider-owned call detail",
        summary="answered",
        external_ref="zoom_phone:call:retry-capture-1",
        participants={"provider": "zoom_phone", "caller_name": "Retry Caller"},
    )
    db_session.add_all([staff, source_log])
    await db_session.commit()

    payload = {
        "existing_communication_id": str(source_log.id),
        "caller_name": "Retry Caller",
        "phone": "+1 701-555-0131",
        "practice_area": "family",
        "purpose": "Needs a custody consultation",
        "outcome": "create_lead",
        "qualified": True,
        "task_mode": "specific_staff",
        "task_assigned_to_user_id": str(staff.id),
        "task_title": "Return intake call",
    }

    first = await client.post("/api/intake/dashboard/calls", json=payload)
    second = await client.post("/api/intake/dashboard/calls", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_data = first.json()
    second_data = second.json()
    assert second_data["communication_id"] == first_data["communication_id"]
    assert second_data["contact_id"] == first_data["contact_id"]
    assert second_data["lead_id"] == first_data["lead_id"]
    assert second_data["task_id"] == first_data["task_id"]
    assert first_data["created_lead"] is True
    assert second_data["created_lead"] is False

    assert await db_session.scalar(select(func.count()).select_from(Contact)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(PartnerAssignmentLog))
        == 1
    )
    assert notified_task_ids == [uuid.UUID(first_data["task_id"])]
    await db_session.refresh(source_log)
    assert source_log.participants["intake_lead_id"] == first_data["lead_id"]
    assert source_log.body.count("--- Original Zoom Phone details ---") == 1


@pytest.mark.asyncio
async def test_legacy_zoom_capture_retry_recovers_call_task_linked_lead(
    client, db_session, test_tenant, test_user, monkeypatch
):
    notified_task_ids: list[uuid.UUID] = []

    async def capture_notification(_db, notified_task, _tenant_id):
        notified_task_ids.append(notified_task.id)
        return True

    monkeypatch.setattr(
        intake_dashboard_router, "notify_task_created", capture_notification
    )
    staff = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="legacy-retry-staff@testfirm.com",
        full_name="Legacy Retry Staff",
        role="user",
        is_active=True,
    )
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_type="prospect",
        first_name="Legacy",
        last_name="Caller",
        phone="+1 701-555-0133",
        created_by_user_id=test_user.id,
    )
    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="qualified",
        source="phone",
        practice_area="family",
        description="Needs a custody consultation",
        created_by_user_id=test_user.id,
    )
    source_log = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        direction="inbound",
        channel="call",
        status="logged",
        subject="Inbound call: Legacy Caller",
        body=(
            "Needs a custody consultation\n\n"
            "--- Original Zoom Phone details ---\nProvider-owned legacy detail"
        ),
        summary="Needs a custody consultation",
        contact_id=contact.id,
        created_by_user_id=test_user.id,
        external_ref="zoom_phone:call:legacy-capture-1",
        participants={
            "provider": "zoom_phone",
            "caller_name": "Legacy Caller",
            "phone": "+1 701-555-0133",
        },
    )
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        title="Legacy Caller - Return intake call",
        description=(
            "General intake task generated by the local intake dashboard.\n"
            f"Linked lead: {lead.id}"
        ),
        task_type="follow_up",
        status="pending",
        priority="urgent",
        due_date=date.today(),
        contact_id=contact.id,
        assigned_to_user_id=staff.id,
        created_by_user_id=test_user.id,
        source="intake_dashboard",
        external_ref=f"intake-dashboard:call:{source_log.id}:general-task",
    )
    db_session.add_all([staff, contact])
    await db_session.flush()
    db_session.add_all([lead, source_log, task])
    await db_session.commit()

    response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "existing_communication_id": str(source_log.id),
            "caller_name": "Legacy Caller",
            "phone": "+1 701-555-0133",
            "practice_area": "family",
            "purpose": "Needs a custody consultation",
            "outcome": "create_lead",
            "qualified": True,
            "task_mode": "specific_staff",
            "task_assigned_to_user_id": str(staff.id),
            "task_title": "Return intake call",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["communication_id"] == str(source_log.id)
    assert data["contact_id"] == str(contact.id)
    assert data["lead_id"] == str(lead.id)
    assert data["task_id"] == str(task.id)
    assert data["created_lead"] is False
    assert await db_session.scalar(select(func.count()).select_from(Contact)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 1
    assert notified_task_ids == []
    await db_session.refresh(source_log)
    assert source_log.participants["intake_lead_id"] == data["lead_id"]
    assert source_log.body.count("--- Original Zoom Phone details ---") == 1


@pytest.mark.asyncio
async def test_legacy_zoom_capture_without_explicit_lead_link_fails_closed(
    client, db_session, test_tenant, test_user
):
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_type="prospect",
        first_name="Ambiguous",
        last_name="Caller",
        phone="+1 701-555-0134",
        created_by_user_id=test_user.id,
    )
    unrelated_lead = Lead(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="new",
        source="phone",
        created_by_user_id=test_user.id,
    )
    source_log = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        direction="inbound",
        channel="call",
        status="logged",
        subject="Inbound call: Ambiguous Caller",
        body="Previously captured without a durable lead marker",
        contact_id=contact.id,
        created_by_user_id=test_user.id,
        external_ref="zoom_phone:call:legacy-ambiguous-1",
        participants={"provider": "zoom_phone", "caller_name": "Ambiguous Caller"},
    )
    db_session.add(contact)
    await db_session.flush()
    db_session.add_all([unrelated_lead, source_log])
    await db_session.commit()

    response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "existing_communication_id": str(source_log.id),
            "caller_name": "Ambiguous Caller",
            "phone": "+1 701-555-0134",
            "purpose": "Requests a new consultation",
            "outcome": "create_lead",
            "qualified": True,
            "task_mode": "none",
        },
    )

    assert response.status_code == 409
    assert "no explicit lead link" in response.json()["detail"]
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 1
    await db_session.refresh(source_log)
    assert "intake_lead_id" not in source_log.participants


@pytest.mark.asyncio
async def test_legacy_zoom_capture_with_mismatched_lead_link_fails_closed(
    client, db_session, test_tenant, test_user
):
    linked_contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_type="prospect",
        first_name="Linked",
        last_name="Caller",
        created_by_user_id=test_user.id,
    )
    other_contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_type="prospect",
        first_name="Other",
        last_name="Caller",
        created_by_user_id=test_user.id,
    )
    wrong_lead = Lead(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_id=other_contact.id,
        status="new",
        source="phone",
        created_by_user_id=test_user.id,
    )
    source_log = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        direction="inbound",
        channel="call",
        status="logged",
        subject="Inbound call: Linked Caller",
        contact_id=linked_contact.id,
        created_by_user_id=test_user.id,
        external_ref="zoom_phone:call:legacy-mismatch-1",
        participants={"provider": "zoom_phone", "caller_name": "Linked Caller"},
    )
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        title="Linked Caller - Return call",
        description=f"Linked lead: {wrong_lead.id}",
        source="intake_dashboard",
        external_ref=f"intake-dashboard:call:{source_log.id}:general-task",
    )
    db_session.add_all([linked_contact, other_contact])
    await db_session.flush()
    db_session.add_all([wrong_lead, source_log, task])
    await db_session.commit()

    response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "existing_communication_id": str(source_log.id),
            "caller_name": "Linked Caller",
            "outcome": "create_lead",
            "qualified": True,
            "task_mode": "none",
        },
    )

    assert response.status_code == 409
    assert "invalid lead link" in response.json()["detail"]
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 1


@pytest.mark.asyncio
async def test_concurrent_zoom_call_capture_creates_one_contact_lead_and_task(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    staff = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="concurrent-staff@testfirm.com",
        full_name="Concurrent Staff",
        role="user",
        is_active=True,
    )
    source_log = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        direction="inbound",
        channel="call",
        status="received",
        subject="Zoom Phone inbound call: Concurrent Caller",
        body="Provider-owned concurrent call detail",
        summary="answered",
        external_ref="zoom_phone:call:concurrent-capture-1",
        participants={
            "provider": "zoom_phone",
            "caller_name": "Concurrent Caller",
        },
    )
    db_session.add_all([staff, source_log])
    await db_session.commit()

    notified_task_ids: list[uuid.UUID] = []

    async def capture_notification(_db, notified_task, _tenant_id):
        notified_task_ids.append(notified_task.id)
        return True

    monkeypatch.setattr(
        intake_dashboard_router, "notify_task_created", capture_notification
    )
    payload = IntakeDashboardCallCreate(
        existing_communication_id=source_log.id,
        caller_name="Concurrent Caller",
        phone="+1 701-555-0132",
        practice_area="family",
        purpose="Needs an attorney callback",
        outcome="create_lead",
        qualified=True,
        task_mode="specific_staff",
        task_assigned_to_user_id=staff.id,
        task_title="Return concurrent intake call",
    )
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def capture_once():
        async with session_factory() as session:
            return await intake_dashboard_router.create_dashboard_call(
                payload,
                current_user=test_user,
                db=session,
            )

    first, second = await asyncio.gather(capture_once(), capture_once())

    assert second.communication_id == first.communication_id == source_log.id
    assert second.contact_id == first.contact_id
    assert second.lead_id == first.lead_id
    assert second.task_id == first.task_id
    assert {first.created_lead, second.created_lead} == {True, False}
    assert await db_session.scalar(select(func.count()).select_from(Contact)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(PartnerAssignmentLog))
        == 1
    )
    assert notified_task_ids == [first.task_id]

    await db_session.refresh(source_log)
    assert source_log.participants["intake_lead_id"] == str(first.lead_id)
    assert source_log.body.count("--- Original Zoom Phone details ---") == 1


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
    assert task.title == "Sam Caller - Route to service provider"
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
async def test_recent_callers_marks_internal_zoom_calls(
    client, db_session, test_tenant
):
    log = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        channel="call",
        direction="inbound",
        subject="Zoom Phone inbound call: Reception to Attorney",
        summary="answered",
        body="Internal transfer",
        status="received",
        occurred_at=datetime.now(timezone.utc),
        external_ref=f"zoom_phone:call:{uuid.uuid4()}",
        participants={
            "provider": "zoom_phone",
            "direction": "inbound",
            "caller_name": "Reception",
            "callee_name": "Attorney",
            "caller_number": "101",
            "callee_number": "202",
            "caller_extension_number": "101",
            "callee_extension_number": "202",
            "result": "answered",
        },
    )
    db_session.add(log)
    await db_session.commit()

    recent = await client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 10}
    )

    assert recent.status_code == 200
    caller = recent.json()["callers"][0]
    assert caller["id"] == str(log.id)
    assert caller["direction"] == "inbound"
    assert caller["caller_number"] == "101"
    assert caller["callee_number"] == "202"
    assert caller["is_internal_call"] is True
    assert caller["internal_call_type"] == "internal_to_internal"


@pytest.mark.asyncio
async def test_dashboard_call_create_lead_specific_staff_keeps_post_commit_context(
    client, db_session, test_tenant, monkeypatch
):
    staff = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="leadstaff@testfirm.com",
        full_name="Lead Staff",
        role="user",
        is_active=True,
    )
    db_session.add(staff)
    await db_session.commit()

    original_refresh = db_session.refresh

    async def fail_communication_log_refresh(instance, *args, **kwargs):
        if isinstance(instance, CommunicationLog):
            raise AssertionError("post-commit communication log refresh is unsafe")
        return await original_refresh(instance, *args, **kwargs)

    notified = {}

    async def assert_scoped_notification(db, task, tenant_id, assignment_note=None):
        current_tenant = (
            await db.execute(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
        ).scalar_one()
        assert current_tenant == tenant_id
        notified["task_id"] = task.id

    monkeypatch.setattr(db_session, "refresh", fail_communication_log_refresh)
    monkeypatch.setattr(
        intake_dashboard_router, "notify_task_created", assert_scoped_notification
    )

    resp = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Lena Lead",
            "phone": "(701) 555-9191",
            "practice_area": "family",
            "purpose": "Needs attorney review and staff follow-up",
            "outcome": "create_lead",
            "qualified": True,
            "task_mode": "specific_staff",
            "task_assigned_to_user_id": str(staff.id),
            "task_title": "Follow up with Lena Lead",
        },
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["created_lead"] is True
    assert data["lead_id"]
    assert data["task_id"]
    assert notified["task_id"] == uuid.UUID(data["task_id"])

    task = await db_session.get(Task, uuid.UUID(data["task_id"]))
    assert task.assigned_to_user_id == staff.id
    assert task.contact_id == uuid.UUID(data["contact_id"])
    assert task.title == "Lena Lead - Follow up with Lena Lead"
    assert "Created by:" in task.description
    assert f"Linked lead: {data['lead_id']}" in task.description

    assignment = (
        await db_session.execute(
            select(PartnerAssignmentLog).where(
                PartnerAssignmentLog.communication_id
                == uuid.UUID(data["communication_id"])
            )
        )
    ).scalar_one()
    assert assignment.assignment_method == "specific_staff"
    assert assignment.assigned_to_user_id == staff.id


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


@pytest.mark.asyncio
async def test_recent_callers_exposes_source_and_call_facts(
    client, db_session, test_tenant, test_user
):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Zoom Phone inbound call: Zed Caller",
                summary="Zoom call",
                external_ref="zoom_phone:call:abc123",
                participants={
                    "caller_name": "Zed Caller",
                    "phone": "701-555-7777",
                    "callee_name": "Front Desk",
                    "result": "answered",
                    "duration_seconds": 142,
                    "recording_url": "https://zoom.example/rec",
                    "transcript_url": "https://zoom.example/txt",
                    "provider": "zoom_phone",
                },
                occurred_at=now,
            ),
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Inbound call: Manny Manual",
                summary="Walk-in style",
                participants={"caller_name": "Manny Manual", "phone": "701-555-0000"},
                created_by_user_id=test_user.id,
                occurred_at=now - timedelta(minutes=3),
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get("/api/intake/dashboard/recent-callers", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 5
    by_name = {c["caller_name"]: c for c in data["callers"]}

    zed = by_name["Zed Caller"]
    assert zed["source"] == "zoom_phone"
    assert zed["answered_by"] == "Front Desk"
    assert zed["result"] == "answered"
    assert zed["duration_seconds"] == 142
    assert zed["recording_url"] == "https://zoom.example/rec"
    assert zed["transcript_url"] == "https://zoom.example/txt"

    manny = by_name["Manny Manual"]
    assert manny["source"] == "manual"
    assert manny["answered_by"] is None
    assert manny["recording_url"] is None


@pytest.mark.asyncio
async def test_recent_callers_batched_enrichment_matches(
    client, db_session, test_tenant, test_user
):
    partner = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="bq@f.com",
        full_name="Batch Partner",
        role="user",
        is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Bea",
        last_name="Quary",
        phone="701-555-3333",
        created_by_user_id=test_user.id,
    )
    db_session.add_all([partner, contact])
    await db_session.flush()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="qualified",
        practice_area="divorce",
        assigned_to_user_id=partner.id,
        created_by_user_id=test_user.id,
    )
    db_session.add(lead)
    await db_session.flush()
    log = CommunicationLog(
        tenant_id=test_tenant.id,
        direction="inbound",
        channel="call",
        status="logged",
        subject="Inbound call: Bea Quary",
        summary="Batched",
        participants={"caller_name": "Bea Quary", "phone": "701-555-3333"},
        contact_id=contact.id,
        created_by_user_id=test_user.id,
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(log)
    db_session.add(
        Task(
            tenant_id=test_tenant.id,
            title="Urgent intake follow-up: Bea Quary",
            description="x",
            task_type="follow_up",
            status="pending",
            priority="urgent",
            due_date=date.today(),
            contact_id=contact.id,
            assigned_to_user_id=partner.id,
            created_by_user_id=test_user.id,
            source="intake_dashboard",
            external_ref=f"intake-dashboard:lead:{lead.id}:follow-up",
        )
    )
    await db_session.commit()

    resp = await client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 10}
    )
    assert resp.status_code == 200
    caller = resp.json()["callers"][0]
    assert caller["caller_name"] == "Bea Quary"
    assert caller["lead_id"] == str(lead.id)
    assert caller["lead_status"] == "qualified"
    assert caller["assigned_to_name"] == "Batch Partner"
    assert caller["task_status"] == "pending"
    assert caller["created_by_name"] in (test_user.full_name, test_user.email)


@pytest.mark.asyncio
async def test_intake_drafts_crud_and_upsert_is_idempotent(
    client, db_session, test_tenant, test_user
):
    draft_id = uuid.uuid4()
    initial_payload = {
        "caller_name": "Jane Doe",
        "phone": "7015558888",
        "notes": "Initial intake note",
    }

    created = await client.put(
        f"/api/intake/drafts/{draft_id}",
        json={"payload": initial_payload},
    )
    assert created.status_code == 200
    created_data = created.json()
    assert created_data["id"] == str(draft_id)
    assert created_data["tenant_id"] == str(test_tenant.id)
    assert created_data["created_by_user_id"] == str(test_user.id)
    assert created_data["payload"] == initial_payload

    list_after_create = await client.get("/api/intake/drafts")
    assert list_after_create.status_code == 200
    drafts = list_after_create.json()
    assert len(drafts) == 1
    assert drafts[0]["id"] == str(draft_id)
    assert drafts[0]["payload"] == initial_payload

    updated_payload = {
        "caller_name": "Jane Doe",
        "phone": "7015558888",
        "notes": "Updated intake note",
        "status": "in_progress",
    }
    updated = await client.put(
        f"/api/intake/drafts/{draft_id}",
        json={"payload": updated_payload},
    )
    assert updated.status_code == 200
    assert updated.json()["payload"] == updated_payload
    assert updated.json()["id"] == str(draft_id)

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(IntakeCallDraft)
        .where(IntakeCallDraft.id == draft_id)
    )
    assert row_count == 1

    draft_record = (
        await db_session.execute(
            select(IntakeCallDraft).where(IntakeCallDraft.id == draft_id)
        )
    ).scalar_one()
    assert draft_record.payload == updated_payload
    assert draft_record.created_by_user_id == test_user.id
    assert draft_record.tenant_id == test_tenant.id

    deleted = await client.delete(f"/api/intake/drafts/{draft_id}")
    assert deleted.status_code == 204
    deleted_again = await client.delete(f"/api/intake/drafts/{draft_id}")
    assert deleted_again.status_code == 404

    list_after_delete = await client.get("/api/intake/drafts")
    assert list_after_delete.status_code == 200
    assert list_after_delete.json() == []


@pytest.mark.asyncio
async def test_intake_drafts_are_scoped_by_current_user_and_tenant(
    client, db_session, test_tenant, test_user
):
    peer = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="peer@testfirm.com",
        full_name="Peer User",
        role="user",
        is_active=True,
        oauth_provider="google",
        oauth_subject="peer-subject",
    )
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other-firm.example",
        billing_tier="payg",
        is_active=True,
    )
    other_tenant_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="other@testfirm.com",
        full_name="Other Tenant User",
        role="admin",
        is_active=True,
        oauth_provider="google",
        oauth_subject="other-subject",
    )
    peer_draft_id = uuid.uuid4()
    other_tenant_draft_id = uuid.uuid4()

    db_session.add(other_tenant)
    await db_session.flush()
    db_session.add_all([peer, other_tenant_user])
    await db_session.flush()
    db_session.add_all(
        [
            IntakeCallDraft(
                id=peer_draft_id,
                tenant_id=test_tenant.id,
                created_by_user_id=peer.id,
                payload={"owner": "peer"},
            ),
            IntakeCallDraft(
                id=other_tenant_draft_id,
                tenant_id=other_tenant.id,
                created_by_user_id=other_tenant_user.id,
                payload={"owner": "other_tenant"},
            ),
        ]
    )
    await db_session.commit()

    mine_payload = {"caller_name": "Mine"}
    created = await client.put(
        f"/api/intake/drafts/{uuid.uuid4()}",
        json={"payload": mine_payload},
    )
    assert created.status_code == 200

    listing = await client.get("/api/intake/drafts")
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()}
    assert created.json()["id"] in ids
    assert peer_draft_id not in ids
    assert other_tenant_draft_id not in ids

    assert (
        await client.delete(f"/api/intake/drafts/{peer_draft_id}")
    ).status_code == 404
    assert (
        await client.delete(f"/api/intake/drafts/{other_tenant_draft_id}")
    ).status_code == 404


@pytest.mark.asyncio
async def test_intake_drafts_idempotent_put_rejects_other_tenant_id_collision(
    client, db_session, test_tenant, test_user
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Collision Tenant",
        domain="collision.example",
        billing_tier="payg",
        is_active=True,
    )
    other_tenant_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email=f"collision-{uuid.uuid4().hex[:8]}@testfirm.com",
        full_name="Collision Tenant User",
        role="admin",
        is_active=True,
    )
    collision_id = uuid.uuid4()

    db_session.add(other_tenant)
    await db_session.flush()
    db_session.add(other_tenant_user)
    await db_session.flush()
    db_session.add(
        IntakeCallDraft(
            id=collision_id,
            tenant_id=other_tenant.id,
            created_by_user_id=other_tenant_user.id,
            payload={"status": "stale"},
        )
    )
    await db_session.commit()

    response = await client.put(
        f"/api/intake/drafts/{collision_id}",
        json={"payload": {"status": "fresh"}},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_intake_drafts_updated_at_is_server_authored(
    client,
    db_session,
    test_tenant,
    test_user,
):
    draft_id = uuid.uuid4()

    created = await client.put(
        f"/api/intake/drafts/{draft_id}",
        json={"payload": {"notes": "first"}},
    )
    assert created.status_code == 200
    created_data = created.json()
    created_updated_at = datetime.fromisoformat(
        created_data["updated_at"].replace("Z", "+00:00")
    )

    await asyncio.sleep(0.01)
    updated = await client.put(
        f"/api/intake/drafts/{draft_id}",
        json={"payload": {"notes": "second"}},
    )
    assert updated.status_code == 200
    updated_data = updated.json()
    updated_updated_at = datetime.fromisoformat(
        updated_data["updated_at"].replace("Z", "+00:00")
    )

    assert updated_updated_at > created_updated_at
