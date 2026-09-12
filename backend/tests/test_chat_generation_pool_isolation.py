"""Chat turns must not drain the pool that ordinary requests share.

Conversation generation takes a session-level advisory lock, which cannot move
between connections, so one chat turn pins one connection for the whole turn —
seconds to minutes while the model works, not the milliseconds an ordinary
query holds one.

Before the split those connections came out of the request pool. A worker with
enough concurrent turns therefore had no slot left for anything else, and
matter, task and document reads — nothing wrong with them — queued for
DATABASE_POOL_TIMEOUT_SECONDS and then failed. Generation now draws from its
own bounded pool, so the two workloads saturate independently.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import database as app_database
from app.config import get_settings
from app.routers import chat as chat_router
from fastapi import HTTPException


@pytest.fixture
def settings():
    return get_settings()


def test_generation_pool_is_separate_from_the_request_pool(settings):
    """The two engines must not be the same object or share a pool."""
    assert app_database.generation_engine is not app_database.engine
    assert app_database.generation_engine.pool is not app_database.engine.pool


def test_generation_pool_is_bounded_and_configurable(settings):
    """Its size is a deliberate capacity decision, not an accident."""
    pool = app_database.generation_engine.pool
    assert pool.size() == settings.DATABASE_GENERATION_POOL_SIZE
    assert pool._max_overflow == settings.DATABASE_GENERATION_MAX_OVERFLOW
    # A caller queueing for a generation slot is queueing behind a whole LLM
    # turn, so it must give up sooner than an ordinary request does.
    assert (
        settings.DATABASE_GENERATION_POOL_TIMEOUT_SECONDS
        < settings.DATABASE_POOL_TIMEOUT_SECONDS
    )


def test_generation_engine_selection_follows_the_session(settings):
    """Only the application engine is redirected to the generation pool.

    Tests and any other caller that binds a session to its own engine must keep
    the lease on that engine — otherwise the advisory lock would be taken on a
    connection to a different database than the one being used.
    """
    assert (
        app_database.get_generation_engine(app_database.engine)
        is app_database.generation_engine
    )
    other = object()
    assert app_database.get_generation_engine(other) is other


@pytest.mark.asyncio
async def test_saturated_generation_pool_does_not_starve_ordinary_requests(
    client,
    db_session,
    test_engine,
    monkeypatch,
):
    """Fill every generation slot, then prove normal reads still work.

    The generation pool is stood up here against the test database with a
    deliberately tiny ceiling, and `get_generation_engine` is pointed at it so
    the lease behaves exactly as it does in production.
    """
    pool_size, overflow = 1, 0
    tiny_engine = create_async_engine(
        str(test_engine.url),
        pool_size=pool_size,
        max_overflow=overflow,
        pool_timeout=0.5,
    )
    monkeypatch.setattr(
        chat_router, "get_generation_engine", lambda _request_engine: tiny_engine
    )

    request_pool = test_engine.sync_engine.pool
    baseline = request_pool.checkedout()

    first = (await client.post("/api/conversations", json={})).json()
    second = (await client.post("/api/conversations", json={})).json()

    held = await chat_router._try_conversation_generation_lease(
        db_session, uuid.UUID(first["id"])
    )
    assert held is not None
    try:
        # The only generation slot is taken, and it is NOT a request slot.
        # Taking a lease can only *lower* request checkouts, never raise them:
        # the lease first rolls the request session back, which hands that
        # connection to the pool before pinning a generation one.
        assert tiny_engine.pool.checkedout() == pool_size
        assert request_pool.checkedout() <= baseline

        # A second turn cannot get a slot. It must say so as a capacity
        # problem (503), not as "this conversation is busy" (409) — a
        # different conversation is not busy, the server is.
        with pytest.raises(HTTPException) as exhausted:
            await chat_router._try_conversation_generation_lease(
                db_session, uuid.UUID(second["id"])
            )
        assert exhausted.value.status_code == 503
        assert exhausted.value.headers["Retry-After"] == "5"

        # The point of the split: ordinary reads are unaffected while
        # generation is completely saturated.
        listed = await client.get("/api/conversations")
        assert listed.status_code == 200
        assert {row["id"] for row in listed.json()} >= {first["id"], second["id"]}
    finally:
        await held.release()
        await tiny_engine.dispose()


@pytest.mark.asyncio
async def test_exhausted_generation_pool_leaks_no_connection(
    client,
    db_session,
    test_engine,
    monkeypatch,
):
    """A refused lease must not strand the slot it failed to acquire."""
    tiny_engine = create_async_engine(
        str(test_engine.url), pool_size=1, max_overflow=0, pool_timeout=0.5
    )
    monkeypatch.setattr(
        chat_router, "get_generation_engine", lambda _request_engine: tiny_engine
    )
    conv = (await client.post("/api/conversations", json={})).json()
    other = (await client.post("/api/conversations", json={})).json()

    held = await chat_router._try_conversation_generation_lease(
        db_session, uuid.UUID(conv["id"])
    )
    assert held is not None
    try:
        for _ in range(3):
            with pytest.raises(HTTPException):
                await chat_router._try_conversation_generation_lease(
                    db_session, uuid.UUID(other["id"])
                )
        assert tiny_engine.pool.checkedout() == 1
    finally:
        await held.release()

    # Releasing the held lease returns the slot, so the next turn succeeds.
    assert tiny_engine.pool.checkedout() == 0
    regained = await chat_router._try_conversation_generation_lease(
        db_session, uuid.UUID(other["id"])
    )
    assert regained is not None
    await regained.release()
    await tiny_engine.dispose()


@pytest.mark.asyncio
async def test_streaming_turn_consumes_a_generation_slot_not_a_request_slot(
    client,
    db_session,
    test_engine,
    monkeypatch,
):
    """The lease a real turn takes comes from the generation pool."""
    tiny_engine = create_async_engine(
        str(test_engine.url), pool_size=2, max_overflow=0, pool_timeout=0.5
    )
    monkeypatch.setattr(
        chat_router, "get_generation_engine", lambda _request_engine: tiny_engine
    )
    request_pool = test_engine.sync_engine.pool
    conv = (await client.post("/api/conversations", json={})).json()
    baseline = request_pool.checkedout()

    lease = await chat_router._try_conversation_generation_lease(
        db_session, uuid.UUID(conv["id"])
    )
    assert lease is not None
    try:
        assert tiny_engine.pool.checkedout() == 1
        assert request_pool.checkedout() <= baseline
        # The lease session really is usable for the turn's writes.
        assert isinstance(lease.session, AsyncSession)
        assert await lease.session.scalar(chat_router.text("SELECT 1")) == 1
    finally:
        await lease.release()
    assert tiny_engine.pool.checkedout() == 0
    await tiny_engine.dispose()


@pytest.mark.asyncio
async def test_lease_conflict_still_returns_busy_not_capacity(
    client,
    db_session,
    test_engine,
    monkeypatch,
):
    """Two turns on the SAME conversation is a 409, not a 503.

    The capacity path must not swallow the lock-conflict signal: they are
    different problems and the caller should be told which one it hit.
    """
    roomy_engine = create_async_engine(
        str(test_engine.url), pool_size=5, max_overflow=0, pool_timeout=0.5
    )
    monkeypatch.setattr(
        chat_router, "get_generation_engine", lambda _request_engine: roomy_engine
    )
    conv = (await client.post("/api/conversations", json={})).json()
    held = await chat_router._try_conversation_generation_lease(
        db_session, uuid.UUID(conv["id"])
    )
    assert held is not None
    try:
        # Same conversation, slots available -> the advisory lock refuses, and
        # the function signals that by returning None (callers map it to 409).
        conflict = await chat_router._try_conversation_generation_lease(
            db_session, uuid.UUID(conv["id"])
        )
        assert conflict is None
    finally:
        await held.release()
        await roomy_engine.dispose()
