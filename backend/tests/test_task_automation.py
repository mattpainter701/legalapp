"""Tests for deterministic, single-attempt execution of approved task actions.

The guarantee under test: one approval key starts at most one automatic provider
attempt, no matter how many times the approval arrives. External delivery can be
ambiguous after a timeout, so failed attempts are terminal rather than retried.
"""

import hashlib
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.contact import Contact
from app.models.document import Document
from app.models.matter_document import MatterDocument
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.task import Task, TaskAutomationRun
from app.models.tenant import TenantSettings
from app.models.user import User
from app.services import task_automation
from app.services.connected_mail import ConnectedMailDelivery
from app.services.email import EmailDeliveryResult
from app.services.matter_file_store import StorageResult


@pytest.fixture(autouse=True)
def _approved_actor_has_legal_approval_capability(monkeypatch):
    """Existing delivery fixtures represent work already approved by counsel."""

    async def allow(_db, _actor_user_id):
        return True

    monkeypatch.setattr(task_automation, "_actor_can_approve_legal_work", allow)


class _RecordingSender:
    """Stand-in for EmailService.send_email that counts real sends."""

    def __init__(self, result=EmailDeliveryResult.SENT):
        self.result = result
        self.calls = []

    async def send_email(self, to, subject, html_body, text_body=""):
        self.calls.append(
            {"to": list(to), "subject": subject, "html": html_body, "text": text_body}
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def _matter(db_session, tenant, user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"matter-{uuid.uuid4().hex[:8]}",
        matter_name="Redwood Outdoor Supply - OGC Retainer",
        matter_type="corporate",
    )
    db_session.add(matter)
    await db_session.commit()
    return matter


async def _approved_email_task(
    db_session, tenant, user, matter, *, version=1, status="in_progress"
):
    settings = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )
    if settings is None:
        settings = TenantSettings(tenant_id=tenant.id, enable_chat_actions=True)
        db_session.add(settings)
    else:
        settings.enable_chat_actions = True
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        first_name="Redwood",
        last_name="Operations",
        email="ops@redwood.example",
    )
    db_session.add(contact)
    await db_session.flush()
    party = MatterParty(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        matter_id=matter.id,
        contact_id=contact.id,
        role="client",
        is_primary=True,
    )
    db_session.add(party)
    await db_session.flush()
    task = Task(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        matter_id=matter.id,
        title="Request insurance certificate from Redwood",
        status=status,
        reviewer_user_id=user.id,
        source="assistant",
        version=version,
        pending_action={
            "type": "email_client",
            "to": ["ops@redwood.example"],
            "recipient_bindings": [
                {
                    "party_id": str(party.id),
                    "contact_id": str(contact.id),
                    "address": "ops@redwood.example",
                }
            ],
            "subject": "Insurance certificate for the warehouse pilot",
            "body": "Please send the current certificate of insurance.",
            "matter_id": str(matter.id),
            "source_ids": [],
        },
    )
    db_session.add(task)
    await db_session.commit()
    return task


async def _approved_document_task(
    db_session, tenant, user, matter, *, version=1, status="in_progress"
):
    settings = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )
    if settings is None:
        settings = TenantSettings(tenant_id=tenant.id, enable_chat_actions=True)
        db_session.add(settings)
    else:
        settings.enable_chat_actions = True
    task = Task(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        matter_id=matter.id,
        title="Prepare client status letter",
        status=status,
        reviewer_user_id=user.id,
        source="assistant",
        version=version,
        pending_action={
            "type": "matter_document_draft",
            "matter_id": str(matter.id),
            "title": "Client Status Letter",
            "body": "Dear Client,\n\nThe matter remains on schedule.",
            "source_ids": [],
            "sources": [],
        },
    )
    db_session.add(task)
    await db_session.commit()
    return task


