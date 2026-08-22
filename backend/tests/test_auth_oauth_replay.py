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

    await auth._save_callback_replay(
        request,
        "state-123",
        "provider-code",
        "jwt-token",
        "/workspace-mcp/authorize?request_id=abc",
    )

    first = await auth._replay_frontend_callback(request, "state-123", "provider-code")
    assert first is not None
    assert first.status_code == 307
    first_code = _callback_code(first)
    assert await auth._consume_callback_token(request, first_code) == (
        "jwt-token",
        "/workspace-mcp/authorize?request_id=abc",
    )
    assert await auth._consume_callback_token(request, first_code) is None

    second = await auth._replay_frontend_callback(request, "state-123", "provider-code")
    assert second is not None
    second_code = _callback_code(second)
    assert second_code != first_code
    assert await auth._consume_callback_token(request, second_code) == (
        "jwt-token",
        "/workspace-mcp/authorize?request_id=abc",
    )


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


@pytest.mark.parametrize(
    "value",
    [
        "https://attacker.example/callback",
        "//attacker.example/callback",
        "/\\attacker.example",
        "/matters\nnext",
        "/" + "a" * 2048,
    ],
)
def test_oauth_return_path_rejects_external_or_ambiguous_values(value):
    assert auth._validated_return_to(value) is None


def test_oauth_return_path_accepts_bounded_internal_path():
    path = "/workspace-mcp/authorize?request_id=abc"
    assert auth._validated_return_to(path) == path


@pytest.mark.asyncio
async def test_callback_token_accepts_legacy_value_without_continuation():
    _clear_oauth_fallbacks()
    request = _request_without_redis()
    code = "legacy-callback-code"
    auth._fallback_callback_tokens[code] = ("legacy-jwt", auth._time.time())

    assert await auth._consume_callback_token(request, code) == ("legacy-jwt", None)


@pytest.mark.parametrize(
    "provider_login",
    [auth.microsoft_login, auth.google_login],
    ids=["microsoft", "google"],
)
@pytest.mark.asyncio
async def test_provider_login_keeps_continuation_in_server_side_state(
    monkeypatch, provider_login
):
    _clear_oauth_fallbacks()
    monkeypatch.setattr(auth, "_oauth_configured", lambda *_args: True)
    request = _request_without_redis()
    path = "/workspace-mcp/authorize?request_id=abc"

    response = await provider_login(request, return_to=path)

    provider_state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    assert auth._fallback_state_data[provider_state]["return_to"] == path
    assert "return_to" not in response.headers["location"]
