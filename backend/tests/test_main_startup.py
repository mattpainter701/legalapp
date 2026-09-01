from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import main as app_main


def _fake_redis():
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.aclose = AsyncMock()
    return redis


def _fake_engine(message: str = "down"):
    connect = Mock(side_effect=ValueError(message))
    return SimpleNamespace(
        connect=connect,
        dispose=AsyncMock(),
    )


def _stub_app():
    return SimpleNamespace(state=SimpleNamespace())


@asynccontextmanager
async def _noop_protocol_lifespan():
    """Keep DB-startup tests from consuming the SDK manager's one lifecycle."""
    yield


@pytest.mark.asyncio
async def test_lifespan_continues_in_dev_mode_when_db_connectivity_fails(monkeypatch):
    """DEV_MODE startup keeps serving when first DB ping fails."""
    app = _stub_app()
    redis = _fake_redis()

    monkeypatch.setattr(app_main.settings, "DEV_MODE", True, raising=False)
    monkeypatch.setattr(app_main.settings, "RUN_SCHEDULER", False, raising=False)
    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", False, raising=False)
    monkeypatch.setattr(app_main.aioredis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(app_main, "engine", _fake_engine())
    monkeypatch.setattr(app_main.cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main, "protocol_lifespan", _noop_protocol_lifespan)

    async with app_main.lifespan(app):
        pass

    assert app_main.engine.connect.call_count == 1
    assert app_main.cache_manager.init.called
    assert app_main.plugin_cache_manager.init.called
    assert app_main.cache_manager.close.called
    assert app_main.plugin_cache_manager.close.called


@pytest.mark.asyncio
async def test_lifespan_fails_closed_when_db_connectivity_fails_in_production(
    monkeypatch,
):
    """Production startup raises on DB connectivity failure."""
    app = _stub_app()
    redis = _fake_redis()

    monkeypatch.setattr(app_main.settings, "DEV_MODE", False, raising=False)
    monkeypatch.setattr(app_main.settings, "RUN_SCHEDULER", False, raising=False)
    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", False, raising=False)
    monkeypatch.setattr(app_main.aioredis, "from_url", lambda *args, **kwargs: redis)
    fake_engine = _fake_engine()
    monkeypatch.setattr(app_main, "engine", fake_engine)
    monkeypatch.setattr(app_main.cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main, "protocol_lifespan", _noop_protocol_lifespan)

    with pytest.raises(RuntimeError, match="Database connectivity probe failed"):
        async with app_main.lifespan(app):
            pass

    assert fake_engine.connect.call_count == 1
    assert not app_main.cache_manager.init.called


@pytest.mark.asyncio
async def test_lifespan_initializes_studio_render_runtime_when_enabled(monkeypatch):
    app = _stub_app()
    redis = _fake_redis()

    monkeypatch.setattr(app_main.settings, "DEV_MODE", True, raising=False)
    monkeypatch.setattr(app_main.settings, "RUN_SCHEDULER", False, raising=False)
    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", False, raising=False)
    monkeypatch.setattr(
        app_main.settings, "TEMPLATE_STUDIO_RENDER_ENABLED", True, raising=False
    )
    monkeypatch.setattr(app_main.aioredis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(app_main, "engine", _fake_engine())
    monkeypatch.setattr(app_main.cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main, "protocol_lifespan", _noop_protocol_lifespan)

    fake_store = Mock()
    fake_manifests = {("kind", "fmt", 1): Mock()}
    fake_capabilities = Mock()
    fake_runtime = SimpleNamespace(
        object_store=fake_store,
        manifests=fake_manifests,
        capabilities=fake_capabilities,
    )
    monkeypatch.setattr(
        app_main, "build_studio_render_api_runtime", lambda _settings: fake_runtime
    )

    async with app_main.lifespan(app):
        assert app.state.studio_render_object_store is fake_store
        assert app.state.studio_render_manifests == dict(fake_manifests)
        assert app.state.studio_render_capabilities is fake_capabilities


@pytest.mark.asyncio
async def test_lifespan_survives_studio_render_runtime_error(monkeypatch):
    app = _stub_app()
    redis = _fake_redis()

    monkeypatch.setattr(app_main.settings, "DEV_MODE", True, raising=False)
    monkeypatch.setattr(app_main.settings, "RUN_SCHEDULER", False, raising=False)
    monkeypatch.setattr(app_main.settings, "LITELLM_ENABLED", False, raising=False)
    monkeypatch.setattr(
        app_main.settings, "TEMPLATE_STUDIO_RENDER_ENABLED", True, raising=False
    )
    monkeypatch.setattr(app_main.aioredis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(app_main, "engine", _fake_engine())
    monkeypatch.setattr(app_main.cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "init", AsyncMock())
    monkeypatch.setattr(app_main.plugin_cache_manager, "close", AsyncMock())
    monkeypatch.setattr(app_main, "protocol_lifespan", _noop_protocol_lifespan)

    from app.services.studio_render_runtime import StudioRenderRuntimeError

    def _raise_runtime_error(_settings):
        raise StudioRenderRuntimeError("runtime unavailable")

    monkeypatch.setattr(
        app_main,
        "build_studio_render_api_runtime",
        _raise_runtime_error,
    )

    async with app_main.lifespan(app):
        assert app.state.studio_render_object_store is None
        assert app.state.studio_render_manifests is None
        assert app.state.studio_render_capabilities is None


@pytest.mark.asyncio
async def test_health_readiness_reports_studio_render_state(monkeypatch):
    monkeypatch.setattr(
        app_main.settings, "TEMPLATE_STUDIO_RENDER_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        app_main.settings, "TEMPLATE_STUDIO_RENDER_HEALTH_MAX_AGE_SECONDS", 60
    )

    class _FreshStore:
        def worker_heartbeat_fresh(self, *, max_age_seconds):
            assert max_age_seconds == 60
            return True

    class _StaleStore:
        def worker_heartbeat_fresh(self, *, max_age_seconds):
            return False

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(studio_render_object_store=_FreshStore()))
    )
    response = await app_main.health_readiness(request)
    assert response.status_code == 503
    assert response.body is not None
    body = response.body.decode()
    assert '"studio_render":"ok"' in body

    request.app.state.studio_render_object_store = _StaleStore()
    response = await app_main.health_readiness(request)
    body = response.body.decode()
    assert '"studio_render":"unavailable"' in body

    monkeypatch.setattr(
        app_main.settings, "TEMPLATE_STUDIO_RENDER_ENABLED", False, raising=False
    )
    response = await app_main.health_readiness(request)
    body = response.body.decode()
    assert "studio_render" not in body
