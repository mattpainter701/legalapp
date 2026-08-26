from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
import redis.asyncio as aioredis

from app import main as app_main
from app.database import set_smb_agent_bootstrap_lookup
from app.middleware import smb_auth
from app.services import scheduler as scheduler_module
from app.services.scheduler import LegalScheduler


@pytest.mark.asyncio
async def test_smb_bootstrap_lookup_rejects_zero_or_two_selectors():
    with pytest.raises(ValueError, match="exactly one SMB bootstrap"):
        await set_smb_agent_bootstrap_lookup(AsyncMock())
    with pytest.raises(ValueError, match="exactly one SMB bootstrap"):
        await set_smb_agent_bootstrap_lookup(
            AsyncMock(), api_key_hash="hash", pairing_code="code"
        )


@pytest.mark.asyncio
async def test_invalid_smb_agent_key_is_rate_limited(monkeypatch):
    request = SimpleNamespace(
        headers={"X-Agent-API-Key": "invalid"},
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    rate_limit = AsyncMock(side_effect=HTTPException(status_code=429))
    monkeypatch.setattr(smb_auth, "_check_smb_rate_limit", rate_limit)

    with pytest.raises(HTTPException) as exc_info:
        await smb_auth.get_smb_agent(request, db)
    assert exc_info.value.status_code == 429
    rate_limit.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_lifespan_assigns_redis_to_started_scheduler(monkeypatch):
    class _Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement):
            return SimpleNamespace(fetchone=lambda: (False, False))

    redis = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    engine = SimpleNamespace(
        connect=Mock(return_value=_Connection()), dispose=AsyncMock()
    )
    scheduler = SimpleNamespace(start=Mock(), shutdown=Mock())
    monkeypatch.setattr(app_main.settings, "RUN_SCHEDULER", True, raising=False)
    monkeypatch.setattr(app_main.settings, "DEV_MODE", True, raising=False)
    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", False, raising=False)
    monkeypatch.setattr(app_main.settings, "UPLOAD_DIR", "uploads", raising=False)
    monkeypatch.setattr(app_main.aioredis, "from_url", lambda *a, **k: redis)
    monkeypatch.setattr(app_main, "engine", engine)
    monkeypatch.setattr(app_main, "LegalScheduler", lambda: scheduler)
    for manager in (
        app_main.cache_manager,
        app_main.plugin_cache_manager,
        app_main.matter_context_cache_manager,
        app_main.communication_context_cache,
    ):
        monkeypatch.setattr(manager, "init", AsyncMock())
        monkeypatch.setattr(manager, "close", AsyncMock())

    @asynccontextmanager
    async def _lifespan():
        yield

    monkeypatch.setattr(app_main, "protocol_lifespan", _lifespan)
    monkeypatch.setattr(app_main, "workspace_protocol_lifespan", _lifespan)
    app = SimpleNamespace(state=SimpleNamespace())
    async with app_main.lifespan(app):
        assert scheduler.redis is redis
    scheduler.start.assert_called_once_with()


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_reconcile_smb_updates_uses_existing_redis_and_logs_restore(monkeypatch):
    session = SimpleNamespace(rollback=AsyncMock())
    redis = SimpleNamespace()
    reconcile = AsyncMock(return_value=2)
    monkeypatch.setattr(
        scheduler_module, "async_session_maker", lambda: _SessionContext(session)
    )
    monkeypatch.setattr(
        scheduler_module, "_apply_scheduler_tenant_context", AsyncMock()
    )
    monkeypatch.setattr("app.services.smb.reconcile_queued_agent_updates", reconcile)
    job = LegalScheduler()
    job.redis = redis
    token = scheduler_module._scheduler_tenant_id.set(uuid4())
    try:
        await job._reconcile_smb_agent_updates()
    finally:
        scheduler_module._scheduler_tenant_id.reset(token)
    reconcile.assert_awaited_once_with(session, redis)


@pytest.mark.asyncio
async def test_reconcile_smb_updates_closes_lazy_redis(monkeypatch):
    session = SimpleNamespace(rollback=AsyncMock())
    redis = SimpleNamespace(aclose=AsyncMock())
    reconcile = AsyncMock(return_value=0)
    monkeypatch.setattr(
        scheduler_module, "async_session_maker", lambda: _SessionContext(session)
    )
    monkeypatch.setattr(
        scheduler_module, "_apply_scheduler_tenant_context", AsyncMock()
    )
    monkeypatch.setattr("app.services.smb.reconcile_queued_agent_updates", reconcile)
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: redis)
    job = LegalScheduler()
    token = scheduler_module._scheduler_tenant_id.set(uuid4())
    try:
        await job._reconcile_smb_agent_updates()
    finally:
        scheduler_module._scheduler_tenant_id.reset(token)
    redis.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reconcile_smb_updates_rolls_back_on_failure(monkeypatch):
    session = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(
        scheduler_module, "async_session_maker", lambda: _SessionContext(session)
    )
    monkeypatch.setattr(
        scheduler_module, "_apply_scheduler_tenant_context", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.smb.reconcile_queued_agent_updates",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    job = LegalScheduler()
    job.redis = object()
    token = scheduler_module._scheduler_tenant_id.set(uuid4())
    try:
        await job._reconcile_smb_agent_updates()
    finally:
        scheduler_module._scheduler_tenant_id.reset(token)
    session.rollback.assert_awaited_once_with()
