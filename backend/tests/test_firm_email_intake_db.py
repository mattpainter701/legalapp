"""Exercise real PostgreSQL persistence through firm intake review."""
import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.inbound_email import InboundEmail, FirmInboundEmailAlias
from app.models.plugin import Matter
from app.models.task import Task
from app.models.user import User
from app.routers import firm_email_intake as routes
from app.services import inbound_email as storage
from app.services.matter_file_store import StorageResult


@pytest.mark.asyncio
async def test_real_review_creates_assigned_todo_once(db_session, test_user, test_tenant, tmp_path, monkeypatch):
    db = db_session
    tid, uid = test_tenant.id, test_user.id
    colleague = User(tenant_id=tid, email="jane@example.com", full_name="Jane Smith", role="user")
    matter = Matter(tenant_id=tid, user_id=uid, slug="smith", matter_name="Smith matter")
    db.add_all([colleague, matter]); await db.flush()
    cid, mid = colleague.id, matter.id
    alias = FirmInboundEmailAlias(tenant_id=tid, 
        token_hash="a" * 64, encrypted_local_part="encrypted", status="active")
    db.add(alias); await db.flush()
    raw = b"From: staff@example.com\r\nSubject: [TASK] Jane, review this tomorrow\r\n\r\nClient request"
    monkeypatch.setattr(storage.settings, "UPLOAD_DIR", str(tmp_path))
    iid = uuid.uuid4()
    path = storage.quarantine_path(tid, iid)
    storage.write_quarantined_message(path, raw)
    item = InboundEmail(id=iid, tenant_id=tid, firm_alias_id=alias.id, matter_id=None, status="pending",
        envelope_sender=test_user.email, recipient="firm@example.com", subject="[TASK] Jane, review this tomorrow",
        message_sha256=hashlib.sha256(raw).hexdigest(), raw_size=len(raw), raw_storage_path=str(path),
        occurred_at=datetime.now(timezone.utc))
    db.add(item); await db.commit()
    monkeypatch.setattr(routes, "get_current_user", AsyncMock(return_value=test_user))
    monkeypatch.setattr(storage, "autofile_folder_id", AsyncMock(return_value=None))
    monkeypatch.setattr(storage.matter_file_store, "store_matter_file_result", AsyncMock(return_value=
        StorageResult(provider="local", backend="local", storage_path=str(tmp_path / "filed.eml"))))
    body = routes.ReviewTodo(matter_id=mid, assigned_to_user_id=cid, title="Review this")
    result = await routes.accept(iid, body, None, db)
    task = await db.get(Task, result["task_id"])
    assert task.matter_id == mid and task.assigned_to_user_id == cid
    assert task.created_by_user_id == uid and task.status == "pending"
    assert task.task_type == "general" and task.due_date is None
    assert task.external_ref == f"inbound-email:{iid}"
    assert not path.exists()
    assert (await db.get(InboundEmail, iid)).status == "accepted"
    with pytest.raises(HTTPException) as error:
        await routes.accept(iid, body, None, db)
    assert error.value.status_code == 409
    assert len((await db.execute(select(Task).where(Task.tenant_id == tid))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_other_tenant_cannot_review_or_list_item(db_session, test_user):
    other_tid = uuid.uuid4()
    alias = FirmInboundEmailAlias(tenant_id=other_tid, 
        token_hash="b" * 64, encrypted_local_part="encrypted", status="active")
    db_session.add(alias); await db_session.flush()
    item = InboundEmail(tenant_id=other_tid, firm_alias_id=alias.id, status="pending",
        envelope_sender="staff@other.com", recipient="firm@example.com", subject="[TASK] private",
        message_sha256="c" * 64, raw_size=1, occurred_at=datetime.now(timezone.utc))
    db_session.add(item); await db_session.flush()
    with pytest.raises(HTTPException) as error:
        await routes.pending_item(db_session, test_user.tenant_id, item.id)
    assert error.value.status_code == 404
    assert await routes.active_alias(db_session, test_user.tenant_id) is None
