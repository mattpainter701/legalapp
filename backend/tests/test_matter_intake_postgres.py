"""PostgreSQL intake persistence and concurrent receipt coverage."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models.contact import Contact
from app.models.matter_document import MatterDocument
from app.models.matter_intake import MatterIntake
from app.models.plugin import Matter, MatterEvent
from app.models.task import Task
from app.routers import matter_intake as routes
from app.schemas.matter_intake import IntakeReceipt, IntakeStart
from app.services import matter_intake as service


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["onedrive", "google_drive"])
async def test_concurrent_receipt_creates_one_scheduling_task(
    db_session, test_user, test_engine, monkeypatch, provider
):
    user = SimpleNamespace(id=test_user.id, tenant_id=test_user.tenant_id, role="admin")
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=f"intake-{uuid.uuid4().hex[:8]}",
        matter_name="Smith",
        client_contact_id=contact.id,
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    monkeypatch.setattr(
        service,
        "store_file",
        AsyncMock(
            return_value=SimpleNamespace(
                succeeded=True,
                storage_path="provider/path",
                provider=provider,
                backend=provider,
                provider_item_id="item",
                drive_id="drive",
                parent_id="parent",
            )
        ),
    )
    monkeypatch.setattr(
        service, "get_user_capabilities", AsyncMock(return_value={"manage_matters"})
    )
    monkeypatch.setattr(
        service,
        "send_client_email",
        AsyncMock(
            return_value=SimpleNamespace(
                delivery_certainty="confirmed_sent", provider=provider
            )
        ),
    )
    fixed = datetime(2026, 9, 6, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "now", lambda: fixed)
    options = IntakeStart(
        email=contact.email,
        channels=["email"],
        questions=[dict(key="summary", label="Summary")],
        confirm_send=True,
    )
    packet = await service.start_packet(
        db_session, user, matter, options, "fee.pdf", b"%PDF-reviewed"
    )
    assert (
        await service.start_packet(
            db_session, user, matter, options, "fee.pdf", b"%PDF-reviewed"
        )
    ).id == packet.id
    packet_id, matter_id = packet.id, matter.id
    await service.deliver(db_session, packet, "welcome:email")
    docs = [
        MatterDocument(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            matter_id=matter_id,
            filename=name,
            storage_path="provider/path",
            storage_provider=provider,
            storage_backend=provider,
        )
        for name in ("signed.pdf", "questionnaire.txt")
    ]
    db_session.add_all(docs)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def receive(kind, doc_id):
        async with factory() as session:
            return await routes.receipt(
                matter_id,
                IntakeReceipt(
                    requirement=kind,
                    document_id=doc_id,
                    note="Reviewed complete document",
                ),
                session,
                user,
            )

    await asyncio.gather(
        receive("fee_agreement", docs[0].id), receive("questionnaire", docs[1].id)
    )
    await receive("fee_agreement", docs[0].id)
    await db_session.refresh(packet)
    assert packet.status == "documents_complete" and packet.completed_at == fixed
    assert packet.sent_at == fixed
    followup = await db_session.get(Task, uuid.uuid5(packet_id, "documents"))
    scheduled = await db_session.get(Task, uuid.uuid5(packet_id, "scheduling"))
    assert followup.status == "cancelled"
    assert scheduled.due_date == (fixed + timedelta(days=1)).date()
    assert scheduled.due_time.hour == 9
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterEvent)
            .where(
                MatterEvent.matter_id == matter_id,
                MatterEvent.title == "Intake documents complete",
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterIntake)
            .where(MatterIntake.matter_id == matter_id)
        )
        == 1
    )
    other = SimpleNamespace(id=uuid.uuid4(), tenant_id=user.tenant_id, role="user")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as denied:
        await routes.read(matter_id, db_session, other)
    assert denied.value.status_code == 404
