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
        await _add_ms_credential(
            db_session, test_tenant.id, "offline_access User.Read.All"
        )
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


# ── Scope preservation on re-auth ─────────────────────────────────────────


@pytest.mark.asyncio
class TestReauthScopePreservation:
    async def test_reauth_without_flag_keeps_teams_scopes(self, client, ms_connected):
        resp = await client.get(
            "/api/integrations/microsoft/connect?intent=admin",
            follow_redirects=False,
        )

        assert resp.status_code in (302, 307)
        assert "ChannelMessage.Send" in resp.headers["location"]

    async def test_reauth_without_teams_history_stays_base(
        self, client, db_session, test_tenant
    ):
        await _add_ms_credential(
            db_session, test_tenant.id, "offline_access User.Read.All"
        )

        resp = await client.get(
            "/api/integrations/microsoft/connect?intent=admin",
            follow_redirects=False,
        )

        assert resp.status_code in (302, 307)
        assert "ChannelMessage.Send" not in resp.headers["location"]


# ── Status endpoint ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStatus:
    async def test_teams_connected_true_when_scoped(self, client, ms_connected):
        resp = await client.get("/api/integrations/status")
        assert resp.status_code == 200
        ms = resp.json()["microsoft"]
        assert ms["teams_connected"] is True
        assert ms["teams_missing_scopes"] == []

    async def test_teams_missing_scopes_reported(self, client, db_session, test_tenant):
        await _add_ms_credential(
            db_session, test_tenant.id, "offline_access User.Read.All"
        )
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

    async def test_links_carry_the_matter_name(
        self, client, ms_connected, db_session, test_tenant, test_user
    ):
        matter = await _add_matter(db_session, test_tenant.id, test_user.id)
        await client.post(
            "/api/integrations/teams/links",
            json={
                "matter_id": str(matter.id),
                "team_id": "team-1",
                "channel_id": "chan-1",
            },
        )

        listed = await client.get("/api/integrations/teams/links")
        row = next(r for r in listed.json() if r["matter_id"] == str(matter.id))
        assert row["matter_name"] == "Acme v. Globex"

    async def test_delete_rejects_a_malformed_id(self, client, ms_connected):
        # Previously reached the UUID column and surfaced as a 500.
        resp = await client.delete("/api/integrations/teams/links/not-a-uuid")
        assert resp.status_code == 422

    async def test_delete_reports_a_missing_link(self, client, ms_connected):
        resp = await client.delete(f"/api/integrations/teams/links/{uuid.uuid4()}")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestNotificationRouting:
    async def test_event_types_are_published(self, client, ms_connected):
        resp = await client.get("/api/integrations/teams/event-types")
        assert resp.status_code == 200
        types = {row["event_type"] for row in resp.json()}
        assert "deadline_approaching" in types
        assert all(row["label"] for row in resp.json())

    async def test_unknown_event_type_is_rejected(self, client, ms_connected):
        # An unroutable event would save happily and then never fire.
        resp = await client.put(
            "/api/integrations/teams/notification-settings",
            json={
                "settings": [
                    {
                        "event_type": "deadline_aproaching",
                        "team_id": "team-1",
                        "channel_id": "chan-1",
                    }
                ]
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "teams_unknown_event_type"
        assert detail["unknown_event_types"] == ["deadline_aproaching"]

    async def test_duplicate_routes_collapse_instead_of_failing(
        self, client, ms_connected
    ):
        route = {
            "event_type": "deadline_approaching",
            "team_id": "team-1",
            "channel_id": "chan-1",
        }
        resp = await client.put(
            "/api/integrations/teams/notification-settings",
            json={"settings": [route, dict(route)]},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_replace_is_a_full_replacement(self, client, ms_connected):
        await client.put(
            "/api/integrations/teams/notification-settings",
            json={
                "settings": [
                    {
                        "event_type": "deadline_approaching",
                        "team_id": "team-1",
                        "channel_id": "chan-1",
                    }
                ]
            },
        )
        await client.put(
            "/api/integrations/teams/notification-settings",
            json={
                "settings": [
                    {
                        "event_type": "voice_call_captured",
                        "team_id": "team-2",
                        "channel_id": "chan-2",
                    }
                ]
            },
        )
        listed = await client.get("/api/integrations/teams/notification-settings")
        assert [r["event_type"] for r in listed.json()] == ["voice_call_captured"]


@pytest.mark.asyncio
class TestChannels:
    async def test_create_channel(self, client, ms_connected, monkeypatch):
        from app.services import teams as teams_service

        captured = {}

        async def fake_create(
            tenant_id, team_id, display_name, *, description=None, user_id=None
        ):
            captured["team_id"] = team_id
            captured["display_name"] = display_name
            captured["description"] = description
            captured["user_id"] = user_id
            return {
                "id": "chan-created",
                "display_name": display_name,
                "membership_type": "standard",
            }

        monkeypatch.setattr(teams_service, "create_channel", fake_create)

        resp = await client.post(
            "/api/integrations/teams/channels",
            json={
                "team_id": "team-1",
                "display_name": "Acme v Globex",
                "description": "Matter channel",
            },
        )

        assert resp.status_code == 201
        assert resp.json()["id"] == "chan-created"
        assert resp.json()["display_name"] == "Acme v Globex"
        assert captured["team_id"] == "team-1"
        assert captured["description"] == "Matter channel"


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

        async def fake_send(
            tenant_id, team_id, channel_id, *, adaptive_card=None, **kw
        ):
            captured["card"] = adaptive_card
            captured["channel"] = channel_id
            return True

        async def fake_token(tenant_id, user_id=None):
            captured["token_calls"] = captured.get("token_calls", 0) + 1
            return "graph-token"

        monkeypatch.setattr(teams_service, "send_channel_message", fake_send)
        monkeypatch.setattr(teams_service, "resolve_token", fake_token)

        fields = {"matter_name": "Acme v. Globex", "Due": "2026-07-01"}

        sent = await teams_notify.notify(
            str(test_tenant.id),
            "deadline_approaching",
            title="Deadline approaching",
            fields=fields,
            matter_id=str(matter.id),
            deep_link="https://app/matters/x",
        )

        assert sent == 1
        assert fields == {"matter_name": "Acme v. Globex", "Due": "2026-07-01"}
        assert captured["channel"] == "chan-1"
        card = captured["card"]
        assert card["type"] == "AdaptiveCard"
        # FactSet should carry the Due field; matter_name is the subtitle.
        facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
        assert any(f["title"] == "Due" for f in facts)
        assert card["actions"][0]["url"] == "https://app/matters/x"
        # One credential resolution for the whole fan-out, not one per channel.
        assert captured["token_calls"] == 1

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


# ── Graph collection paging + error surfacing ────────────────────────────


@pytest.mark.asyncio
async def test_list_joined_teams_follows_next_link(monkeypatch):
    """A firm with more teams than one Graph page must see all of them."""
    from app.services import teams as teams_service

    pages = {
        "/me/joinedTeams": {
            "value": [{"id": "t1", "displayName": "Litigation"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2",
        },
        "https://graph.microsoft.com/v1.0/page2": {
            "value": [{"id": "t2", "displayName": "Corporate"}]
        },
    }

    async def fake_request(method, path, *, token, **kw):
        return httpx.Response(200, json=pages[path])

    monkeypatch.setattr(teams_service, "_graph_request", fake_request)
    monkeypatch.setattr(
        teams_service, "_get_token", lambda *a, **k: _immediate("token")
    )

    teams = await teams_service.list_joined_teams("tenant", "user")
    assert [t["id"] for t in teams] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_list_joined_teams_raises_on_graph_error(monkeypatch):
    """A Graph failure must not read to the admin as "you have no teams"."""
    from app.services import teams as teams_service

    async def fake_request(method, path, *, token, **kw):
        return httpx.Response(403, text="Forbidden")

    monkeypatch.setattr(teams_service, "_graph_request", fake_request)
    monkeypatch.setattr(
        teams_service, "_get_token", lambda *a, **k: _immediate("token")
    )

    with pytest.raises(teams_service.TeamsIntegrationError) as exc:
        await teams_service.list_joined_teams("tenant", "user")
    assert "Reconnect" in str(exc.value)


@pytest.mark.asyncio
async def test_list_joined_teams_stops_at_the_page_cap(monkeypatch):
    """A cyclic nextLink must terminate rather than spin forever."""
    from app.services import teams as teams_service

    async def fake_request(method, path, *, token, **kw):
        return httpx.Response(
            200,
            json={
                "value": [{"id": "t", "displayName": "Loop"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/loop",
            },
        )

    monkeypatch.setattr(teams_service, "_graph_request", fake_request)
    monkeypatch.setattr(
        teams_service, "_get_token", lambda *a, **k: _immediate("token")
    )

    teams = await teams_service.list_joined_teams("tenant", "user")
    assert len(teams) == teams_service.GRAPH_PAGE_LIMIT


async def _immediate(value):
    return value


# ── Graph failure messages and card/token plumbing ───────────────────────


def test_graph_failure_message_is_actionable_per_status():
    from app.services import teams as teams_service

    denied = teams_service.graph_failure_message(httpx.Response(403), "your teams")
    assert "Reconnect" in denied
    throttled = teams_service.graph_failure_message(httpx.Response(429), "your teams")
    assert "throttling" in throttled
    other = teams_service.graph_failure_message(httpx.Response(500), "your teams")
    assert "could not load" in other
    assert "did not respond" in teams_service.graph_failure_message(None, "your teams")


@pytest.mark.asyncio
async def test_resolve_token_is_the_public_wrapper(monkeypatch):
    from app.services import teams as teams_service

    monkeypatch.setattr(
        teams_service, "_get_token", lambda *a, **k: _immediate("wrapped-token")
    )
    assert await teams_service.resolve_token("tenant", "user") == "wrapped-token"


@pytest.mark.asyncio
async def test_create_channel_reports_a_name_collision(monkeypatch):
    """Graph answers 409 for a duplicate name; "reconnect" would be wrong advice."""
    from app.services import teams as teams_service

    async def fake_request(method, path, *, token, json_body=None, **kw):
        return httpx.Response(409, text="already exists")

    monkeypatch.setattr(teams_service, "_graph_request", fake_request)
    monkeypatch.setattr(
        teams_service, "_get_token", lambda *a, **k: _immediate("token")
    )

    with pytest.raises(teams_service.TeamsIntegrationError) as exc:
        await teams_service.create_channel("tenant", "team-1", "General")
    assert "already exists" in str(exc.value)


@pytest.mark.asyncio
async def test_create_channel_requires_a_name():
    from app.services import teams as teams_service

    with pytest.raises(teams_service.TeamsIntegrationError):
        await teams_service.create_channel("tenant", "team-1", "   ")


@pytest.mark.asyncio
async def test_list_channels_pages_and_maps(monkeypatch):
    from app.services import teams as teams_service

    async def fake_request(method, path, *, token, **kw):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "c1",
                        "displayName": "General",
                        "membershipType": "standard",
                    },
                    {"displayName": "no id — dropped"},
                ]
            },
        )

    monkeypatch.setattr(teams_service, "_graph_request", fake_request)
    monkeypatch.setattr(
        teams_service, "_get_token", lambda *a, **k: _immediate("token")
    )

    channels = await teams_service.list_channels("tenant", "team-1")
    assert channels == [
        {"id": "c1", "display_name": "General", "membership_type": "standard"}
    ]


@pytest.mark.asyncio
async def test_send_channel_message_reuses_a_supplied_token(monkeypatch):
    from app.services import teams as teams_service

    seen = {}

    async def fake_request(method, path, *, token, json_body=None, **kw):
        seen["token"] = token
        seen["body"] = json_body
        return httpx.Response(201, json={})

    async def unexpected(*a, **k):
        raise AssertionError("a supplied token must not be re-resolved")

    monkeypatch.setattr(teams_service, "_graph_request", fake_request)
    monkeypatch.setattr(teams_service, "_get_token", unexpected)

    ok = await teams_service.send_channel_message(
        "tenant", "team-1", "chan-1", html="<p>hi</p>", token="supplied"
    )
    assert ok is True
    assert seen["token"] == "supplied"
    assert seen["body"]["body"]["content"] == "<p>hi</p>"


def test_card_actions_place_the_deep_link_first():
    from app.services.teams import build_matter_card

    card = build_matter_card(
        "Title",
        "Matter X",
        {},
        deep_link="https://app/matter",
        actions=[{"type": "Action.OpenUrl", "title": "Other", "url": "https://other"}],
    )
    assert [a["title"] for a in card["actions"]] == ["Open in LawHand", "Other"]
    # No fields means no FactSet block rather than an empty one.
    assert all(b["type"] != "FactSet" for b in card["body"])


# ── Notification dispatch guards ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_refuses_an_unknown_event_type(test_tenant):
    from app.services import teams_notify

    # A caller emitting an unrouted event is a wiring bug: no routing row can
    # ever match it, so it must not be treated as a tenant problem.
    sent = await teams_notify.notify(
        str(test_tenant.id), "not_a_real_event", title="x", fields={}
    )
    assert sent == 0


@pytest.mark.asyncio
async def test_notify_gives_up_when_no_token_resolves(
    ms_connected, db_session, test_tenant, test_user, monkeypatch
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

    async def no_token(tenant_id, user_id=None):
        raise teams_service.TeamsIntegrationError("no usable token")

    async def unexpected(*a, **k):
        raise AssertionError("must not attempt a post without a token")

    monkeypatch.setattr(teams_service, "resolve_token", no_token)
    monkeypatch.setattr(teams_service, "send_channel_message", unexpected)

    sent = await teams_notify.notify(
        str(test_tenant.id),
        "deadline_approaching",
        title="x",
        fields={},
        matter_id=str(matter.id),
    )
    assert sent == 0


@pytest.mark.asyncio
async def test_resolve_targets_prefers_matter_specific_routes(
    db_session, test_tenant, test_user
):
    from app.services import teams_notify
    from app.models.teams_notification_setting import TeamsNotificationSetting

    matter = await _add_matter(db_session, test_tenant.id, test_user.id)
    db_session.add_all(
        [
            TeamsNotificationSetting(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                event_type="deadline_approaching",
                team_id="team-default",
                channel_id="chan-default",
                matter_id=None,
                is_enabled=True,
            ),
            TeamsNotificationSetting(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                event_type="deadline_approaching",
                team_id="team-specific",
                channel_id="chan-specific",
                matter_id=matter.id,
                is_enabled=True,
            ),
        ]
    )
    await db_session.commit()

    targets = await teams_notify.resolve_targets(
        str(test_tenant.id), "deadline_approaching", str(matter.id)
    )
    # The matter override wins outright — the firm-wide default must not also fire.
    assert targets == [("team-specific", "chan-specific")]

    default_targets = await teams_notify.resolve_targets(
        str(test_tenant.id), "deadline_approaching", None
    )
    assert default_targets == [("team-default", "chan-default")]


def test_event_catalogue_is_ordered_and_complete():
    from app.services import teams_notify

    catalogue = teams_notify.event_type_catalogue()
    assert [e["event_type"] for e in catalogue] == list(teams_notify.TEAMS_EVENT_TYPES)
    assert teams_notify.is_known_event_type("deadline_approaching")
    assert not teams_notify.is_known_event_type("nope")


def test_channel_create_request_rejects_a_blank_name():
    import pydantic

    from app.schemas.teams import ChannelCreateRequest

    with pytest.raises(pydantic.ValidationError):
        ChannelCreateRequest(team_id="team-1", display_name="   ")
    # A padded but real name is accepted and trimmed.
    assert (
        ChannelCreateRequest(team_id=" team-1 ", display_name=" General ").display_name
        == "General"
    )


def test_intake_feed_labels_both_capture_providers():
    from app.routers.intake_dashboard import _log_source
    from app.models.communication_log import CommunicationLog

    def log(**kw):
        return CommunicationLog(subject="s", **kw)

    assert _log_source(log(external_ref="teams_voice:call:1")) == "teams_voice"
    assert _log_source(log(participants={"provider": "teams_voice"})) == "teams_voice"
    assert _log_source(log(external_ref="zoom_phone:call:1")) == "zoom_phone"
    assert _log_source(log(participants={"provider": "zoom_phone"})) == "zoom_phone"
    assert _log_source(log(external_ref=None, participants=None)) == "manual"
