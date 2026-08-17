"""Retention boundaries for inbox triage.

Mailbox scanning can inspect messages for the current user, but only messages
that match a contact on an active matter become durable firm communications.
"""

import uuid

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
