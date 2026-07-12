"""Task assignment notes, closure reasons, and customer-history documentation."""

import uuid
from datetime import date

import pytest
from sqlalchemy import event, func, select

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.task import Task
from app.models.user import User
from app.services import email as email_module


async def _make_contact(db_session, test_tenant, test_user):
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Caller",
        phone="701-555-3333",
        created_by_user_id=test_user.id,
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    return contact


async def _make_assignee(db_session, test_tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="paralegal@testfirm.com",
        full_name="Pat Paralegal",
        role="attorney",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _history_rows(db_session, tenant_id, external_ref):
    rows = (
        (
            await db_session.execute(
                select(CommunicationLog).where(
                    CommunicationLog.tenant_id == tenant_id,
                    CommunicationLog.external_ref == external_ref,
                )
            )
        )
        .scalars()
        .all()
    )
    return rows


@pytest.mark.asyncio
async def test_create_assigned_task_records_note_and_history(
    client, db_session, test_tenant, test_user
):
    contact = await _make_contact(db_session, test_tenant, test_user)
    assignee = await _make_assignee(db_session, test_tenant)

    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Call back Jane Caller",
            "task_type": "follow_up",
            "priority": "urgent",
            "contact_id": str(contact.id),
            "assigned_to_user_id": str(assignee.id),
            "assignment_note": "She prefers afternoon calls; retainer question.",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    # The assigner's note is persisted on the task itself.
    assert "She prefers afternoon calls" in body["description"]
    # assignment_note is not a Task column and must not leak into the response.
    assert "assignment_note" not in body

    # The assignment is documented in the contact's communication history.
    rows = await _history_rows(
        db_session, test_tenant.id, f"task:{body['id']}:assigned"
    )
    assert len(rows) == 1
    log = rows[0]
    assert log.contact_id == contact.id
    assert log.subject.startswith("Task assigned:")
    assert "Pat Paralegal" in (log.body or "")
    assert "She prefers afternoon calls" in (log.body or "")


@pytest.mark.asyncio
async def test_cancel_requires_reason_and_documents_history(
    client, db_session, test_tenant, test_user
):
    contact = await _make_contact(db_session, test_tenant, test_user)
    task = Task(
        tenant_id=test_tenant.id,
        title="Follow up on quote",
        task_type="follow_up",
        status="pending",
        priority="high",
        due_date=date.today(),
        contact_id=contact.id,
        assigned_to_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    # Cancelling without a reason is rejected.
    resp = await client.patch(f"/api/tasks/{task.id}", json={"status": "cancelled"})
    assert resp.status_code == 422

    resp = await client.patch(
        f"/api/tasks/{task.id}",
        json={"status": "cancelled", "closed_reason": "Caller retained other counsel"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["closed_reason"] == "Caller retained other counsel"
    assert body["closed_by_user_id"] == str(test_user.id)

    rows = await _history_rows(db_session, test_tenant.id, f"task:{task.id}:cancelled")
    assert len(rows) == 1
    assert "Caller retained other counsel" in (rows[0].body or "")

    # Reopening clears the closure record.
    resp = await client.patch(f"/api/tasks/{task.id}", json={"status": "pending"})
    assert resp.status_code == 200
    assert resp.json()["closed_reason"] is None
    assert resp.json()["closed_by_user_id"] is None


@pytest.mark.asyncio
async def test_complete_with_reason_sets_closure_fields(
    client, db_session, test_tenant, test_user
):
    task = Task(
        tenant_id=test_tenant.id,
        title="Send engagement letter",
        status="in_progress",
        priority="medium",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    # Completing without a reason stays allowed (checkbox flow).
    resp = await client.patch(f"/api/tasks/{task.id}", json={"status": "completed"})
    assert resp.status_code == 200
    assert resp.json()["closed_reason"] is None
    assert resp.json()["closed_by_user_id"] == str(test_user.id)
    assert resp.json()["completed_at"] is not None


@pytest.mark.asyncio
async def test_reassign_with_note_resets_receipt_and_documents_history(
    client, db_session, test_tenant, test_user
):
    contact = await _make_contact(db_session, test_tenant, test_user)
    assignee = await _make_assignee(db_session, test_tenant)
    task = Task(
        tenant_id=test_tenant.id,
        title="Call back Jane Caller",
        task_type="follow_up",
        status="pending",
        priority="urgent",
        contact_id=contact.id,
        assigned_to_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    # Assignee (test_user) views it first.
    view = await client.post(f"/api/tasks/{task.id}/view")
    assert view.json()["viewed_at"] is not None

    resp = await client.patch(
        f"/api/tasks/{task.id}",
        json={
            "assigned_to_user_id": str(assignee.id),
            "assignment_note": "Taking over while I'm in trial this week.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_to_user_id"] == str(assignee.id)
    # New assignee has not seen the task.
    assert body["viewed_at"] is None
    assert "Taking over while I'm in trial" in body["description"]

    rows = await _history_rows(db_session, test_tenant.id, f"task:{task.id}:reassigned")
    assert len(rows) == 1
    assert rows[0].subject.startswith("Task reassigned:")
    assert "Pat Paralegal" in (rows[0].body or "")


@pytest.mark.asyncio
async def test_unassign_is_row_locked_and_records_contact_history(
    client, db_session, test_tenant, test_user
):
    contact = await _make_contact(db_session, test_tenant, test_user)
    assignee = await _make_assignee(db_session, test_tenant)
    task = Task(
        tenant_id=test_tenant.id,
        title="Return prospective client call",
        task_type="follow_up",
        status="pending",
        priority="urgent",
        contact_id=contact.id,
        assigned_to_user_id=assignee.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    statements: list[str] = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    sync_engine = db_session.bind.sync_engine
    event.listen(sync_engine, "before_cursor_execute", capture_sql)
    try:
        response = await client.patch(
            f"/api/tasks/{task.id}",
            json={
                "assigned_to_user_id": None,
                "assignment_note": "Return to the unassigned intake queue.",
            },
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", capture_sql)

    assert response.status_code == 200, response.text
    assert response.json()["assigned_to_user_id"] is None
    assert any(
        "FROM tasks" in statement and "FOR UPDATE" in statement.upper()
        for statement in statements
    )
    rows = await _history_rows(db_session, test_tenant.id, f"task:{task.id}:unassigned")
    assert len(rows) == 1
    assert rows[0].subject.startswith("Task unassigned:")
    assert "Previously assigned to: Pat Paralegal" in (rows[0].body or "")
    assert "Unassigned by: Test Attorney" in (rows[0].body or "")
    assert "Return to the unassigned intake queue" in (rows[0].body or "")


@pytest.mark.asyncio
async def test_log_contact_writes_customer_history(
    client, db_session, test_tenant, test_user
):
    contact = await _make_contact(db_session, test_tenant, test_user)
    task = Task(
        tenant_id=test_tenant.id,
        title="Call back Jane Caller",
        task_type="follow_up",
        status="pending",
        priority="urgent",
        contact_id=contact.id,
        assigned_to_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    resp = await client.post(
        f"/api/tasks/{task.id}/contacted",
        json={"method": "call", "note": "Reached her; consult booked for Tuesday."},
    )
    assert resp.status_code == 200

    rows = await _history_rows(db_session, test_tenant.id, f"task:{task.id}:contacted")
    assert len(rows) == 1
    log = rows[0]
    assert log.channel == "call"
    assert log.direction == "outbound"
    assert log.contact_id == contact.id
    assert "consult booked for Tuesday" in (log.body or "")


@pytest.mark.asyncio
async def test_task_without_contact_writes_no_history(
    client, db_session, test_tenant, test_user
):
    assignee = await _make_assignee(db_session, test_tenant)
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Internal admin chore",
            "assigned_to_user_id": str(assignee.id),
            "assignment_note": "No customer attached.",
        },
    )
    assert resp.status_code == 201
    rows = await _history_rows(
        db_session, test_tenant.id, f"task:{resp.json()['id']}:assigned"
    )
    assert rows == []


@pytest.mark.asyncio
async def test_disabled_email_preserves_task_assignment_as_durable_work(
    client, db_session, test_tenant, monkeypatch
):
    assignee = await _make_assignee(db_session, test_tenant)
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Assignment must notify",
            "assigned_to_user_id": str(assignee.id),
        },
    )

    assert resp.status_code == 201
    assert resp.json()["assigned_to_user_id"] == str(assignee.id)
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.asyncio
async def test_disabled_email_maps_manual_reminder_to_503(
    client, db_session, test_tenant, monkeypatch
):
    assignee = await _make_assignee(db_session, test_tenant)
    task = Task(
        tenant_id=test_tenant.id,
        title="Call the client",
        assigned_to_user_id=assignee.id,
    )
    db_session.add(task)
    await db_session.commit()
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    resp = await client.post(f"/api/tasks/{task.id}/remind")

    assert resp.status_code == 503
    assert "outbound email is unavailable" in resp.json()["detail"]
