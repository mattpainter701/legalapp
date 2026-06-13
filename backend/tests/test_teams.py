"""Tests for the Phase 1 Microsoft Teams integration.

Covers gating (active-MS + scope detection), link CRUD, the status endpoint's
Teams fields, the notification dispatcher payload, the Adaptive Card builder,
and 429 throttling backoff in the Graph request wrapper.
"""

import uuid

import httpx
import pytest
import pytest_asyncio

from app.models.tenant_credential import TenantCredential
from app.models.plugin import Matter
from app.models.user_oauth_token import UserOAuthToken
from app.services.teams import TEAMS_REQUIRED_SCOPES


async def _add_ms_credential(db_session, tenant_id, scopes: str):
    cred = TenantCredential(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="microsoft",
        encrypted_access_token="placeholder",
        encrypted_refresh_token=None,
        scopes=scopes,
        is_active=True,
    )
    db_session.add(cred)
    await db_session.commit()
    return cred


async def _add_matter(db_session, tenant_id, user_id):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        slug=f"matter-{uuid.uuid4().hex[:8]}",
        matter_name="Acme v. Globex",
    )
    db_session.add(matter)
    await db_session.commit()
    await db_session.refresh(matter)
    return matter


@pytest_asyncio.fixture
async def ms_connected(db_session, test_tenant):
    """Tenant with a fully-scoped active Microsoft credential."""
    base = "offline_access User.Read.All"
    return await _add_ms_credential(
        db_session, test_tenant.id, f"{base} {TEAMS_REQUIRED_SCOPES}"
    )


# ── Gating ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGating:
    async def test_no_ms_credential_returns_409(self, client):
        resp = await client.get("/api/integrations/teams/teams")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "teams_not_connected"

    async def test_missing_teams_scopes_returns_403(
        self, client, db_session, test_tenant
    ):
        await _add_ms_credential(db_session, test_tenant.id, "offline_access User.Read.All")
        resp = await client.get("/api/integrations/teams/teams")
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "teams_scopes_missing"
        assert "ChannelMessage.Send" in detail["missing_scopes"]

    async def test_fully_scoped_passes(self, client, ms_connected, monkeypatch):
        from app.services import teams as teams_service

        async def fake_list(tenant_id, user_id=None):
            return [{"id": "team-1", "display_name": "Litigation"}]

        monkeypatch.setattr(teams_service, "list_joined_teams", fake_list)
        resp = await client.get("/api/integrations/teams/teams")
        assert resp.status_code == 200
        assert resp.json()[0]["display_name"] == "Litigation"


# ── Status endpoint ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStatus:
    async def test_teams_connected_true_when_scoped(self, client, ms_connected):
        resp = await client.get("/api/integrations/status")
        assert resp.status_code == 200
        ms = resp.json()["microsoft"]
        assert ms["teams_connected"] is True
        assert ms["teams_missing_scopes"] == []

    async def test_teams_missing_scopes_reported(
        self, client, db_session, test_tenant
    ):
        await _add_ms_credential(db_session, test_tenant.id, "offline_access User.Read.All")
        resp = await client.get("/api/integrations/status")
        ms = resp.json()["microsoft"]
        assert ms["teams_connected"] is False
        assert "Chat.ReadWrite" in ms["teams_missing_scopes"]

    async def test_disconnected_reports_all_missing(self, client):
        resp = await client.get("/api/integrations/status")
        ms = resp.json()["microsoft"]
        assert ms["teams_connected"] is False
        assert set(ms["teams_missing_scopes"]) == set(TEAMS_REQUIRED_SCOPES.split())