@pytest.mark.asyncio
async def test_approving_a_drafted_email_sends_it_once(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    approved_snapshot = dict(task.pending_action)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert len(sender.calls) == 1
    assert sender.calls[0]["to"] == ["ops@redwood.example"]

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "sent"
    assert run.delivery_certainty == "confirmed_sent"
    assert run.error_message is None
    assert run.action_snapshot == approved_snapshot
    assert run.action_sha256 == task_automation.action_payload_sha256(approved_snapshot)
    assert run.provider == "smtp"
    assert "sent" in (run.delivery_detail or "").lower()
    assert run.completed_at is not None
    await db_session.refresh(task)
    assert task.pending_action is None


@pytest.mark.asyncio
async def test_legacy_document_draft_is_not_uploaded_during_approval(
    db_session, test_tenant, test_user, monkeypatch
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_document_task(db_session, test_tenant, test_user, matter)
    storage_called = False

    async def store_document(**kwargs):
        nonlocal storage_called
        storage_called = True
        return StorageResult(
            provider="local",
            backend="local",
            storage_path="/uploads/client-status-letter.docx",
        )

    monkeypatch.setattr(
        task_automation.matter_file_store,
        "store_matter_file_result",
        store_document,
    )

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert storage_called is False
    document = await db_session.scalar(
        select(MatterDocument).where(MatterDocument.matter_id == matter.id)
    )
    assert document is None

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert run.delivery_certainty == "not_attempted"
    assert "predates verified cloud review" in run.error_message
    assert run.provider is None
    assert run.provider_message_id is None
    await db_session.refresh(task)
    assert task.pending_action is not None
    assert task.status == "in_progress"
    assert task.completed_at is None
    assert task.version == 1


@pytest.mark.asyncio
async def test_sent_delivery_audit_prevents_task_deletion(
    client, db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )
    response = await client.delete(f"/api/tasks/{task.id}")

    assert response.status_code == 409
    assert "delivery audit" in response.json()["detail"].lower()
    assert await db_session.get(Task, task.id) is not None
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run is not None
    assert run.status == "sent"
    assert run.action_snapshot is not None


@pytest.mark.parametrize("reference_state", ["pending", "audited"])
@pytest.mark.asyncio
async def test_action_source_document_cannot_be_deleted(
    reference_state,
    client,
    db_session,
    test_tenant,
    test_user,
    tmp_path,
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    source_path = tmp_path / f"{reference_state}-evidence.pdf"
    source_path.write_bytes(b"retained source evidence")
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename=source_path.name,
        content_type="application/pdf",
        storage_path=str(source_path),
        status="indexed",
        matter_id=matter.id,
    )
    db_session.add(document)
    await db_session.flush()
    snapshot = {
        **task.pending_action,
        "source_document_ids": [str(document.id)],
        "sources": [
            {
                "source_id": f"document:{document.id}",
                "label": document.filename,
                "url": f"/api/documents/{document.id}/download",
            }
        ],
    }
    if reference_state == "pending":
        task.pending_action = snapshot
    else:
        task.pending_action = None
        task.status = "in_progress"
        db_session.add(
            TaskAutomationRun(
                tenant_id=test_tenant.id,
                task_id=task.id,
                action_type="email_client",
                idempotency_key="retained-audit-source",
                action_snapshot=snapshot,
                action_sha256=task_automation.action_payload_sha256(snapshot),
                status="sent",
                delivery_certainty="confirmed_sent",
                completed_at=datetime.now(timezone.utc),
            )
        )
    await db_session.commit()

    response = await client.delete(f"/api/documents/{document.id}")

    assert response.status_code == 409
    assert "source evidence" in response.json()["detail"]
    assert source_path.exists()
    assert await db_session.get(Document, document.id) is not None


@pytest.mark.asyncio
async def test_changed_action_source_bytes_block_delivery(
    db_session,
    test_tenant,
    test_user,
    tmp_path,
    monkeypatch,
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    original = b"board-approved evidence version"
    source_path = tmp_path / "approved-evidence.pdf"
    source_path.write_bytes(original)
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename=source_path.name,
        content_type="application/pdf",
        storage_path=str(source_path),
        status="ready",
        matter_id=matter.id,
    )
    db_session.add(document)
    await db_session.flush()
    task.pending_action = {
        **task.pending_action,
        "source_document_ids": [str(document.id)],
        "source_document_bindings": [
            {
                "document_id": str(document.id),
                "sha256": hashlib.sha256(original).hexdigest(),
            }
        ],
        "sources": [
            {
                "source_id": f"document:{document.id}",
                "label": document.filename,
                "url": f"/api/documents/{document.id}/download",
            }
        ],
    }
    await db_session.commit()

    source_path.write_bytes(b"changed after attorney review")
    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert run.delivery_certainty == "not_attempted"
    assert "cited local documents" in run.error_message


@pytest.mark.asyncio
async def test_legacy_durable_job_is_terminalized_without_delivery(
    db_session,
    test_tenant,
    test_user,
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    legacy_run = TaskAutomationRun(
        tenant_id=test_tenant.id,
        task_id=task.id,
        action_type="email_client",
        idempotency_key="approve:review:v1",
        status="sending",
    )
    db_session.add(legacy_run)
    await db_session.commit()
    row = SimpleNamespace(
        tenant_id=test_tenant.id,
        attempts=2,
        payload={
            "task_id": str(task.id),
            "from_status": "review",
            "to_status": "in_progress",
        },
    )

    result = await task_automation.run_task_automation_job(row)

    await db_session.refresh(legacy_run)
    assert result["delivery"] == "legacy_outcome_unknown"
    assert legacy_run.status == "failed"
    assert legacy_run.delivery_certainty == "outcome_unknown"
    assert legacy_run.completed_at is not None


@pytest.mark.asyncio
async def test_repeated_approval_never_sends_a_second_copy(
    db_session, test_tenant, test_user, monkeypatch
):
    """A double-clicked Approve must not email the client twice."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    for _ in range(3):
        await task_automation.run_task_automation(
            task.id,
            test_tenant.id,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
        )

    assert len(sender.calls) == 1
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 1


@pytest.mark.asyncio
async def test_concurrent_approvals_resolve_to_one_send(
    db_session, test_tenant, test_user, monkeypatch
):
    """Two racing transitions must collide on the claim, not both send."""
    import asyncio

    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await asyncio.gather(
        *(
            task_automation.run_task_automation(
                task.id,
                test_tenant.id,
                from_status="review",
                to_status="in_progress",
                actor_user_id=test_user.id,
            )
            for _ in range(4)
        )
    )

    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_unconfigured_delivery_is_not_recorded_as_sent(
    db_session, test_tenant, test_user, monkeypatch
):
    """A disabled mailer must never look like the client was contacted.

    EmailDeliveryResult is deliberately not a boolean for exactly this reason;
    recording UNCONFIGURED as success would both mislead the attorney and block
    the retry once delivery is fixed.
    """
    sender = _RecordingSender(result=EmailDeliveryResult.UNCONFIGURED)
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "unconfigured" in (run.error_message or "").lower()
    assert run.delivery_certainty == "not_attempted"

    # The draft survives so the approval can be retried after configuration.
    await db_session.refresh(task)
    assert task.pending_action is not None


@pytest.mark.asyncio
async def test_a_failed_run_is_not_retried_after_an_unchanged_status_cycle(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender(result=EmailDeliveryResult.FAILED)
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )
    sender.result = EmailDeliveryResult.SENT
    # A board round-trip changes the task version, but must not manufacture a
    # fresh send attempt while the exact approved payload is unchanged.
    task.status = "review"
    task.version += 1
    await db_session.commit()
    task.status = "in_progress"
    task.version += 1
    await db_session.commit()
    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert len(sender.calls) == 1
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_a_raising_handler_records_failure_without_corrupting_the_task(
    db_session, test_tenant, test_user, monkeypatch
):
    """The approval already committed; a send failure must not undo it."""
    sender = _RecordingSender(result=RuntimeError("smtp exploded"))
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "smtp exploded" in run.error_message

    await db_session.refresh(task)
    assert task.status == "in_progress"
    assert task.pending_action is not None


@pytest.mark.asyncio
async def test_automation_only_runs_on_approval_out_of_review(
    db_session, test_tenant, test_user, monkeypatch
):
    """A later manual transition must not re-trigger a send."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="in_progress",
        to_status="completed",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_a_task_without_a_pending_action_sends_nothing(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        matter_id=matter.id,
        title="Ordinary reviewed task",
        status="review",
        source="assistant",
    )
    db_session.add(task)
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []


@pytest.mark.asyncio
async def test_an_unknown_action_type_fails_closed(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        matter_id=matter.id,
        title="Unsupported action",
        status="in_progress",
        source="assistant",
        pending_action={"type": "wire_transfer", "amount": "100000"},
    )
    db_session.add(TenantSettings(tenant_id=test_tenant.id, enable_chat_actions=True))
    db_session.add(task)
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 1
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "unsupported action" in (run.error_message or "").lower()


@pytest.mark.asyncio
async def test_another_tenant_cannot_trigger_this_tenants_automation(
    db_session, test_tenant, test_user, monkeypatch
):
    """The tenant id is part of the lookup, not just the RLS context."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    await task_automation.run_task_automation(
        task.id,
        uuid.uuid4(),
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []


@pytest.mark.asyncio
async def test_editing_a_draft_cannot_change_its_recipients(
    client, db_session, test_tenant, test_user
):
    """The edit endpoint must not reopen what recipient resolution closed."""
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    response = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={
            "body": "Revised: please send it by Friday.",
            "to": ["attacker@evil.example"],
            "expected_version": task.version,
        },
    )

    assert response.status_code == 200
    await db_session.refresh(task)
    assert task.pending_action["body"] == "Revised: please send it by Friday."
    # The unknown field was ignored, not applied.
    assert task.pending_action["to"] == ["ops@redwood.example"]


@pytest.mark.asyncio
async def test_draft_edit_response_preserves_prior_delivery_attempt_history(
    client, db_session, test_tenant, test_user
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    snapshot = dict(task.pending_action)
    db_session.add(
        TaskAutomationRun(
            tenant_id=test_tenant.id,
            task_id=task.id,
            action_type="email_client",
            idempotency_key="prior-edit-audit",
            action_snapshot=snapshot,
            action_sha256=task_automation.action_payload_sha256(snapshot),
            status="failed",
            error_message="Outcome unknown",
            delivery_detail="Check Sent Items",
            delivery_certainty="outcome_unknown",
            completed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    response = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={
            "body": "Reviewed retry body.",
            "expected_version": task.version,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivery"]["status"] == "failed"
    assert body["delivery"]["delivery_certainty"] == "outcome_unknown"
    assert body["delivery_history"] == [body["delivery"]]


@pytest.mark.asyncio
async def test_pending_action_edit_requires_the_current_version(
    client, db_session, test_tenant, test_user
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    original_body = task.pending_action["body"]

    missing = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={"body": "Missing version"},
    )
    stale = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={"body": "Stale version", "expected_version": task.version + 1},
    )

    assert missing.status_code == 422
    assert stale.status_code == 409
    await db_session.refresh(task)
    assert task.pending_action["body"] == original_body


@pytest.mark.asyncio
async def test_editing_a_draft_rotates_the_idempotency_key(
    db_session, test_tenant, test_user
):
    """An edited draft may send again; an unchanged one may not.

    Unrelated task-version changes do not create a send attempt. A meaningful
    action edit does, so a failed-then-corrected draft can be reviewed again.
    """
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, version=1
    )

    first_key = task_automation.automation_idempotency_key(task, "review")
    task.version = 2
    second_key = task_automation.automation_idempotency_key(task, "review")
    edited_action = dict(task.pending_action)
    edited_action["body"] = "Corrected certificate request."
    task.pending_action = edited_action
    third_key = task_automation.automation_idempotency_key(task, "review")

    assert first_key == second_key
    assert third_key != second_key


@pytest.mark.asyncio
async def test_an_approved_draft_can_no_longer_be_edited(
    client, db_session, test_tenant, test_user
):
    """Editing after the fact would misrepresent what was actually sent."""
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    task.status = "in_progress"
    await db_session.commit()

    response = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={"body": "Too late.", "expected_version": task.version},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_another_tenant_cannot_edit_this_firms_draft(
    client, db_session, test_tenant, test_user
):
    from app.models.tenant import Tenant

    other = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain=f"other-{uuid.uuid4().hex[:8]}.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    foreign_task = Task(
        id=uuid.uuid4(),
        tenant_id=other.id,
        title="Their drafted email",
        status="review",
        source="assistant",
        pending_action={
            "type": "email_client",
            "to": ["their-client@other.example"],
            "subject": "Confidential",
            "body": "Confidential.",
            "matter_id": str(uuid.uuid4()),
            "source_ids": [],
        },
    )
    db_session.add(foreign_task)
    await db_session.commit()

    response = await client.patch(
        f"/api/tasks/{foreign_task.id}/pending-action",
        json={"body": "Injected content.", "expected_version": 1},
    )

    assert response.status_code == 404
    await db_session.refresh(foreign_task)
    assert foreign_task.pending_action["body"] == "Confidential."


@pytest.mark.parametrize("to_status", ["cancelled", "waiting", "completed", "review"])
@pytest.mark.asyncio
async def test_only_approval_into_active_work_sends(
    db_session, test_tenant, test_user, monkeypatch, to_status
):
    """Leaving Review is not the same as approving.

    Cancelling a drafted client email must never send it — that is the worst
    possible inversion of the attorney's intent. Nor should parking it in
    Waiting, or marking the task done without acting on the draft.
    """
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status=to_status,
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_cancelling_a_drafted_email_over_http_sends_nothing(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """End-to-end guard on the real approval surface."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "cancelled",
            "expected_version": task.version,
            "reason": "Client called instead; no letter needed.",
        },
    )

    assert response.status_code == 200
    assert sender.calls == []


@pytest.mark.asyncio
async def test_approving_over_http_queues_then_the_worker_sends(
    client, db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 200
    assert sender.calls == []
    from app.models.durable_job import DurableJob

    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.tenant_id == test_tenant.id)
    )
    await task_automation.run_task_automation_job(job)
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_the_generic_patch_queues_the_same_durable_execution(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """Both status-changing endpoints must agree on what approval means.

    Before this was centralized, PATCH could move a task out of Review — which
    the board treats as approval — while silently never running the action, so
    an attorney would believe a client had been emailed.
    """
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 200
    assert sender.calls == []
    from app.models.durable_job import DurableJob

    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.tenant_id == test_tenant.id)
    )
    await task_automation.run_task_automation_job(job)
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_approval_fails_closed_when_recipient_address_changed_after_draft(
    client, db_session, test_tenant, test_user
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    contact_id = uuid.UUID(task.pending_action["recipient_bindings"][0]["contact_id"])
    contact = await db_session.get(Contact, contact_id)
    contact.email = "new-address@redwood.example"
    await db_session.commit()

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 409
    assert "email address changed" in response.json()["detail"]["message"]
    await db_session.refresh(task)
    assert task.status == "review"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(TaskAutomationRun)
            .where(TaskAutomationRun.task_id == task.id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_worker_rechecks_recipient_binding_immediately_before_send(
    client, db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    approved = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )
    assert approved.status_code == 200

    await db_session.refresh(task)
    contact_id = uuid.UUID(task.pending_action["recipient_bindings"][0]["contact_id"])
    contact = await db_session.get(Contact, contact_id)
    contact.email = "changed-before-worker@redwood.example"
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert run.delivery_certainty == "not_attempted"
    assert "recipient" in (run.delivery_detail or "").lower()


@pytest.mark.parametrize("with_delivery_audit", [False, True])
@pytest.mark.asyncio
async def test_outbound_task_context_cannot_be_relinked(
    with_delivery_audit,
    client,
    db_session,
    test_tenant,
    test_user,
    monkeypatch,
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    other_matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session,
        test_tenant,
        test_user,
        matter,
        status="in_progress" if with_delivery_audit else "review",
    )
    if with_delivery_audit:
        await task_automation.run_task_automation(
            task.id,
            test_tenant.id,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
        )
        await db_session.refresh(task)
        assert task.pending_action is None

    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"matter_id": str(other_matter.id), "expected_version": task.version},
    )

    assert response.status_code == 409
    assert "cannot be relinked" in response.json()["detail"]
    await db_session.refresh(task)
    assert task.matter_id == matter.id


