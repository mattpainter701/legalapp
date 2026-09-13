import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import dkim
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.routers import firm_email_intake as routes
from app.services import firm_email_intake as service
from tests.test_inbound_email_routes import FakeDB, FakeResult
from tests.test_inbound_email_routes import configure_ingress, ingress_request, alias_row
from app.routers import matters_correspondence as ingress


def staff(name="Jane Smith", email="jane@example.com", **kwargs):
    return NS(id=uuid.uuid4(), tenant_id=uuid.uuid4(), full_name=name, email=email,
        role="user", principal_type="human", is_active=True, **kwargs)


@pytest.fixture
def signed_mail(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    import base64
    dns = b"v=DKIM1; k=rsa; p=" + base64.b64encode(public)
    verify = dkim.DKIM.verify
    monkeypatch.setattr(dkim.DKIM, "verify", lambda self, idx=0: verify(self, idx=idx, dnsfunc=lambda *a, **k: dns))
    def make(subject="[TASK] Jane, review this tomorrow", domain=b"example.com", signed=(b"from", b"subject")):
        raw = ("From: jane@example.com\r\nTo: firm@example.net\r\nSubject: " + subject +
               "\r\n\r\nFrom: Client <client@example.net>\r\nPlease review this.\r\n").encode()
        return dkim.sign(raw, b"test", domain, private, include_headers=list(signed)) + raw
    return make


def test_signature_verifies_content_and_identity(signed_mail):
    raw = signed_mail()
    assert service.verify_staff_signature(raw, "jane@example.com")
    assert not service.verify_staff_signature(raw, "other@example.com")
    assert not service.verify_staff_signature(raw.replace(b"Please review", b"Please delete"), "jane@example.com")
    assert not service.verify_staff_signature(raw.replace(b"review this tomorrow", b"send money tomorrow"), "jane@example.com")
    assert not service.verify_staff_signature(signed_mail(domain=b"attacker.example"), "jane@example.com")
    assert not service.verify_staff_signature(signed_mail(signed=(b"from",)), "jane@example.com")
    assert not service.verify_staff_signature(b"Authentication-Results: mx.cloudflare.net; dkim=pass\r\nFrom: jane@example.com\r\nSubject: [TASK] test\r\n\r\n", "jane@example.com")
    assert not service.verify_staff_signature(b"From: jane@example.com\r\nFrom: evil@example.com\r\n\r\n", "jane@example.com")
    assert not service.verify_staff_signature(raw.replace(b"From: jane", b"Subject: extra\r\nFrom: jane"), "jane@example.com")


def test_task_suggestions_are_plain_todos_and_timezone_aware():
    jane = staff()
    now = datetime(2026, 9, 13, 1, tzinfo=timezone.utc)
    task = service.todo_suggestion("[TASK] Jane, review this tomorrow", now, "America/Chicago", [jane], jane.id)
    assert task == {"title": "review this", "due_date": "2026-09-13", "assigned_to_user_id": str(jane.id), "assignee_hint": "Jane"}
    assert service.todo_suggestion("[task] Review documents", now, "UTC", [jane], jane.id)["due_date"] is None
    assert len(service.todo_suggestion("[TASK] " + "x" * 500, now, "UTC", [jane], jane.id)["title"]) == 300
    assert service.todo_suggestion("[TASK] Jane, review this", now, "UTC", [jane, staff()], jane.id)["assigned_to_user_id"] is None
    assert service.todo_suggestion("[TASK] Nobody, review this", now, "UTC", [jane], jane.id)["assigned_to_user_id"] is None
    assert service.todo_suggestion("[TASK] Jane Smith, review in two weeks", now, "UTC", [jane], jane.id)["due_date"] == "2026-09-27"
    for subject in ["Fwd: [TASK] review this", "[DEADLINE] File tomorrow", "Review this", "[TASK] Jane, "]:
        assert service.todo_suggestion(subject, now, "UTC", [jane], jane.id) is None


def test_forwarded_sender_hints():
    raw = b"From: staff@example.com\r\nContent-Type: text/plain\r\n\r\nFrom: Client <client@example.net>\r\nhello"
    assert service.forwarded_senders(raw) == ["client@example.net"]
    original = EmailMessage(); original["From"] = "client@example.net"; original.set_content("test")
    wrapper = EmailMessage(); wrapper.set_content("review"); wrapper.add_attachment(original)
    assert service.forwarded_senders(wrapper.as_bytes()) == ["client@example.net"]
    html = EmailMessage()
    html.set_content('<div><b>From:</b> Client &lt;client@example.net&gt;<br><b>Sent:</b> Monday</div>', subtype="html")
    assert service.forwarded_senders(html.as_bytes()) == ["client@example.net"]


@pytest.mark.asyncio
async def test_matching_keeps_ambiguity_and_filters_by_tenant(monkeypatch):
    a, b = NS(id=uuid.uuid4(), matter_name="A", case_number="26-CV-10"), NS(id=uuid.uuid4(), matter_name="B", case_number="26-CV-20")
    monkeypatch.setattr(service, "forwarded_senders", lambda _: ["client@example.com"])
    monkeypatch.setattr(service, "_match_email_to_matters", AsyncMock(return_value=[a.id, b.id]))
    db = FakeDB(FakeResult(rows=[a, b]))
    tenant_id = uuid.uuid4()
    assert await service.suggest_matters(db, tenant_id, b"", "26-CV-10") == [{"id": str(a.id), "title": "A"}]
    sql = str(db.executed[0][0].compile(dialect=postgresql.dialect()))
    assert "tenant_id" in sql and "is_closed IS false" in sql
    assert len(await service.suggest_matters(FakeDB(FakeResult(rows=[a,b])), tenant_id, b"", "no case")) == 2


@pytest.mark.asyncio
async def test_staff_authentication(monkeypatch):
    jane = staff()
    monkeypatch.setattr(service, "verify_staff_signature", lambda *a: True)
    db = FakeDB(FakeResult(rows=[jane]))
    assert await service.authenticated_submitter(db, jane.tenant_id, b"", jane.email) is jane
    assert "tenant_id" in str(db.executed[0][0])
    assert await service.authenticated_submitter(FakeDB(FakeResult(rows=[])), jane.tenant_id, b"", jane.email) is None
    monkeypatch.setattr(service, "verify_staff_signature", lambda *a: False)
    assert await service.authenticated_submitter(FakeDB(FakeResult(rows=[jane])), jane.tenant_id, b"", jane.email) is None


@pytest.fixture
def context(monkeypatch):
    user = staff()
    monkeypatch.setattr(routes, "get_current_user", AsyncMock(return_value=user))
    monkeypatch.setattr(routes, "require_admin", AsyncMock(return_value=user))
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    return user


@pytest.mark.asyncio
async def test_firm_settings_enable_rotate_disable_and_timezone(context, monkeypatch):
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_ENABLED", True)
    monkeypatch.setattr(routes, "get_intake", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(routes, "encrypt_token", lambda value: value)
    db = FakeDB(FakeResult(), FakeResult(), FakeResult())
    await routes.configure_intake(routes.IntakeSettings(action="enable", timezone="America/Chicago"), None, db)
    alias = db.added[0]
    assert alias.tenant_id == context.tenant_id
    assert alias.encrypted_local_part.startswith("f-")
    assert db.added[1].custom_config["firm_email_timezone"] == "America/Chicago"
    for action in ["rotate", "disable", "settings", "enable"]:
        alias.status = "active"
        config = NS(custom_config={"other": "preserved"})
        db = FakeDB(FakeResult(), FakeResult(alias), FakeResult(config))
        await routes.configure_intake(routes.IntakeSettings(action=action), None, db)
        assert config.custom_config["other"] == "preserved"
        assert len(db.added) == (1 if action == "rotate" else 0)
        if action in {"rotate", "disable"}: assert alias.status == "revoked"
    with pytest.raises(HTTPException):
        await routes.configure_intake(routes.IntakeSettings(action="enable", timezone="bogus"), None, FakeDB())
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_ENABLED", False)
    with pytest.raises(HTTPException):
        await routes.configure_intake(routes.IntakeSettings(action="enable"), None, FakeDB())


@pytest.mark.asyncio
async def test_staff_only_and_admin_enforcement(context, monkeypatch):
    context.role = "client"
    with pytest.raises(HTTPException): await routes.staff_context(None, FakeDB())
    monkeypatch.setattr(routes, "require_admin", AsyncMock(side_effect=HTTPException(403)))
    with pytest.raises(HTTPException): await routes.configure_intake(routes.IntakeSettings(action="disable"), None, FakeDB())


@pytest.mark.asyncio
async def test_review_revalidates_matter_assignee_and_duplicate(context, monkeypatch):
    matter = NS(id=uuid.uuid4(), is_closed=False)
    row = NS(id=uuid.uuid4(), status="pending", matter_id=None)
    monkeypatch.setattr(routes, "_get_matter_or_404", AsyncMock(return_value=matter))
    monkeypatch.setattr(routes, "active_staff", AsyncMock(return_value=[context]))
    filing = AsyncMock(return_value=NS(task=NS(id=uuid.uuid4())))
    monkeypatch.setattr(routes, "file_inbound_email", filing)
    body = routes.ReviewTodo(matter_id=matter.id, assigned_to_user_id=context.id, title="Review this")
    db = FakeDB(FakeResult(row))
    result = await routes.accept(row.id, body, None, db)
    assert result["matter_id"] == matter.id and row.matter_id == matter.id
    assert filing.call_args.kwargs["task_suggestion"].task_type == "general"
    assert filing.call_args.kwargs["assigned_to_user_id"] == context.id
    sql = str(db.executed[0][0].compile(dialect=postgresql.dialect()))
    assert "tenant_id" in sql and "FOR UPDATE OF inbound_emails" in sql
    for value, code in [(None,404),(NS(status="accepted"),409)]:
        with pytest.raises(HTTPException) as error:
            await routes.accept(row.id, body, None, FakeDB(FakeResult(value)))
        assert error.value.status_code == code
    body.assigned_to_user_id = uuid.uuid4()
    with pytest.raises(HTTPException): await routes.accept(row.id, body, None, FakeDB(FakeResult(row)))
    body.assigned_to_user_id = context.id; body.title = " "
    with pytest.raises(HTTPException): await routes.accept(row.id, body, None, FakeDB(FakeResult(row)))
    matter.is_closed = True
    with pytest.raises(HTTPException): await routes.accept(row.id, body, None, FakeDB(FakeResult(row)))


@pytest.mark.asyncio
async def test_queue_and_rejection(context, monkeypatch):
    row = NS(id=uuid.uuid4(), subject="Review", envelope_sender=context.email, created_at=datetime.now(timezone.utc),
        body_preview="Email", authentication_results={"firm_intake": {"task": None}}, status="pending")
    result = await routes.queue(None, 0, FakeDB(FakeResult(rows=[row]), FakeResult(rows=[])))
    assert result["items"][0]["id"] == row.id
    monkeypatch.setattr(routes, "remove_quarantined_message", lambda row: None)
    db = FakeDB(FakeResult(row))
    assert await routes.reject(row.id, None, db) == {"status": "rejected"}
    assert row.reviewed_by_user_id == context.id and db.commits == 1


@pytest.mark.asyncio
async def test_settings_read(context, monkeypatch):
    monkeypatch.setattr(routes, "_alias_response", lambda _: NS(model_dump=lambda: {"alias": None}))
    db = FakeDB(FakeResult(), FakeResult(NS(custom_config={"firm_email_timezone": "America/Chicago"})),
        FakeResult(rows=[context]), FakeResult(4))
    result = await routes.get_intake(None, db)
    assert result["pending_count"] == 4 and result["staff"][0]["email"] == context.email
    assert result["timezone"] == "America/Chicago"
    assert await routes.intake_timezone(FakeDB(FakeResult()), context.tenant_id) == "UTC"


@pytest.mark.asyncio
async def test_firm_ingress_verification_queue_and_duplicate(context, monkeypatch, tmp_path):
    configure_ingress(monkeypatch, tmp_path)
    alias = alias_row(context, NS(id=None)); alias.kind = "firm"
    request = ingress_request()
    request.headers["x-lawhand-envelope-to"] = "f-abcdefghijklmnopqrstuvwxyz@intake.getlawhand.com"
    monkeypatch.setattr(service, "authenticated_submitter", AsyncMock(return_value=context))
    monkeypatch.setattr(service, "active_staff", AsyncMock(return_value=[context]))
    monkeypatch.setattr(service, "suggest_matters", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "intake_timezone", AsyncMock(return_value="UTC"))
    db = FakeDB(FakeResult(alias), FakeResult())
    await ingress.receive_cloudflare_inbound_email(request, db)
    assert len(db.added) == 1 and db.added[0].matter_id is None
    assert db.added[0].authentication_results["firm_intake"]["submitter_id"] == str(context.id)
    assert db.added[0].status == "pending"
    duplicate_db = FakeDB(FakeResult(alias), FakeResult(uuid.uuid4()))
    assert await ingress.receive_cloudflare_inbound_email(request, duplicate_db) == {"accepted": True}
    assert not duplicate_db.added
    monkeypatch.setattr(service, "authenticated_submitter", AsyncMock(return_value=None))
    denied_db = FakeDB(FakeResult(alias), FakeResult())
    with pytest.raises(HTTPException) as error:
        await ingress.receive_cloudflare_inbound_email(request, denied_db)
    assert error.value.status_code == 403 and not denied_db.added
