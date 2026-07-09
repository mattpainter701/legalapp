from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.routers import auth


def _request_without_redis():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


def _clear_oauth_fallbacks():
    auth._fallback_states.clear()
    auth._fallback_state_data.clear()
    auth._fallback_callback_tokens.clear()
    auth._fallback_callback_replays.clear()


def _callback_code(response):
    location = response.headers["location"]
    return parse_qs(urlparse(location).query)["code"][0]


@pytest.mark.asyncio
async def test_duplicate_provider_callback_replays_fresh_frontend_code():
    _clear_oauth_fallbacks()
    request = _request_without_redis()

    await auth._save_callback_replay(request, "state-123", "provider-code", "jwt-token")

    first = await auth._replay_frontend_callback(request, "state-123", "provider-code")
    assert first is not None
    assert first.status_code == 307
    first_code = _callback_code(first)
    assert await auth._consume_callback_token(request, first_code) == "jwt-token"
    assert await auth._consume_callback_token(request, first_code) is None

    second = await auth._replay_frontend_callback(request, "state-123", "provider-code")
    assert second is not None
    second_code = _callback_code(second)
    assert second_code != first_code
    assert await auth._consume_callback_token(request, second_code) == "jwt-token"


@pytest.mark.asyncio
async def test_provider_callback_replay_is_bound_to_exact_state_and_code():
    _clear_oauth_fallbacks()
    request = _request_without_redis()

    await auth._save_callback_replay(request, "state-123", "provider-code", "jwt-token")

    assert (
        await auth._replay_frontend_callback(
            request, "state-123", "other-provider-code"
        )
        is None
    )
    assert (
        await auth._replay_frontend_callback(request, "other-state", "provider-code")
        is None
    )


@pytest.mark.asyncio
async def test_provider_callback_replay_expires():
    _clear_oauth_fallbacks()
    request = _request_without_redis()
    key = auth._callback_replay_key("state-123", "provider-code")
    auth._fallback_callback_replays[key] = (
        "jwt-token",
        auth._time.time() - auth._CALLBACK_REPLAY_TTL - 1,
    )

    assert (
        await auth._replay_frontend_callback(request, "state-123", "provider-code")
        is None
    )
