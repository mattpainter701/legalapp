"""Tests for the provider token revocation helpers added to token_vault.py."""

import httpx
import pytest

from app.services import token_vault


class TestRevokeGoogleToken:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        async def fake_post(self, url, *args, **kwargs):
            assert url == "https://oauth2.googleapis.com/revoke"
            assert kwargs["data"] == {"token": "the-token"}
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        assert await token_vault.revoke_google_token("the-token") is True

    @pytest.mark.asyncio
    async def test_provider_error_returns_false(self, monkeypatch):
        async def fake_post(self, url, *args, **kwargs):
            return httpx.Response(
                400, json={"error": "invalid_token"}, request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        assert await token_vault.revoke_google_token("bad-token") is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self, monkeypatch):
        async def fake_post(self, url, *args, **kwargs):
            raise httpx.ConnectTimeout("boom")

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        assert await token_vault.revoke_google_token("the-token") is False


class TestRevokeMicrosoftToken:
    @pytest.mark.asyncio
    async def test_is_always_a_documented_no_op(self):
        # No safe per-token revoke API exists for MS confidential-client
        # tokens; this must never silently claim success.
        assert await token_vault.revoke_microsoft_token("any-token") is False


class TestRevokeProviderToken:
    @pytest.mark.asyncio
    async def test_prefers_refresh_token_for_google(self, monkeypatch):
        seen = {}

        async def fake_post(self, url, *args, **kwargs):
            seen["token"] = kwargs["data"]["token"]
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        result = await token_vault.revoke_provider_token(
            "google", access_token="access-1", refresh_token="refresh-1"
        )
        assert result is True
        assert seen["token"] == "refresh-1"

    @pytest.mark.asyncio
    async def test_falls_back_to_access_token(self, monkeypatch):
        seen = {}

        async def fake_post(self, url, *args, **kwargs):
            seen["token"] = kwargs["data"]["token"]
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        result = await token_vault.revoke_provider_token(
            "google", access_token="access-1", refresh_token=None
        )
        assert result is True
        assert seen["token"] == "access-1"

    @pytest.mark.asyncio
    async def test_no_tokens_returns_false_without_network_call(self, monkeypatch):
        def fail_if_called(self, *args, **kwargs):
            raise AssertionError("should not make a network call with no tokens")

        monkeypatch.setattr(httpx.AsyncClient, "post", fail_if_called)
        result = await token_vault.revoke_provider_token(
            "google", access_token=None, refresh_token=None
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_false(self):
        result = await token_vault.revoke_provider_token(
            "zoom", access_token="a", refresh_token="r"
        )
        assert result is False
