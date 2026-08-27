"""Retention boundaries for inbox triage.

Mailbox scanning can inspect messages for the current user, but only messages
that match a contact on an active matter become durable firm communications.
"""

import uuid
from unittest.mock import AsyncMock

from app.models.task import Task
from app.services import email_agent


async def test_unmatched_email_is_not_archived_or_turned_into_a_task(monkeypatch):
    class NoPersistenceDB:
        def __init__(self):
            self.persistence_attempted = False

        async def execute(self, *_args, **_kwargs):
            self.persistence_attempted = True
            raise AssertionError("Unmatched email must not query durable records")

        async def commit(self):
            self.persistence_attempted = True
            raise AssertionError("Unmatched email must not commit durable records")

    async def no_matter_match(*_args, **_kwargs):
        return []

    async def no_op_tenant_context(*_args, **_kwargs):
        return None

    import app.database

    monkeypatch.setattr(email_agent, "_match_email_to_matters", no_matter_match)
    monkeypatch.setattr(app.database, "set_tenant_context", no_op_tenant_context)
    db = NoPersistenceDB()

    await email_agent._auto_log_and_task(
        db=db,
        tenant_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email={
            "id": "fortigate-webfilter-001",
            "provider": "microsoft",
            "from": "alerts@firewall.example",
            "to": "attorney@firm.example",
            "subject": "%%log.logdesc%%",
            "body_preview": "Automated firewall web filter block notification.",
        },
        classification={
            "summary": "Automated system log.",
            "deadline_mentioned": "2026-07-28",
            "urgency": "high",
            "action_needed": "Review the alert.",
        },
    )

    assert db.persistence_attempted is False


async def test_copied_contact_does_not_make_an_unknown_sender_matter_mail(monkeypatch):
    """A known contact in cc must not pull an unknown sender's mail into a matter.

    Inbound sync sees everything in the mailbox.  Matching recipients as well as
    the sender meant that any message copying a client -- or simply addressed to
    the firm -- became durable matter correspondence no matter who sent it.
    """
    import uuid as uuid_mod

    from app.services import email_agent as agent

    tenant_id = uuid_mod.uuid4()
    seen_addresses = {}

    class Contacts:
        def all(self):
            return []

    class DB:
        async def execute(self, statement):
            # Capture the address set the query was built from.
            text = str(statement.compile(compile_kwargs={"literal_binds": True}))
            seen_addresses["sql"] = text
            return Contacts()

    email = {
        "from": "stranger@unknown.example",
        "to": "firm@example.com",
        "cc": "client@known.example",
        "bcc": "paralegal@known.example",
    }

    result = await agent._match_email_to_matters(DB(), tenant_id, email)

    assert result == []
    sql = seen_addresses["sql"].lower()
    assert "stranger@unknown.example" in sql
    for copied in ("client@known.example", "paralegal@known.example", "firm@example.com"):
        assert copied not in sql, f"{copied} must not be matched: it is a recipient, not the sender"


class _NoExistingResult:
    def scalar_one_or_none(self):
        return None


class _CaptureDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        return _NoExistingResult()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        value = self.added[-1]
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1


async def test_model_deadline_without_subject_tag_stays_correspondence_only(monkeypatch):
    import app.database
    from app.services import task_notifications

    monkeypatch.setattr(app.database, "set_tenant_context", AsyncMock())
    notify = AsyncMock()
    monkeypatch.setattr(task_notifications, "notify_task_created", notify)
    db = _CaptureDB()

    await email_agent._auto_log_and_task(
        db=db,
        tenant_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        email={
            "id": "message-untagged",
            "provider": "microsoft",
            "from": "client@example.com",
            "to": "attorney@example.com",
            "subject": "Can we meet in two weeks?",
            "body_preview": "Please put this on the calendar.",
            "received": "2026-08-26T10:00:00-05:00",
        },
        classification={
            "summary": "Client asks for a meeting.",
            "deadline_mentioned": "2026-09-09",
            "urgency": "high",
        },
        matched_matter_ids=[uuid.uuid4()],
    )

    assert not any(isinstance(value, Task) for value in db.added)
    assert db.commits == 1
    notify.assert_not_awaited()


async def test_subject_tag_creates_task_and_mirrors_notification(monkeypatch):
    import app.database
    from app.services import task_notifications

    monkeypatch.setattr(app.database, "set_tenant_context", AsyncMock())
    notify = AsyncMock()
    monkeypatch.setattr(task_notifications, "notify_task_created", notify)
    db = _CaptureDB()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    matter_id = uuid.uuid4()

    await email_agent._auto_log_and_task(
        db=db,
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        email={
            "id": "message-tagged",
            "provider": "microsoft",
            "from": "client@example.com",
            "to": "attorney@example.com",
            "subject": "[TASK] Nigel I need to meet with you in two weeks",
            "body_preview": "Please put this on the calendar.",
            "received": "2026-08-26T10:00:00-05:00",
        },
        classification={"summary": "Client asks for a meeting."},
        matched_matter_ids=[matter_id],
    )

    task = next(value for value in db.added if isinstance(value, Task))
    assert task.title == "Nigel I need to meet with you"
    assert task.due_date.isoformat() == "2026-09-09"
    assert task.matter_id == matter_id
    assert task.external_ref == "microsoft:message-tagged"
    notify.assert_awaited_once_with(db, task, str(tenant_id))
