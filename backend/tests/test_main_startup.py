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

    with pytest.raises(RuntimeError, match="Database connectivity probe failed"):
        async with app_main.lifespan(app):
            pass

    assert fake_engine.connect.call_count == 1
    assert not app_main.cache_manager.init.called
