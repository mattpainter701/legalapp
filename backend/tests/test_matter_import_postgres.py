"""Exercise persisted import transactions against the CI PostgreSQL fixture."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models.communication_log import CommunicationLog
from app.models.external_import import ExternalRecordLink
from app.models.matter_document import MatterDocument
from app.routers import matter_imports as routes
from app.services.matter_import_manifest import file_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["onedrive", "google_drive"])
async def test_persisted_import_retry_and_correspondence(
    db_session, test_user, monkeypatch, provider
):
    user = SimpleNamespace(id=test_user.id, tenant_id=test_user.tenant_id, role="admin")
    content = b"From: lawyer@former.example\r\nTo: client@example.com\r\nSubject: Historic case\r\n\r\nPreserved body"
    plan = routes.ImportPlan(
        id=uuid.uuid4(),
        files=[dict(file_manifest("Smith/mail.eml", content), group="Smith")],
    )
    await routes.plan(plan, db_session, user)
    approval = routes.Approval(
        confirm=True,
        mappings=[
            dict(
                group="Smith",
                first_name="Jane",
                last_name="Smith",
                matter_name="Smith case",
                intake="existing",
            )
        ],
    )
    approved = await routes.approve(plan.id, approval, db_session, user)
    assert await routes.approve(plan.id, approval, db_session, user) == approved
    monkeypatch.setattr(
        routes.MatterFileStore,
        "store_matter_file_result",
        AsyncMock(
            return_value=SimpleNamespace(
                succeeded=True,
                storage_path="provider/path",
                provider=provider,
                backend=provider,
                provider_item_id="object",
                drive_id="drive",
                parent_id="parent",
            )
        ),
    )
    first = await routes.ingest(db_session, user, plan.id, "Smith/mail.eml", content)
    assert (
        await routes.ingest(db_session, user, plan.id, "Smith/mail.eml", content)
        == first
    )
    assert (await routes.status(plan.id, db_session, user))["status"] == "complete"
    log = await db_session.scalar(
        select(CommunicationLog).where(
            CommunicationLog.document_id == uuid.UUID(first["document_id"])
        )
    )
    assert log.subject == "Historic case" and log.status == "logged"
    assert log.participants["from"] == "lawyer@former.example"
    doc = await db_session.get(MatterDocument, uuid.UUID(first["document_id"]))
    assert doc.storage_provider == provider and not doc.portal_visible
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ExternalRecordLink)
            .where(ExternalRecordLink.import_run_id == plan.id)
        )
        == 1
    )
    second = routes.ImportPlan(id=uuid.uuid4(), files=plan.files)
    await routes.plan(second, db_session, user)
    await routes.approve(
        second.id,
        routes.Approval(
            confirm=True, mappings=[dict(group="Smith", matter_id=first["matter_id"])]
        ),
        db_session,
        user,
    )
    assert (
        await routes.ingest(db_session, user, second.id, "Smith/mail.eml", content)
    )["status"] == "duplicate"


@pytest.mark.asyncio
async def test_another_user_cannot_read_or_upload_batch(db_session, test_user):
    user = SimpleNamespace(id=test_user.id, tenant_id=test_user.tenant_id, role="admin")
    plan = routes.ImportPlan(
        id=uuid.uuid4(), files=[dict(file_manifest("case.txt", b"case"), group="Case")]
    )
    await routes.plan(plan, db_session, user)
    other = SimpleNamespace(id=uuid.uuid4(), tenant_id=user.tenant_id, role="user")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await routes.get_run(db_session, other, plan.id)
    assert exc.value.status_code == 404
