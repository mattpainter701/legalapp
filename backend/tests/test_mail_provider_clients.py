import base64

import httpx
import pytest

from app.services.provider_http import ProviderAuthError, ProviderError


@pytest.mark.asyncio
async def test_gmail_read_mail_uses_provider_client_and_skips_detail_failures(
    monkeypatch,
):
    from app.services import google_mail

    async def fake_token(db, tenant_id, user_id, provider):
        return "google-token"

    calls: list[tuple[str, str, dict]] = []

    async def fake_gmail_request(method, path, *, token, params=None):
        calls.append((method, path, params or {}))
        assert token == "google-token"
        if path == "/users/me/messages":
            return httpx.Response(
                200,
                json={"messages": [{"id": "m1"}, {"id": "m2"}]},
            )
        if path.endswith("/m1"):
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "Preview",
                    "labelIds": ["IMPORTANT"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "sender@example.com"},
                            {"name": "To", "value": "attorney@example.com"},
                            {"name": "Subject", "value": "Matter update"},
                            {"name": "Date", "value": "Thu, 02 Jul 2026 10:00:00 GMT"},
                        ]
                    },
                },
            )
        raise ProviderError("detail failed", status_code=503)

    monkeypatch.setattr(google_mail, "get_fresh_user_token", fake_token)
    monkeypatch.setattr(google_mail, "gmail_request", fake_gmail_request)

    messages = await google_mail.gmail_read_mail(None, "tenant", "user")

    assert len(messages) == 1
    assert messages[0]["id"] == "m1"
    assert messages[0]["subject"] == "Matter update"
    assert messages[0]["importance"] == "high"
    assert calls[0][1] == "/users/me/messages"


@pytest.mark.asyncio
async def test_gmail_read_raw_raises_provider_error_for_missing_raw(monkeypatch):
    from app.services import google_mail

    async def fake_token(db, tenant_id, user_id, provider):
        return "google-token"

    async def fake_gmail_request(method, path, *, token, params=None):
        return httpx.Response(200, json={})

    monkeypatch.setattr(google_mail, "get_fresh_user_token", fake_token)
    monkeypatch.setattr(google_mail, "gmail_request", fake_gmail_request)

    with pytest.raises(ProviderError):
        await google_mail.gmail_read_raw(None, "tenant", "user", "message-id")


@pytest.mark.asyncio
async def test_gmail_read_raw_decodes_raw_message(monkeypatch):
    from app.services import google_mail

    encoded = base64.urlsafe_b64encode(b"Subject: Test\r\n\r\nBody").decode()

    async def fake_token(db, tenant_id, user_id, provider):
        return "google-token"

    async def fake_gmail_request(method, path, *, token, params=None):
        return httpx.Response(200, json={"raw": encoded})

    monkeypatch.setattr(google_mail, "get_fresh_user_token", fake_token)
    monkeypatch.setattr(google_mail, "gmail_request", fake_gmail_request)

    assert await google_mail.gmail_read_raw(None, "tenant", "user", "message-id") == (
        b"Subject: Test\r\n\r\nBody"
    )


@pytest.mark.asyncio
async def test_microsoft_read_mail_user_uses_graph_client(monkeypatch):
    from app.services import microsoft_mail

    async def fake_token(db, tenant_id, user_id, provider):
        return "ms-token"

    async def fake_graph_request(method, path, *, token, params=None):
        assert method == "GET"
        assert path == "/me/messages"
        assert token == "ms-token"
        assert "$filter" in params
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "subject": "Graph subject",
                        "bodyPreview": "Body",
                        "from": {
                            "emailAddress": {
                                "address": "sender@example.com",
                                "name": "Sender",
                            }
                        },
                        "toRecipients": [
                            {"emailAddress": {"address": "attorney@example.com"}}
                        ],
                        "receivedDateTime": "2026-07-02T10:00:00Z",
                        "isRead": True,
                        "importance": "normal",
                        "hasAttachments": False,
                        "conversationId": "c1",
                    }
                ]
            },
        )

    monkeypatch.setattr(microsoft_mail, "get_fresh_user_token", fake_token)
    monkeypatch.setattr(microsoft_mail, "graph_request", fake_graph_request)

    messages = await microsoft_mail.ms_read_mail_user(None, "tenant", "user")

    assert messages[0]["id"] == "m1"
    assert messages[0]["from"] == "sender@example.com"
    assert messages[0]["to"] == ["attorney@example.com"]


@pytest.mark.asyncio
async def test_microsoft_tenant_mail_skips_provider_auth_failures(monkeypatch):
    from app.services import microsoft_mail

    async def fake_tenant_token(db, tenant_id, provider):
        return "tenant-token"

    async def fake_graph_request(method, path, *, token, params=None):
        return httpx.Response(
            200,
            json={"value": [{"id": "u1", "mail": "user@example.com"}]},
        )

    async def fake_user_mail(*args, **kwargs):
        raise ProviderAuthError("unauthorized", status_code=401)

    monkeypatch.setattr(microsoft_mail, "get_fresh_token", fake_tenant_token)
    monkeypatch.setattr(microsoft_mail, "graph_request", fake_graph_request)
    monkeypatch.setattr(microsoft_mail, "ms_read_mail_user", fake_user_mail)

    assert await microsoft_mail.ms_read_mail_tenant(None, "tenant") == []
