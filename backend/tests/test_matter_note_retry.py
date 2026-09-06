"""Real PostgreSQL transaction tests for retryable matter notes (no providers)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import set_tenant_context
from app.models.plugin import Matter, MatterEvent
from app.models.matter_note import MatterNote
from app.routers import matters
from app.schemas.matter import MatterNoteCreate


@pytest_asyncio.fixture
async def note_store(db_session, test_engine, test_tenant, test_user, monkeypatch):
    actor = SimpleNamespace(
        id=test_user.id, tenant_id=test_tenant.id, full_name=test_user.full_name
    )
    matter = Matter(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        user_id=actor.id,
        matter_name="Retry test",
        slug="retry-test",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = str(matter.id)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    cache = AsyncMock()
    monkeypatch.setattr(matters, "_invalidate_matter_context_cache", cache)

    async def current_user(request, db):
        return db.info.get("actor", actor)

    monkeypatch.setattr(matters, "get_current_user", current_user)

    async def save(body, *, other_actor=None, fail_commit=False):
        async with factory() as db:
            db.info["actor"] = other_actor or actor
            await set_tenant_context(db, str(db.info["actor"].tenant_id))
            if fail_commit:

                async def fail():
                    await db.flush()
                    raise RuntimeError("Synthetic connection loss before commit")

                db.commit = fail
            return await matters.add_note(matter_id, body, SimpleNamespace(), db)

    return SimpleNamespace(
        save=save, factory=factory, actor=actor, matter_id=matter.id, cache=cache
    )


@pytest.mark.asyncio
async def test_note_concurrent_retry_creates_one_note_and_event(note_store):
    body = MatterNoteCreate(
        request_id=uuid4(),
        title="Client call",
        content="Follow up",
        is_billable=True,
        hours="0.50",
    )
    first, second = await asyncio.gather(note_store.save(body), note_store.save(body))
    assert first.id == second.id
    assert first.id != str(body.request_id)
    assert first.content == "Follow up"
    async with note_store.factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterNote)) == 1
        assert await db.scalar(select(func.count()).select_from(MatterEvent)) == 1
    note_store.cache.assert_awaited_once()
    # Decimal-equivalent request serializations also replay.
    assert (
        await note_store.save(body.model_copy(update={"hours": body.hours.normalize()}))
    ).id == first.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"content": "Different"},
        {"is_billable": True},
        {"hours": "1.00"},
        {"title": "Other"},
        {"note_type": "client"},
    ],
)
async def test_note_retry_rejects_changed_payload(note_store, change):
    body = MatterNoteCreate(request_id=uuid4(), title="Call", content="Original")
    await note_store.save(body)
    changed = MatterNoteCreate(**{**body.model_dump(), **change})
    with pytest.raises(HTTPException) as error:
        await note_store.save(changed)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_note_retry_after_deletion_does_not_resurrect(note_store):
    body = MatterNoteCreate(request_id=uuid4(), title="Call", content="Original")
    saved = await note_store.save(body)
    async with note_store.factory() as db:
        await db.execute(delete(MatterNote).where(MatterNote.id == saved.id))
        await db.commit()
    with pytest.raises(HTTPException) as error:
        await note_store.save(body)
    assert error.value.status_code == 409
    async with note_store.factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterNote)) == 0
        assert await db.scalar(select(func.count()).select_from(MatterEvent)) == 1


@pytest.mark.asyncio
async def test_note_rollback_then_retry_is_atomic(note_store):
    body = MatterNoteCreate(request_id=uuid4(), title="Call", content="Original")
    with pytest.raises(RuntimeError):
        await note_store.save(body, fail_commit=True)
    async with note_store.factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterNote)) == 0
        assert await db.scalar(select(func.count()).select_from(MatterEvent)) == 0
    await note_store.save(body)
    note_store.cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_replay_rechecks_tenant_before_receipt(note_store):
    body = MatterNoteCreate(request_id=uuid4(), title="Call", content="Private")
    await note_store.save(body)
    outsider = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), full_name="Other firm")
    with pytest.raises(HTTPException) as error:
        await note_store.save(body, other_actor=outsider)
    assert error.value.status_code == 404
    assert "Private" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_legacy_note_without_request_id_still_creates(note_store):
    body = MatterNoteCreate(title="Legacy caller", content="No retry key")
    first = await note_store.save(body)
    second = await note_store.save(body)
    assert first.id != second.id
