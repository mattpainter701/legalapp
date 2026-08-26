"""Focused branch coverage for the SMB router and scheduler additions.

These tests deliberately keep the database and Redis at the boundary.  The
production paths are exercised with small async fakes so failure handling and
tenant/update routing remain covered without requiring a live service stack.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis_module
from fastapi import HTTPException
from redis.exceptions import RedisError

from app.routers import smb as smb_router
from app.services import scheduler as scheduler_module


def _request(redis=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
        headers={},
        state=SimpleNamespace(),
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self, *results):
        self.results = iter(results)
        self.commit = AsyncMock()
        self.execute = AsyncMock(
            side_effect=lambda *_args, **_kwargs: next(self.results)
        )


@pytest.mark.asyncio
async def test_registration_receipt_handles_bytes_mismatch_and_redis_failure(
    monkeypatch,
):
    body = SimpleNamespace(pairing_code="pairing")
    redis = SimpleNamespace(get=AsyncMock(return_value=b"ciphertext"))
    monkeypatch.setattr(
        smb_router, "_registration_fingerprint", lambda _body: "expected"
    )
    monkeypatch.setattr(
        smb_router, "decrypt_token", lambda _: '{"fingerprint":"wrong"}'
    )
    assert await smb_router._load_registration_receipt(redis, body) is None

    redis.get.side_effect = RedisError("down")
    assert await smb_router._load_registration_receipt(redis, body) is None

    redis.set = AsyncMock(side_effect=RedisError("down"))
    monkeypatch.setattr(smb_router, "encrypt_token", lambda value: value)
    await smb_router._store_registration_receipt(
        redis, body, SimpleNamespace(model_dump=lambda **_: {})
    )
    assert redis.set.await_count == 1


@pytest.mark.asyncio
async def test_registration_and_wait_helpers_cover_none_and_retry(monkeypatch):
    body = SimpleNamespace(pairing_code="pairing")
    assert await smb_router._load_registration_receipt(None, body) is None
    assert await smb_router._wait_for_registration_receipt(None, body) is None
    assert await smb_router._store_registration_receipt(None, body, object()) is None

    response = SimpleNamespace()
    load = AsyncMock(side_effect=[None, response])
    monkeypatch.setattr(smb_router, "_load_registration_receipt", load)
    monkeypatch.setattr(smb_router.asyncio, "sleep", AsyncMock())
    assert await smb_router._wait_for_registration_receipt(object(), body) is response


@pytest.mark.asyncio
async def test_register_endpoint_returns_receipt_after_concurrent_retry(monkeypatch):
    body = SimpleNamespace(pairing_code="pairing")
    response = SimpleNamespace()
    monkeypatch.setattr(
        smb_router, "_load_registration_receipt", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        smb_router, "_wait_for_registration_receipt", AsyncMock(return_value=response)
    )
    monkeypatch.setattr(
        smb_router.smb_service,
        "register_agent",
        AsyncMock(side_effect=ValueError("already registered")),
    )
    assert await smb_router.register_agent(body, _request(), _Db()) is response


@pytest.mark.asyncio
async def test_agent_task_result_validation_and_redis_failure(monkeypatch):
    agent = SimpleNamespace(id="agent-1", tenant_id="tenant-1")
    with pytest.raises(HTTPException) as mismatch:
        await smb_router.submit_task_result(
            "other", "task", SimpleNamespace(task_id="task"), _request(), _Db(), agent
        )
    assert mismatch.value.status_code == 403

    with pytest.raises(HTTPException) as task_mismatch:
        await smb_router.submit_task_result(
            "agent-1",
            "task",
            SimpleNamespace(task_id="other"),
            _request(),
            _Db(),
            agent,
        )
    assert task_mismatch.value.status_code == 400

    monkeypatch.setattr(
        smb_router.smb_service,
        "submit_task_result",
        AsyncMock(side_effect=RedisError("down")),
    )
    with pytest.raises(HTTPException) as unavailable:
        await smb_router.submit_task_result(
            "agent-1", "task", SimpleNamespace(task_id="task"), _request(), _Db(), agent
        )
    assert unavailable.value.status_code == 503


@pytest.mark.asyncio
async def test_content_fetch_public_endpoint_maps_errors(monkeypatch):
    user = SimpleNamespace(id="user-1", tenant_id="tenant-1")
    service = smb_router.smb_service
    monkeypatch.setattr(
        service,
        "request_content_fetch",
        AsyncMock(side_effect=ValueError("File not found")),
    )
    with pytest.raises(HTTPException) as missing:
        await smb_router.request_content_fetch(
            "file", request=_request(), db=_Db(), user=user
        )
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        service,
        "request_content_fetch",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    with pytest.raises(HTTPException) as unavailable:
        await smb_router.request_content_fetch(
            "file", request=_request(), db=_Db(), user=user
        )
    assert unavailable.value.status_code == 503


@pytest.mark.asyncio
async def test_get_agent_update_manifest_and_agent_branches(monkeypatch):
    admin = SimpleNamespace(tenant_id="tenant-1")
    with pytest.raises(HTTPException) as manifest_error:
        monkeypatch.setattr(
            smb_router,
            "fetch_agent_manifest",
            AsyncMock(side_effect=RuntimeError("down")),
        )
        await smb_router.get_agent_update("agent", _Db(), admin)
    assert manifest_error.value.status_code == 503

    monkeypatch.setattr(
        smb_router,
        "fetch_agent_manifest",
        AsyncMock(return_value={"target_version": "0.15.0", "manifest_id": "m1"}),
    )
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    with pytest.raises(HTTPException) as not_found:
        await smb_router.get_agent_update("agent", _Db(_ScalarResult(None)), admin)
    assert not_found.value.status_code == 404

    agent = SimpleNamespace(
        id="agent",
        agent_version="0.14.0",
        update_status="queued",
        update_target_version="0.15.0",
        update_task_id="task",
        update_requested_at=None,
        update_completed_at=None,
        update_error=None,
    )
    result = await smb_router.get_agent_update(
        "agent", _Db(_ScalarResult(agent)), admin
    )
    assert result["latest_version"] == "0.15.0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("detail", "status"),
    [
        ("Agent not found", 404),
        ("Agent is offline", 409),
        ("Agent requires TLS", 409),
        ("unexpected queue failure", 503),
    ],
)
async def test_request_agent_update_maps_service_errors(detail, status, monkeypatch):
    admin = SimpleNamespace(tenant_id="tenant-1")
    monkeypatch.setattr(
        smb_router.smb_service,
        "enqueue_agent_update",
        AsyncMock(side_effect=ValueError(detail)),
    )
    with pytest.raises(HTTPException) as error:
        await smb_router.request_agent_update("agent", _request(), _Db(), admin)
    assert error.value.status_code == status


@pytest.mark.asyncio
async def test_request_agent_update_returns_ack(monkeypatch):
    admin = SimpleNamespace(tenant_id="tenant-1")
    monkeypatch.setattr(
        smb_router.smb_service,
        "enqueue_agent_update",
        AsyncMock(return_value=("task", "agent", {"manifest_id": "m"})),
    )
    db = _Db()
    ack = await smb_router.request_agent_update("agent", _request(), db, admin)
    assert ack.task_id == "task"


@pytest.mark.asyncio
async def test_share_task_result_handles_missing_pending_and_failures(monkeypatch):
    admin = SimpleNamespace(tenant_id="tenant-1")
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    with pytest.raises(HTTPException) as missing:
        await smb_router.get_share_task_result(
            "share", "task", _request(), _Db(_ScalarResult(None)), admin
        )
    assert missing.value.status_code == 404

    monkeypatch.setattr(
        smb_router.smb_service, "get_task_result", AsyncMock(return_value=None)
    )
    pending = await smb_router.get_share_task_result(
        "share", "task", _request(), _Db(_ScalarResult(object())), admin
    )
    assert pending["status"] == "pending"
    monkeypatch.setattr(
        smb_router.smb_service,
        "get_task_result",
        AsyncMock(side_effect=ValueError("expired")),
    )
    with pytest.raises(HTTPException) as expired:
        await smb_router.get_share_task_result(
            "share", "task", _request(), _Db(_ScalarResult(object())), admin
        )
    assert expired.value.status_code == 404


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_scheduler_reconciles_with_existing_and_lazy_redis(monkeypatch):
    session = SimpleNamespace(rollback=AsyncMock())
    apply_context = AsyncMock()
    monkeypatch.setattr(
        scheduler_module, "_apply_scheduler_tenant_context", apply_context
    )
    reconcile = AsyncMock(return_value=2)
    monkeypatch.setattr("app.services.smb.reconcile_queued_agent_updates", reconcile)
    scheduler = scheduler_module.LegalScheduler()
    scheduler.redis = SimpleNamespace()
    monkeypatch.setattr(
        scheduler_module, "async_session_maker", lambda: _SessionContext(session)
    )
    await scheduler._reconcile_smb_agent_updates.__wrapped__(scheduler)
    reconcile.assert_awaited_once_with(session, scheduler.redis)
    apply_context.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_scheduler_lazy_redis_closes_and_rolls_back_on_failure(monkeypatch):
    session = SimpleNamespace(rollback=AsyncMock())
    redis = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        scheduler_module, "_apply_scheduler_tenant_context", AsyncMock()
    )
    monkeypatch.setattr(
        scheduler_module, "async_session_maker", lambda: _SessionContext(session)
    )
    monkeypatch.setattr(scheduler_module.settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(aioredis_module, "from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr(
        "app.services.smb.reconcile_queued_agent_updates",
        AsyncMock(side_effect=RuntimeError("down")),
    )
    scheduler = scheduler_module.LegalScheduler()
    await scheduler._reconcile_smb_agent_updates.__wrapped__(scheduler)
    session.rollback.assert_awaited_once()
    redis.aclose.assert_awaited_once()
