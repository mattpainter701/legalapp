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
