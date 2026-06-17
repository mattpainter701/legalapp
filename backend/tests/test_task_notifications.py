import asyncio
import uuid
from datetime import date

import pytest

from app.models.task import Task
from app.models.user import User
from app.services import task_notifications


@pytest.mark.asyncio
async def test_notify_task_created_pushes_calendar_and_assignment_email(
    db_session, test_tenant
):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="partner@testfirm.com",
        full_name="Partner User",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    task = Task(
        tenant_id=test_tenant.id,
        title="Urgent intake follow-up: Jane Doe",
        description="Call Jane Doe back.",
        task_type="follow_up",
        priority="urgent",
        due_date=date.today(),
        assigned_to_user_id=user.id,
        created_by_user_id=user.id,
        source="intake_dashboard",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    calendar_calls = []
    email_calls = []

    async def fake_calendar_upsert(**kwargs):
        calendar_calls.append(kwargs)
        return {"id": "calendar-event"}

    async def fake_assignment_email(**kwargs):
        email_calls.append(kwargs)
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        task_notifications.google_calendar,
        "upsert_task_event",
        fake_calendar_upsert,
    )
    monkeypatch.setattr(
        task_notifications.microsoft_calendar,
        "upsert_task_event",
        fake_calendar_upsert,
    )
    monkeypatch.setattr(
        task_notifications.email_service,
        "send_task_assignment_alert",
        fake_assignment_email,
    )
    try:
        sent = await task_notifications.notify_task_created(
            db_session, task, str(test_tenant.id)
        )
        await asyncio.sleep(0)
    finally:
        monkeypatch.undo()

    assert sent is True
    assert len(email_calls) == 1
    assert email_calls[0]["to_email"] == "partner@testfirm.com"
    assert email_calls[0]["priority"] == "urgent"
    assert email_calls[0]["task_type"] == "follow_up"
    assert len(calendar_calls) == 2
    assert {call["user_id"] for call in calendar_calls} == {str(user.id)}