@pytest.mark.parametrize("endpoint", ["transition", "patch"])
@pytest.mark.parametrize("active_status", ["queued", "sending"])
@pytest.mark.asyncio
async def test_approval_is_blocked_while_an_earlier_delivery_is_active(
    endpoint,
    active_status,
    client,
    db_session,
    test_tenant,
    test_user,
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    snapshot = dict(task.pending_action)
    db_session.add(
        TaskAutomationRun(
            tenant_id=test_tenant.id,
            task_id=task.id,
            action_type="email_client",
            idempotency_key=f"earlier-{active_status}",
            action_snapshot=snapshot,
            action_sha256=task_automation.action_payload_sha256(snapshot),
            status=active_status,
            triggered_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    payload = {
        "to_status" if endpoint == "transition" else "status": "in_progress",
        "expected_version": task.version,
    }
    if endpoint == "transition":
        response = await client.post(f"/api/tasks/{task.id}/transition", json=payload)
    else:
        response = await client.patch(f"/api/tasks/{task.id}", json=payload)

    assert response.status_code == 409
    assert "still queued or in progress" in response.json()["detail"]["message"].lower()
    await db_session.refresh(task)
    assert task.status == "review"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(TaskAutomationRun)
            .where(TaskAutomationRun.task_id == task.id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_changed_draft_retry_requires_explicit_delivery_risk_acknowledgment(
    client, db_session, test_tenant, test_user
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    failed_snapshot = dict(task.pending_action)
    db_session.add(
        TaskAutomationRun(
            tenant_id=test_tenant.id,
            task_id=task.id,
            action_type="email_client",
            idempotency_key="prior-unconfirmed",
            action_snapshot=failed_snapshot,
            action_sha256=task_automation.action_payload_sha256(failed_snapshot),
            status="failed",
            error_message="Delivery not confirmed: outcome unknown",
            delivery_detail="Check Sent Items before retrying",
            delivery_certainty="outcome_unknown",
            triggered_by_user_id=test_user.id,
            completed_at=datetime.now(timezone.utc),
        )
    )
    changed_snapshot = {
        **failed_snapshot,
        "body": "Please send the current certificate. Retry reviewed by counsel.",
    }
    task.pending_action = changed_snapshot
    task.version += 1
    await db_session.commit()

    rejected = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )
    assert rejected.status_code == 409
    assert "explicitly acknowledge" in rejected.json()["detail"]["message"]

    await db_session.refresh(task)
    approved = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": task.version,
            "acknowledge_prior_delivery_risk": True,
        },
    )

    assert approved.status_code == 200
    runs = (
        (
            await db_session.execute(
                select(TaskAutomationRun)
                .where(TaskAutomationRun.task_id == task.id)
                .order_by(TaskAutomationRun.created_at, TaskAutomationRun.id)
            )
        )
        .scalars()
        .all()
    )
    assert [run.status for run in runs] == ["failed", "queued"]
    assert runs[-1].action_snapshot == changed_snapshot


@pytest.mark.asyncio
async def test_exact_confirmed_no_send_payload_can_be_explicitly_reapproved(
    client, db_session, test_tenant, test_user
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    snapshot = dict(task.pending_action)
    db_session.add(
        TaskAutomationRun(
            tenant_id=test_tenant.id,
            task_id=task.id,
            action_type="email_client",
            idempotency_key="same-failed-payload",
            action_snapshot=snapshot,
            action_sha256=task_automation.action_payload_sha256(snapshot),
            status="failed",
            error_message="Mailbox authorization is missing",
            delivery_detail="Reconnect the mailbox before retrying",
            delivery_certainty="not_attempted",
            completed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": task.version,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivery"]["status"] == "queued"
    assert [item["status"] for item in body["delivery_history"]] == [
        "queued",
        "failed",
    ]
    assert body["delivery_history"][1]["delivery_certainty"] == "not_attempted"
    runs = (
        (
            await db_session.execute(
                select(TaskAutomationRun)
                .where(TaskAutomationRun.task_id == task.id)
                .order_by(TaskAutomationRun.created_at, TaskAutomationRun.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 2
    assert runs[0].action_sha256 == runs[1].action_sha256
    assert runs[0].idempotency_key != runs[1].idempotency_key


@pytest.mark.asyncio
async def test_approval_records_a_queued_delivery_in_the_same_transaction(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """Durability: the send intent must be committed with the approval.

    A process that dies between the two previously lost the send entirely while
    telling the attorney the work was approved.
    """
    from app.models.durable_job import DurableJob
    from app.services.task_automation import TASK_AUTOMATION_JOB

    # The HTTP endpoint only queues; a leased worker is the sole executor.
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )
    assert response.status_code == 200

    # A durable job survives the process, so the send is recoverable.
    job = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == test_tenant.id,
            DurableJob.kind == TASK_AUTOMATION_JOB,
        )
    )
    assert job is not None
    assert job.payload["task_id"] == str(task.id)
    assert job.payload["approval_idempotency_key"].startswith("approve:review:sha256:")
    assert sender.calls == []


@pytest.mark.parametrize("superseding_status", ["cancelled", "review"])
@pytest.mark.asyncio
async def test_queued_approval_superseded_before_worker_never_sends(
    db_session,
    test_tenant,
    test_user,
    monkeypatch,
    superseding_status,
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    await db_session.commit()

    task.status = superseding_status
    await db_session.commit()
    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
        approval_idempotency_key=approval_key,
    )

    assert sender.calls == []
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "superseded" in (run.error_message or "").lower()


@pytest.mark.asyncio
async def test_execution_kill_switch_blocks_a_queued_send(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    settings = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
    )
    settings.enable_chat_actions = False
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
        approval_idempotency_key=approval_key,
    )

    assert sender.calls == []
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "disabled" in (run.error_message or "").lower()


@pytest.mark.asyncio
async def test_execution_kill_switch_is_rechecked_after_worker_claim(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    original_claim = task_automation._claim_run

    async def claim_then_disable(*args, **kwargs):
        run = await original_claim(*args, **kwargs)
        async with task_automation.async_session_maker() as admin_db:
            await task_automation.set_tenant_context(admin_db, str(test_tenant.id))
            settings = await admin_db.scalar(
                select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
            )
            settings.enable_chat_actions = False
            await admin_db.commit()
        return run

    monkeypatch.setattr(task_automation, "_claim_run", claim_then_disable)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert run.delivery_certainty == "not_attempted"
    assert "disabled" in (run.delivery_detail or "").lower()


@pytest.mark.asyncio
async def test_worker_uses_the_frozen_approval_key_after_version_changes(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, version=3
    )
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    task.version = 99
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
        approval_idempotency_key=approval_key,
    )

    assert len(sender.calls) == 1
    runs = (
        (
            await db_session.execute(
                select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
            )
        )
        .scalars()
        .all()
    )
    assert [run.idempotency_key for run in runs] == [approval_key]


@pytest.mark.asyncio
async def test_worker_rejects_a_superseded_payload_at_the_send_boundary(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    newer_action = dict(task.pending_action)
    newer_action["body"] = "A newer attorney-reviewed draft."
    task.pending_action = newer_action
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
        approval_idempotency_key=approval_key,
    )

    assert sender.calls == []
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "superseded" in (run.delivery_detail or "").lower()
    await db_session.refresh(task)
    assert task.pending_action == newer_action


@pytest.mark.asyncio
async def test_pending_action_approval_requires_a_version_on_both_endpoints(
    client, db_session, test_tenant, test_user
):
    matter = await _matter(db_session, test_tenant, test_user)
    transition_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    patch_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    transition_response = await client.post(
        f"/api/tasks/{transition_task_row.id}/transition",
        json={"to_status": "in_progress"},
    )
    patch_response = await client.patch(
        f"/api/tasks/{patch_task_row.id}",
        json={"status": "in_progress"},
    )

    assert transition_response.status_code == 422
    assert patch_response.status_code == 409
    await db_session.refresh(transition_task_row)
    await db_session.refresh(patch_task_row)
    assert transition_task_row.status == "review"
    assert patch_task_row.status == "review"


@pytest.mark.asyncio
async def test_non_reviewer_cannot_approve_or_edit_a_pending_action(
    client, db_session, test_tenant, test_user
):
    test_user.role = "user"
    reviewer = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"reviewer-{uuid.uuid4().hex[:8]}@testfirm.com",
        full_name="Assigned Reviewer",
        role="user",
        is_active=True,
    )
    db_session.add(reviewer)
    matter = await _matter(db_session, test_tenant, test_user)
    transition_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    transition_task_row.reviewer_user_id = reviewer.id
    patch_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    patch_task_row.reviewer_user_id = reviewer.id
    edit_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    edit_task_row.reviewer_user_id = reviewer.id
    await db_session.commit()

    transition_response = await client.post(
        f"/api/tasks/{transition_task_row.id}/transition",
        json={
            "to_status": "in_progress",
            "expected_version": transition_task_row.version,
        },
    )
    patch_response = await client.patch(
        f"/api/tasks/{patch_task_row.id}",
        json={
            "status": "in_progress",
            "expected_version": patch_task_row.version,
        },
    )
    edit_response = await client.patch(
        f"/api/tasks/{edit_task_row.id}/pending-action",
        json={"body": "Unauthorized edit", "expected_version": edit_task_row.version},
    )
    cancel_response = await client.post(
        f"/api/tasks/{transition_task_row.id}/transition",
        json={
            "to_status": "cancelled",
            "expected_version": transition_task_row.version,
            "reason": "Unauthorized cancellation",
        },
    )
    waiting_response = await client.patch(
        f"/api/tasks/{patch_task_row.id}",
        json={
            "status": "waiting",
            "expected_version": patch_task_row.version,
            "waiting_reason": "Unauthorized supersession",
        },
    )

    assert transition_response.status_code == 403
    assert patch_response.status_code == 403
    assert edit_response.status_code == 403
    assert cancel_response.status_code == 403
    assert waiting_response.status_code == 403


@pytest.mark.parametrize("starting_status", ["in_progress", "waiting", "cancelled"])
@pytest.mark.asyncio
async def test_non_reviewer_cannot_reopen_and_self_assign_an_action(
    client, db_session, test_tenant, test_user, starting_status
):
    test_user.role = "user"
    reviewer = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"reviewer-{uuid.uuid4().hex[:8]}@testfirm.com",
        full_name="Assigned Reviewer",
        role="user",
        is_active=True,
    )
    db_session.add(reviewer)
    matter = await _matter(db_session, test_tenant, test_user)
    transition_task_row = await _approved_email_task(
        db_session,
        test_tenant,
        test_user,
        matter,
        status=starting_status,
    )
    transition_task_row.reviewer_user_id = reviewer.id
    patch_task_row = await _approved_email_task(
        db_session,
        test_tenant,
        test_user,
        matter,
        status=starting_status,
    )
    patch_task_row.reviewer_user_id = reviewer.id
    await db_session.commit()

    transition_response = await client.post(
        f"/api/tasks/{transition_task_row.id}/transition",
        json={
            "to_status": "review",
            "expected_version": transition_task_row.version,
            "reviewer_user_id": str(test_user.id),
        },
    )
    patch_response = await client.patch(
        f"/api/tasks/{patch_task_row.id}",
        json={
            "status": "review",
            "expected_version": patch_task_row.version,
            "reviewer_user_id": str(test_user.id),
        },
    )

    assert transition_response.status_code == 403
    assert patch_response.status_code == 403
    await db_session.refresh(transition_task_row)
    await db_session.refresh(patch_task_row)
    assert transition_task_row.reviewer_user_id == reviewer.id
    assert patch_task_row.reviewer_user_id == reviewer.id


@pytest.mark.asyncio
async def test_same_state_review_without_reviewer_preserves_action_owner(
    client, db_session, test_tenant, test_user
):
    test_user.role = "user"
    matter = await _matter(db_session, test_tenant, test_user)
    transition_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    patch_task_row = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    transition_response = await client.post(
        f"/api/tasks/{transition_task_row.id}/transition",
        json={
            "to_status": "review",
            "expected_version": transition_task_row.version,
        },
    )
    patch_response = await client.patch(
        f"/api/tasks/{patch_task_row.id}",
        json={
            "status": "review",
            "expected_version": patch_task_row.version,
        },
    )

    assert transition_response.status_code == 200
    assert patch_response.status_code == 200
    await db_session.refresh(transition_task_row)
    await db_session.refresh(patch_task_row)
    assert transition_task_row.reviewer_user_id == test_user.id
    assert patch_task_row.reviewer_user_id == test_user.id


@pytest.mark.asyncio
async def test_assigned_capable_non_admin_reviewer_can_approve(
    client, db_session, test_tenant, test_user
):
    test_user.role = "user"
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )
    await db_session.commit()
    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 200
    assert response.json()["reviewer_user_id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_assignment_without_legal_approval_capability_cannot_execute(
    client, db_session, test_tenant, test_user, monkeypatch
):
    async def deny(_db, _actor_user_id):
        return False

    monkeypatch.setattr(task_automation, "_actor_can_approve_legal_work", deny)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="review"
    )

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 409
    await db_session.refresh(task)
    assert task.status == "review"
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_email_delivery_uses_a_separate_session_from_the_task_lock(
    db_session, test_tenant, test_user, monkeypatch
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    observed = {}

    async def fake_send(delivery_db, **_kwargs):
        observed["delivery_db"] = delivery_db
        return ConnectedMailDelivery(EmailDeliveryResult.SENT, "sent", provider="test")

    monkeypatch.setattr(task_automation, "send_client_email", fake_send)
    result = await task_automation._run_email_client(
        db_session, task, dict(task.pending_action), test_user.id
    )

    assert result.succeeded is True
    assert result.provider == "test"
    assert observed["delivery_db"] is not db_session


@pytest.mark.asyncio
async def test_provider_delivery_evidence_is_persisted(
    db_session, test_tenant, test_user, monkeypatch
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    async def fake_send(_delivery_db, **_kwargs):
        return ConnectedMailDelivery(
            EmailDeliveryResult.SENT,
            "Google accepted the message",
            provider="google",
            provider_message_id="gmail-message-123",
        )

    monkeypatch.setattr(task_automation, "send_client_email", fake_send)
    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "sent"
    assert run.delivery_certainty == "confirmed_sent"
    assert run.provider == "google"
    assert run.provider_message_id == "gmail-message-123"
    assert run.delivery_detail == "Google accepted the message"


@pytest.mark.asyncio
async def test_runtime_role_worker_survives_claim_commit_under_force_rls(
    db_session, test_tenant, test_user, monkeypatch
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")

    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    await db_session.commit()

    async def fake_send(_delivery_db, **_kwargs):
        return ConnectedMailDelivery(
            EmailDeliveryResult.SENT, "runtime-role accepted", provider="test"
        )

    engine = create_async_engine(url, pool_pre_ping=True)
    runtime_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_automation, "async_session_maker", runtime_maker)
    monkeypatch.setattr(task_automation, "send_client_email", fake_send)
    try:
        await task_automation.run_task_automation(
            task.id,
            test_tenant.id,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
            approval_idempotency_key=approval_key,
        )
    finally:
        await engine.dispose()

    await db_session.refresh(task)
    run = await db_session.scalar(
        select(TaskAutomationRun).where(
            TaskAutomationRun.task_id == task.id,
            TaskAutomationRun.idempotency_key == approval_key,
        )
    )
    await db_session.refresh(run)
    assert run.status == "sent"
    assert task.pending_action is None


@pytest.mark.asyncio
async def test_the_worker_delivers_a_queued_send(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    class _Job:
        tenant_id = test_tenant.id
        payload = {
            "task_id": str(task.id),
            "from_status": "review",
            "to_status": "in_progress",
            "actor_user_id": str(test_user.id),
            "approval_idempotency_key": task_automation.automation_idempotency_key(
                task,
                "review",
            ),
        }

    result = await task_automation.run_task_automation_job(_Job())

    assert result["task_id"] == str(task.id)
    assert len(sender.calls) == 1
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "sent"


@pytest.mark.asyncio
async def test_worker_infrastructure_failure_escapes_for_durable_retry(
    db_session, test_tenant, test_user, monkeypatch
):
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    async def broken_claim(*_args, **_kwargs):
        raise RuntimeError("database unavailable while claiming automation")

    monkeypatch.setattr(task_automation, "_claim_run", broken_claim)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await task_automation.run_task_automation(
            task.id,
            test_tenant.id,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
        )

    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(TaskAutomationRun)
            .where(TaskAutomationRun.task_id == task.id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_retry_of_interrupted_sending_run_is_terminal_and_not_resent(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    approval_key = task_automation.automation_idempotency_key(task, "review")
    await task_automation.enqueue_automation_run(
        db_session,
        task,
        from_status="review",
        actor_user_id=test_user.id,
        idempotency_key=approval_key,
    )
    await db_session.commit()
    claimed = await task_automation._claim_run(
        db_session,
        task,
        action_type="email_client",
        idempotency_key=approval_key,
        actor_user_id=test_user.id,
    )
    assert claimed is not None and claimed.status == "sending"

    class _RetriedJob:
        tenant_id = test_tenant.id
        attempts = 2
        payload = {
            "task_id": str(task.id),
            "from_status": "review",
            "to_status": "in_progress",
            "actor_user_id": str(test_user.id),
            "approval_idempotency_key": approval_key,
        }

    result = await task_automation.run_task_automation_job(_RetriedJob())

    assert result["delivery"] == "outcome_unknown"
    assert sender.calls == []
    await db_session.refresh(claimed)
    await db_session.refresh(task)
    assert claimed.status == "failed"
    assert "delivery not confirmed" in (claimed.error_message or "").lower()
    assert "sent items" in (claimed.error_message or "").lower()
    assert task.pending_action is not None


@pytest.mark.asyncio
async def test_the_worker_does_not_resend_an_already_sent_approval(
    db_session, test_tenant, test_user, monkeypatch
):
    """A replayed job cannot reclaim an approval already recorded as sent."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    class _Job:
        tenant_id = test_tenant.id
        payload = {
            "task_id": str(task.id),
            "from_status": "review",
            "to_status": "in_progress",
            "actor_user_id": str(test_user.id),
        }

    await task_automation.run_task_automation_job(_Job())

    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_a_run_in_flight_is_never_claimed_twice(
    db_session, test_tenant, test_user
):
    """A 'sending' run must block a second claim, not just a 'sent' one."""
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    key = task_automation.automation_idempotency_key(task, "review")

    first = await task_automation._claim_run(
        db_session,
        task,
        action_type="email_client",
        idempotency_key=key,
        actor_user_id=test_user.id,
    )
    assert first is not None and first.status == "sending"

    second = await task_automation._claim_run(
        db_session,
        task,
        action_type="email_client",
        idempotency_key=key,
        actor_user_id=test_user.id,
    )
    assert second is None


@pytest.mark.asyncio
async def test_delivery_state_is_reported_on_the_task(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """The attorney needs the real outcome, not just "approved"."""
    sender = _RecordingSender(result=EmailDeliveryResult.FAILED)
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    body = (await client.get(f"/api/tasks/{task.id}")).json()

    assert body["delivery"]["status"] == "failed"
    assert body["delivery"]["action_type"] == "email_client"
    assert body["delivery"]["error_message"]
    assert body["delivery"]["action_snapshot"]["to"] == ["ops@redwood.example"]
    assert len(body["delivery"]["action_sha256"]) == 64
    assert body["delivery"]["delivery_detail"]
    assert body["delivery"]["delivery_certainty"] == "outcome_unknown"
    assert body["delivery_history"] == [body["delivery"]]


@pytest.mark.parametrize("endpoint", ["transition", "patch"])
@pytest.mark.asyncio
async def test_stale_approval_conflict_includes_immutable_delivery_evidence(
    endpoint, client, db_session, test_tenant, test_user, monkeypatch
):
    """A racing tab must learn that the reviewed email was actually sent."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    reviewed_version = task.version
    approved_snapshot = dict(task.pending_action)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )
    await db_session.refresh(task)
    task.version = reviewed_version + 1
    await db_session.commit()

    if endpoint == "transition":
        response = await client.post(
            f"/api/tasks/{task.id}/transition",
            json={
                "to_status": "in_progress",
                "expected_version": reviewed_version,
            },
        )
    else:
        response = await client.patch(
            f"/api/tasks/{task.id}",
            json={
                "status": "in_progress",
                "expected_version": reviewed_version,
            },
        )

    assert response.status_code == 409
    current = response.json()["detail"]["current_task"]
    assert current["pending_action"] is None
    assert current["delivery"]["status"] == "sent"
    assert current["delivery"]["action_snapshot"] == approved_snapshot
    assert current["delivery"]["provider"] == "smtp"
    assert len(current["delivery"]["action_sha256"]) == 64