# ── Link CRUD ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLinkCRUD:
    async def test_create_list_delete(
        self, client, ms_connected, db_session, test_tenant, test_user
    ):
        matter = await _add_matter(db_session, test_tenant.id, test_user.id)

        create = await client.post(
            "/api/integrations/teams/links",
            json={
                "matter_id": str(matter.id),
                "team_id": "team-1",
                "channel_id": "chan-1",
                "team_display_name": "Litigation",
                "channel_display_name": "General",
            },
        )
        assert create.status_code == 201
        link_id = create.json()["id"]

        listed = await client.get("/api/integrations/teams/links")
        assert listed.status_code == 200
        assert any(row["id"] == link_id for row in listed.json())

        deleted = await client.delete(f"/api/integrations/teams/links/{link_id}")
        assert deleted.status_code == 204

        listed2 = await client.get("/api/integrations/teams/links")
        assert all(row["id"] != link_id for row in listed2.json())

    async def test_create_is_idempotent(
        self, client, ms_connected, db_session, test_tenant, test_user
    ):
        matter = await _add_matter(db_session, test_tenant.id, test_user.id)
        body = {
            "matter_id": str(matter.id),
            "team_id": "team-1",
            "channel_id": "chan-1",
        }
        first = await client.post("/api/integrations/teams/links", json=body)
        second = await client.post("/api/integrations/teams/links", json=body)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

    async def test_create_rejects_matter_outside_tenant(
        self, client, ms_connected, db_session, test_user
    ):
        matter = await _add_matter(db_session, uuid.uuid4(), test_user.id)

        create = await client.post(
            "/api/integrations/teams/links",
            json={
                "matter_id": str(matter.id),
                "team_id": "team-1",
                "channel_id": "chan-1",
            },
        )

        assert create.status_code == 404
        assert create.json()["detail"] == "Matter not found"

    async def test_notification_settings_reject_matter_outside_tenant(
        self, client, ms_connected, db_session, test_user
    ):
        matter = await _add_matter(db_session, uuid.uuid4(), test_user.id)

        resp = await client.put(
            "/api/integrations/teams/notification-settings",
            json={
                "settings": [
                    {
                        "event_type": "deadline_approaching",
                        "team_id": "team-1",
                        "channel_id": "chan-1",
                        "matter_id": str(matter.id),
                        "is_enabled": True,
                    }
                ]
            },
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Matter not found"


@pytest.mark.asyncio
async def test_get_token_falls_back_when_user_token_lacks_teams_scopes(
    db_session, test_tenant, test_user, ms_connected, monkeypatch
):
    from app.services import teams as teams_service

    user_token = UserOAuthToken(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        provider="microsoft",
        encrypted_access_token="placeholder",
        encrypted_refresh_token=None,
        scopes="offline_access User.Read",
    )
    db_session.add(user_token)
    await db_session.commit()

    calls = {"user": 0, "tenant_credential_id": None}

    async def fake_user_token(*args, **kwargs):
        calls["user"] += 1
        return "user-token"

    async def fake_tenant_token(db, tenant_id, provider, credential_id=None):
        calls["tenant_credential_id"] = credential_id
        return "tenant-token"

    monkeypatch.setattr(teams_service, "get_fresh_user_token", fake_user_token)
    monkeypatch.setattr(teams_service, "get_fresh_token", fake_tenant_token)

    token = await teams_service._get_token(str(test_tenant.id), str(test_user.id))

    assert token == "tenant-token"
    assert calls["user"] == 0
    assert calls["tenant_credential_id"] == str(ms_connected.id)


# ── Dispatcher ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDispatch:
    async def test_notify_builds_card_and_posts(
        self, ms_connected, db_session, test_tenant, test_user, monkeypatch
    ):
        from app.services import teams_notify
        from app.services import teams as teams_service

        matter = await _add_matter(db_session, test_tenant.id, test_user.id)
        link = __import__(
            "app.models.teams_channel_link", fromlist=["TeamsChannelLink"]
        ).TeamsChannelLink(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=matter.id,
            team_id="team-1",
            channel_id="chan-1",
            is_active=True,
        )
        db_session.add(link)
        await db_session.commit()

        captured = {}

        async def fake_send(tenant_id, team_id, channel_id, *, adaptive_card=None, **kw):
            captured["card"] = adaptive_card
            captured["channel"] = channel_id
            return True

        monkeypatch.setattr(teams_service, "send_channel_message", fake_send)

        sent = await teams_notify.notify(
            str(test_tenant.id),
            "deadline_approaching",
            title="Deadline approaching",
            fields={"matter_name": "Acme v. Globex", "Due": "2026-07-01"},
            matter_id=str(matter.id),
            deep_link="https://app/matters/x",
        )

        assert sent == 1
        assert captured["channel"] == "chan-1"
        card = captured["card"]
        assert card["type"] == "AdaptiveCard"
        # FactSet should carry the Due field; matter_name is the subtitle.
        facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
        assert any(f["title"] == "Due" for f in facts)
        assert card["actions"][0]["url"] == "https://app/matters/x"

    async def test_notify_noop_when_not_connected(self, test_tenant):
        from app.services import teams_notify

        sent = await teams_notify.notify(
            str(test_tenant.id),
            "deadline_approaching",
            title="x",
            fields={},
        )
        assert sent == 0


# ── Card builder + throttling ─────────────────────────────────────────────


def test_build_matter_card_shape():
    from app.services.teams import build_matter_card

    card = build_matter_card(
        "Title", "Matter X", {"A": "1", "B": "2"}, deep_link="https://x"
    )
    assert card["version"] == "1.4"
    assert card["body"][0]["text"] == "Title"
    assert card["actions"][0]["type"] == "Action.OpenUrl"


@pytest.mark.asyncio
async def test_graph_request_retries_on_429(monkeypatch):
    from app.services import teams as teams_service

    calls = {"n": 0}

    async def fake_request(self, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"value": []})

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    monkeypatch.setattr(teams_service.asyncio, "sleep", fast_sleep)

    resp = await teams_service._graph_request("GET", "/me/joinedTeams", token="t")
    assert resp is not None
    assert resp.status_code == 200
    assert calls["n"] == 2
