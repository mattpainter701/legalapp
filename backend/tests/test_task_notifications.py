import asyncio
import uuid
from datetime import date

import pytest

from app.models.contact import Contact
from app.models.task import Task
from app.models.user import User
from app.services import task_notifications
from app.services.email import EmailService
from app.services.task_notifications import _calendar_description


@pytest.mark.asyncio
async def test_notify_task_created_pushes_calendar_and_assignment_email(
    db_session, test_tenant
):
    assignee = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="partner@testfirm.com",
        full_name="Partner User",
        role="admin",
        is_active=True,
    )
    creator = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="reception@testfirm.com",
        full_name="Reception User",
        role="staff",
        is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Doe",
        phone="701-555-2222",
        created_by_user_id=creator.id,
    )
    db_session.add_all([assignee, creator])
    await db_session.flush()
    db_session.add(contact)
    await db_session.flush()

    task = Task(
        tenant_id=test_tenant.id,
        title="Urgent intake follow-up: Jane Doe",
        description="Call Jane Doe back.",
        task_type="follow_up",
        priority="urgent",
        due_date=date.today(),
        assigned_to_user_id=assignee.id,
        created_by_user_id=creator.id,
        contact_id=contact.id,
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
    assert email_calls[0]["assignee_name"] == "Partner User"
    assert email_calls[0]["created_by_name"] == "Reception User"
    assert email_calls[0]["customer_name"] == "Jane Doe"
    assert email_calls[0]["source"] == "intake_dashboard"
    assert email_calls[0]["description"] == "Call Jane Doe back."
    assert email_calls[0]["task_url"].endswith(f"/tasks/{task.id}")
    assert len(calendar_calls) == 2
    assert {call["user_id"] for call in calendar_calls} == {str(assignee.id)}
    assert all("Created by: Reception User" in call["description"] for call in calendar_calls)
    assert all(f"Task link: " in call["description"] for call in calendar_calls)


@pytest.mark.asyncio
async def test_task_assignment_email_includes_ticket_fields_and_escapes_html():
    service = EmailService()
    sent = []

    async def fake_send_email(to_emails, subject, html_body, text_body):
        sent.append(
            {
                "to_emails": to_emails,
                "subject": subject,
                "html_body": html_body,
                "text_body": text_body,
            }
        )
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service, "send_email", fake_send_email)
    try:
        ok = await service.send_task_assignment_alert(
            to_email="partner@testfirm.com",
            task_title="Urgent intake follow-up: Jane <Doe>",
            due_date="2026-06-17 15:30",
            priority="urgent",
            task_type="follow_up",
            description="Caller needs divorce help.\n<script>alert(1)</script>",
            assignee_name="Partner User",
            created_by_name="Reception User",
            created_at="June 17, 2026 15:01 UTC",
            customer_name="Jane Doe",
            matter_name="Jane Doe Intake",
            source="intake_dashboard",
            task_url="https://legalapp.example/tasks/123",
        )
    finally:
        monkeypatch.undo()

    assert ok is True
    assert len(sent) == 1
    html = sent[0]["html_body"]
    text = sent[0]["text_body"]
    assert "Created By" in html
    assert "Reception User" in html
    assert "Assigned To" in html
    assert "Partner User" in html
    assert "Customer" in html
    assert "Jane Doe" in html
    assert "Task link" in html
    assert "https://legalapp.example/tasks/123" in html
    assert "Reason / Description" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Created by: Reception User" in text
    assert "Customer: Jane Doe" in text
    assert "Task link: https://legalapp.example/tasks/123" in text
    assert "Reason / Description:" in text


def test_calendar_description_includes_creator_customer_and_task_link():
    task = Task(
        id=uuid.uuid4(),
        title="Wanda Archer - Call back caller",
        description="Task detail: answered",
    )

    description = _calendar_description(
        task,
        creator_name="Reception User",
        customer_name="Wanda Archer",
        task_url=f"https://legalapp.example/tasks/{task.id}",
    )

    assert "Task detail: answered" in description
    assert "Created by: Reception User" in description
    assert "Customer: Wanda Archer" in description
    assert f"Task link: https://legalapp.example/tasks/{task.id}" in description
