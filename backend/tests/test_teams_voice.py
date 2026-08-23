"""Tests for Microsoft Teams Phone (voice) capture.

Covers ISO duration parsing, Entra directory validation, normalization of both
Graph feeds (raw call records and the PSTN usage report), idempotent import
with staff-curation preservation, change-notification job extraction and
clientState verification, and the admin/webhook endpoints.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.communication_log import CommunicationLog
from app.models.tenant_credential import TenantCredential
from app.models.teams_voice_setting import TeamsVoiceSetting
from app.services import teams_voice
from app.services.teams import TEAMS_REQUIRED_SCOPES
from app.services.token_vault import encrypt_token


@pytest_asyncio.fixture
async def ms_connected(db_session, test_tenant):
    """Tenant with a fully-scoped active Microsoft credential."""
    cred = TenantCredential(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        provider="microsoft",
        encrypted_access_token="placeholder",
        scopes=f"offline_access User.Read.All {TEAMS_REQUIRED_SCOPES}",
        is_active=True,
    )
    db_session.add(cred)
    await db_session.commit()
    return cred


@pytest_asyncio.fixture
async def voice_enabled(db_session, test_tenant):
    row = TeamsVoiceSetting(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        entra_tenant_id="contoso-directory-guid",
        is_enabled=True,
        encrypted_client_state=encrypt_token("secret-client-state"),
        subscription_id="sub-1",
        subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        notification_url="https://app/api/integrations/teams/voice/webhook/x",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


def _pstn_row(**overrides):
    record = {
        "id": "pstn-1",
        "callId": "call-1",
        "callType": "ByotIn",
        "callerNumber": "+15125550143",
        "calleeNumber": "+15125559000",
        "userDisplayName": "Front Desk",
        "startDateTime": "2026-08-01T15:04:05Z",
        "endDateTime": "2026-08-01T15:06:18Z",
        "duration": 133,
    }
    record.update(overrides)
    return record


def _call_record(**overrides):
    record = {
        "id": "record-1",
        "type": "peerToPeer",
        "startDateTime": "2026-08-01T15:04:05Z",
        "endDateTime": "2026-08-01T15:06:18Z",
        "joinWebUrl": None,
        "sessions": [
            {
                "caller": {
                    "identity": {
                        "phone": {"id": "+15125550143"},
                    }
                },
                "callee": {
                    "identity": {
                        "user": {"displayName": "Front Desk", "id": "user-1"},
                    }
                },
            }
        ],
    }
    record.update(overrides)
    return record


# ── Pure helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("PT2M13S", 133),
            ("PT1H0M5S", 3605),
            ("PT45S", 45),
            (90, 90),
            ("90", 90),
            ("", None),
            (None, None),
            ("not-a-duration", None),
            ("P1D", None),
        ],
    )
    def test_parse_iso_duration_seconds(self, value, expected):
        assert teams_voice.parse_iso_duration_seconds(value) == expected

    @pytest.mark.parametrize(
        "value", ["common", "organizations", "consumers", "", "  "]
    )
    def test_multi_tenant_endpoints_are_rejected(self, value):
        # These cannot issue an app-only token, so accepting one would save a
        # configuration that can never authenticate.
        assert teams_voice.valid_entra_tenant_id(value) is None

    def test_directory_id_is_normalized(self):
        assert (
            teams_voice.valid_entra_tenant_id("  Contoso.OnMicrosoft.com  ")
            == "contoso.onmicrosoft.com"
        )

    def test_directory_id_rejects_injection_characters(self):
        assert teams_voice.valid_entra_tenant_id("abc/../../evil") is None

    def test_verify_client_state(self):
        assert teams_voice.verify_client_state("abc", "abc") is True
        assert teams_voice.verify_client_state("abc", "abd") is False
        assert teams_voice.verify_client_state(None, "abc") is False
        assert teams_voice.verify_client_state("abc", None) is False


# ── Normalization ─────────────────────────────────────────────────────────


class TestNormalize:
    def test_pstn_inbound_row(self):
        normalized = teams_voice.normalize_teams_voice_record(_pstn_row())
        assert normalized["external_ref"] == "teams_voice:pstn:pstn-1"
        assert normalized["direction"] == "inbound"
        participants = normalized["participants"]
        assert participants["provider"] == "teams_voice"
        assert participants["caller_number"] == "+15125550143"
        assert participants["duration_seconds"] == 133
        assert participants["normalized_phone"]

    def test_outbound_is_skipped(self):
        assert (
            teams_voice.normalize_teams_voice_record(_pstn_row(callType="UserOut"))
            is None
        )

    def test_record_without_id_is_skipped(self):
        assert teams_voice.normalize_teams_voice_record({"callType": "ByotIn"}) is None

    def test_call_record_direction_inferred_from_endpoints(self):
        # A raw callRecord carries no callType; a PSTN caller into a Teams user
        # is inbound.
        normalized = teams_voice.normalize_teams_voice_record(_call_record())
        assert normalized is not None
        assert normalized["direction"] == "inbound"
        assert normalized["participants"]["caller_number"] == "+15125550143"
        assert normalized["participants"]["callee_name"] == "Front Desk"
        # Duration falls back to the start/end span when Graph omits it.
        assert normalized["participants"]["duration_seconds"] == 133

    def test_internal_teams_call_is_skipped(self):
        record = _call_record(
            sessions=[
                {
                    "caller": {"identity": {"user": {"displayName": "Alice"}}},
                    "callee": {"identity": {"user": {"displayName": "Bob"}}},
                }
            ]
        )
        # No PSTN leg on either side — not an intake call.
        assert teams_voice.normalize_teams_voice_record(record) is None

    def test_canonical_id_overrides_record_id(self):
        normalized = teams_voice.normalize_teams_voice_record(
            _pstn_row(canonical_call_id="webhook-id")
        )
        assert normalized["external_ref"] == "teams_voice:pstn:webhook-id"


# ── Change notifications ──────────────────────────────────────────────────


class TestWebhookJobs:
    def test_extracts_one_job_per_record(self):
        body = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "clientState": "secret",
                    "resourceData": {"id": "record-1"},
                },
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "clientState": "secret",
                    "resourceData": {"id": "record-2"},
                },
            ]
        }
        jobs = teams_voice.teams_voice_webhook_jobs(body, subscription_id="sub-1")
        assert [j.payload["call_record_id"] for j in jobs] == ["record-1", "record-2"]
        assert len({j.idempotency_key for j in jobs}) == 2

    def test_duplicate_records_collapse(self):
        item = {
            "subscriptionId": "sub-1",
            "changeType": "created",
            "resourceData": {"id": "record-1"},
        }
        jobs = teams_voice.teams_voice_webhook_jobs(
            {"value": [item, dict(item)]}, subscription_id="sub-1"
        )
        assert len(jobs) == 1

    def test_other_subscriptions_are_dropped(self):
        jobs = teams_voice.teams_voice_webhook_jobs(
            {
                "value": [
                    {
                        "subscriptionId": "someone-elses",
                        "changeType": "created",
                        "resourceData": {"id": "record-1"},
                    }
                ]
            },
            subscription_id="sub-1",
        )
        assert jobs == []

    def test_resource_path_fallback(self):
        jobs = teams_voice.teams_voice_webhook_jobs(
            {
                "value": [
                    {
                        "changeType": "created",
                        "resource": "communications/callRecords('record-9')",
                    }
                ]
            }
        )
        assert jobs[0].payload["call_record_id"] == "record-9"

    def test_malformed_body_yields_nothing(self):
        assert teams_voice.teams_voice_webhook_jobs({}) == []
        assert teams_voice.teams_voice_webhook_jobs({"value": "nope"}) == []
        assert teams_voice.teams_voice_webhook_jobs({"value": [{}]}) == []


# ── Import ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestImport:
    async def test_import_is_idempotent(self, db_session, test_tenant):
        first = await teams_voice.import_teams_voice_records(
            db_session, tenant_id=str(test_tenant.id), records=[_pstn_row()]
        )
        await db_session.commit()
        assert first.imported == 1
        assert len(first.captured) == 1

        second = await teams_voice.import_teams_voice_records(
            db_session, tenant_id=str(test_tenant.id), records=[_pstn_row()]
        )
        await db_session.commit()
        assert second.imported == 0
        assert second.skipped == 1
        assert second.captured == []

        rows = (
            (
                await db_session.execute(
                    select(CommunicationLog).where(
                        CommunicationLog.tenant_id == test_tenant.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_reconciliation_preserves_staff_curation(
        self, db_session, test_tenant, test_user
    ):
        await teams_voice.import_teams_voice_records(
            db_session, tenant_id=str(test_tenant.id), records=[_pstn_row()]
        )
        await db_session.commit()

        row = await db_session.scalar(
            select(CommunicationLog).where(
                CommunicationLog.external_ref == "teams_voice:pstn:pstn-1"
            )
        )
        # Intake staff worked the call: corrected the caller and owned the row.
        row.created_by_user_id = test_user.id
        row.subject = "Intake: Jane Doe re: custody"
        row.participants = {**(row.participants or {}), "caller_name": "Jane Doe"}
        await db_session.commit()

        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(duration=210, result="Completed")],
        )
        await db_session.commit()
        await db_session.refresh(row)

        # Provider metadata refreshes; the curated identity and subject do not.
        assert row.subject == "Intake: Jane Doe re: custody"
        assert row.participants["caller_name"] == "Jane Doe"
        assert row.participants["duration_seconds"] == 210
        assert row.participants["result"] == "Completed"

    async def test_uncurated_row_is_refreshed(self, db_session, test_tenant):
        await teams_voice.import_teams_voice_records(
            db_session, tenant_id=str(test_tenant.id), records=[_pstn_row()]
        )
        await db_session.commit()

        result = await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(duration=210)],
        )
        await db_session.commit()
        assert result.updated == 1

        row = await db_session.scalar(
            select(CommunicationLog).where(
                CommunicationLog.external_ref == "teams_voice:pstn:pstn-1"
            )
        )
        assert row.participants["duration_seconds"] == 210

    async def test_outbound_records_are_skipped(self, db_session, test_tenant):
        result = await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(callType="UserOut")],
        )
        assert result.imported == 0
        assert result.skipped == 1


# ── Settings ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSettings:
    async def test_upsert_generates_client_state(self, db_session, test_tenant):
        row = await teams_voice.upsert_voice_settings(
            db_session,
            tenant_id=str(test_tenant.id),
            entra_tenant_id="contoso-guid-1234",
            is_enabled=True,
        )
        await db_session.commit()
        assert row.encrypted_client_state
        assert teams_voice.client_state_of(row)

    async def test_upsert_rejects_common(self, db_session, test_tenant):
        with pytest.raises(teams_voice.TeamsVoiceError):
            await teams_voice.upsert_voice_settings(
                db_session, tenant_id=str(test_tenant.id), entra_tenant_id="common"
            )

    async def test_changing_directory_clears_subscription(
        self, db_session, test_tenant, voice_enabled
    ):
        # The old subscription lived in the old directory; keeping its id would
        # make renewal target a subscription we no longer own.
        row = await teams_voice.upsert_voice_settings(
            db_session,
            tenant_id=str(test_tenant.id),
            entra_tenant_id="fabrikam-guid-5678",
        )
        await db_session.commit()
        assert row.subscription_id is None
        assert row.subscription_expires_at is None

    async def test_teardown_reaches_microsoft_after_disabling(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        """Disabling voice must remove the subscription, not just forget it.

        Token acquisition normally insists the tenant is enabled; teardown runs
        after the switch is already off, so it must be exempt — otherwise Graph
        keeps posting notifications at an endpoint that now drops them.
        """
        voice_enabled.is_enabled = False
        await db_session.commit()

        deleted = {}

        async def fake_token(db, *, tenant_id, require_enabled=True):
            assert require_enabled is False
            return "app-token"

        async def fake_request(method, path, *, token, **kw):
            deleted["method"] = method
            deleted["path"] = path
            return httpx.Response(204)

        monkeypatch.setattr(teams_voice, "get_app_only_token", fake_token)
        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)

        assert (
            await teams_voice.delete_subscription(
                db_session, tenant_id=str(test_tenant.id)
            )
            is True
        )
        assert deleted == {"method": "DELETE", "path": "/subscriptions/sub-1"}
        await db_session.refresh(voice_enabled)
        assert voice_enabled.subscription_id is None

    async def test_subscription_needs_renewal(self, voice_enabled):
        assert teams_voice.subscription_needs_renewal(voice_enabled) is False

        voice_enabled.subscription_expires_at = datetime.now(timezone.utc) + timedelta(
            hours=2
        )
        assert teams_voice.subscription_needs_renewal(voice_enabled) is True

        voice_enabled.subscription_id = None
        assert teams_voice.subscription_needs_renewal(voice_enabled) is True

        voice_enabled.is_enabled = False
        assert teams_voice.subscription_needs_renewal(voice_enabled) is False


# ── Endpoints ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestVoiceEndpoints:
    async def test_status_requires_teams(self, client):
        resp = await client.get("/api/integrations/teams/voice/status")
        assert resp.status_code == 409

    async def test_status_reports_unconfigured(self, client, ms_connected):
        resp = await client.get("/api/integrations/teams/voice/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["enabled"] is False
        assert body["required_application_permission"] == "CallRecords.Read.All"
        assert "/api/integrations/teams/voice/webhook/" in body["webhook_url"]

    async def test_enable_requires_a_directory_id(self, client, ms_connected):
        resp = await client.put(
            "/api/integrations/teams/voice/settings", json={"is_enabled": True}
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "teams_voice_missing_directory"

    async def test_configure_then_enable(self, client, ms_connected):
        resp = await client.put(
            "/api/integrations/teams/voice/settings",
            json={"entra_tenant_id": "contoso-guid-1234", "is_enabled": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["enabled"] is True
        assert body["entra_tenant_id"] == "contoso-guid-1234"
        assert body["admin_consent_url"].startswith(
            "https://login.microsoftonline.com/contoso-guid-1234/adminconsent"
        )

    async def test_rejects_common_directory(self, client, ms_connected):
        resp = await client.put(
            "/api/integrations/teams/voice/settings",
            json={"entra_tenant_id": "common"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "teams_voice_invalid_directory"


@pytest.mark.asyncio
class TestVoiceWebhook:
    async def test_validation_token_is_echoed(self, client, test_tenant):
        resp = await client.post(
            f"/api/integrations/teams/voice/webhook/{test_tenant.id}"
            "?validationToken=abc123"
        )
        assert resp.status_code == 200
        assert resp.text == "abc123"

    async def test_bad_client_state_is_rejected(
        self, client, test_tenant, voice_enabled
    ):
        resp = await client.post(
            f"/api/integrations/teams/voice/webhook/{test_tenant.id}",
            json={
                "value": [
                    {
                        "subscriptionId": "sub-1",
                        "changeType": "created",
                        "clientState": "wrong",
                        "resourceData": {"id": "record-1"},
                    }
                ]
            },
        )
        assert resp.status_code == 401

    async def test_valid_notification_enqueues_work(
        self, client, db_session, test_tenant, voice_enabled
    ):
        from app.models.durable_job import DurableJob

        resp = await client.post(
            f"/api/integrations/teams/voice/webhook/{test_tenant.id}",
            json={
                "value": [
                    {
                        "subscriptionId": "sub-1",
                        "changeType": "created",
                        "clientState": "secret-client-state",
                        "resourceData": {"id": "record-1"},
                    }
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "accepted", "queued": 1}

        jobs = (
            (
                await db_session.execute(
                    select(DurableJob).where(
                        DurableJob.tenant_id == test_tenant.id,
                        DurableJob.kind == "teams_voice_call_import",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].payload["call_record_id"] == "record-1"

    async def test_disabled_tenant_is_accepted_and_dropped(
        self, client, db_session, test_tenant, voice_enabled
    ):
        # Replying non-2xx would make Graph retry and eventually drop the
        # subscription for a tenant that switched the feature off on purpose.
        voice_enabled.is_enabled = False
        await db_session.commit()

        resp = await client.post(
            f"/api/integrations/teams/voice/webhook/{test_tenant.id}",
            json={
                "value": [
                    {
                        "subscriptionId": "sub-1",
                        "changeType": "created",
                        "clientState": "secret-client-state",
                        "resourceData": {"id": "record-1"},
                    }
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"


# ── App-only credential and token acquisition ─────────────────────────────


@pytest.mark.asyncio
class TestAppOnlyToken:
    async def test_prefers_a_tenant_owned_application(
        self, db_session, test_tenant, voice_enabled
    ):
        from app.models.tenant_oauth_app import TenantOAuthApp

        db_session.add(
            TenantOAuthApp(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                provider="teams_voice",
                encrypted_client_id=encrypt_token("firm-client-id"),
                encrypted_client_secret=encrypt_token("firm-secret"),
                is_active=True,
            )
        )
        await db_session.commit()

        credentials = await teams_voice.get_voice_app_credentials(
            db_session, tenant_id=str(test_tenant.id)
        )
        assert credentials.source == "tenant"
        assert credentials.client_id == "firm-client-id"

    async def test_falls_back_to_the_platform_application(
        self, db_session, test_tenant
    ):
        credentials = await teams_voice.get_voice_app_credentials(
            db_session, tenant_id=str(test_tenant.id)
        )
        assert credentials.source == "platform"

    async def test_requires_an_enabled_tenant(self, db_session, test_tenant):
        with pytest.raises(teams_voice.TeamsVoiceNotConfigured):
            await teams_voice.get_app_only_token(
                db_session, tenant_id=str(test_tenant.id)
            )

    async def test_requires_a_directory_id(
        self, db_session, test_tenant, voice_enabled
    ):
        voice_enabled.entra_tenant_id = None
        await db_session.commit()
        with pytest.raises(teams_voice.TeamsVoiceNotConfigured):
            await teams_voice.get_app_only_token(
                db_session, tenant_id=str(test_tenant.id)
            )

    async def test_returns_the_access_token(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        captured = {}

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None):
                captured["url"] = url
                captured["data"] = data
                return httpx.Response(200, json={"access_token": "app-token"})

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        token = await teams_voice.get_app_only_token(
            db_session, tenant_id=str(test_tenant.id)
        )
        assert token == "app-token"
        # The directory GUID, not a multi-tenant alias, must be in the URL.
        assert "contoso-directory-guid" in captured["url"]
        assert captured["data"]["grant_type"] == "client_credentials"
        assert captured["data"]["scope"] == teams_voice.TEAMS_VOICE_GRAPH_SCOPE

    @pytest.mark.parametrize(
        "detail,expected",
        [
            ("AADSTS7000215: Invalid client secret", "client secret"),
            ("AADSTS700016: Application not found", "directory"),
        ],
    )
    async def test_maps_entra_errors_to_actionable_text(
        self, db_session, test_tenant, voice_enabled, monkeypatch, detail, expected
    ):
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_client(httpx.Response(401, text=detail))
        )
        with pytest.raises(teams_voice.TeamsVoicePermanentError) as exc:
            await teams_voice.get_app_only_token(
                db_session, tenant_id=str(test_tenant.id)
            )
        assert expected in str(exc.value)

    async def test_unmapped_failure_is_retryable(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_client(httpx.Response(503, text="busy"))
        )
        with pytest.raises(teams_voice.TeamsVoiceError) as exc:
            await teams_voice.get_app_only_token(
                db_session, tenant_id=str(test_tenant.id)
            )
        assert not isinstance(exc.value, teams_voice.TeamsVoicePermanentError)

    async def test_missing_access_token_is_an_error(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_client(httpx.Response(200, json={}))
        )
        with pytest.raises(teams_voice.TeamsVoiceError):
            await teams_voice.get_app_only_token(
                db_session, tenant_id=str(test_tenant.id)
            )

    async def test_network_failure_is_reported(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_client(httpx.ConnectError("no route"))
        )
        with pytest.raises(teams_voice.TeamsVoiceError) as exc:
            await teams_voice.get_app_only_token(
                db_session, tenant_id=str(test_tenant.id)
            )
        assert "Entra" in str(exc.value)


def _fake_client(result):
    """An httpx.AsyncClient stand-in returning (or raising) one result."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def _respond(self, *a, **kw):
            if isinstance(result, Exception):
                raise result
            return result

        post = _respond
        request = _respond

    return _Client


# ── Graph request plumbing ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGraphPlumbing:
    async def test_retries_on_throttling_then_succeeds(self, monkeypatch):
        calls = {"n": 0}
        sleeps = []

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def request(self, method, url, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    return httpx.Response(429, headers={"Retry-After": "0.01"})
                return httpx.Response(200, json={"ok": True})

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        monkeypatch.setattr(teams_voice.asyncio, "sleep", fake_sleep)

        resp = await teams_voice._graph_request("GET", "/x", token="t")
        assert resp.status_code == 200
        assert calls["n"] == 2
        assert sleeps == [0.01]

    async def test_gives_up_after_the_retry_budget(self, monkeypatch):
        async def fake_sleep(delay):
            return None

        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_client(httpx.Response(429, text="slow down"))
        )
        monkeypatch.setattr(teams_voice.asyncio, "sleep", fake_sleep)

        resp = await teams_voice._graph_request("GET", "/x", token="t", max_retries=1)
        assert resp.status_code == 429

    async def test_network_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "AsyncClient", _fake_client(httpx.ConnectError("down"))
        )
        assert await teams_voice._graph_request("GET", "/x", token="t") is None

    async def test_collect_follows_next_link(self, monkeypatch):
        pages = {
            "/first": {
                "value": [{"id": "a"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/second",
            },
            "https://graph.microsoft.com/v1.0/second": {"value": [{"id": "b"}]},
        }

        async def fake_request(method, path, *, token, **kw):
            return httpx.Response(200, json=pages[path])

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        items = await teams_voice._graph_collect("/first", token="t", context="x")
        assert [i["id"] for i in items] == ["a", "b"]

    async def test_collect_raises_on_failure(self, monkeypatch):
        async def fake_request(method, path, *, token, **kw):
            return httpx.Response(403, text="nope")

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        with pytest.raises(teams_voice.TeamsVoicePermanentError):
            await teams_voice._graph_collect("/first", token="t", context="x")

    async def test_collect_stops_at_the_page_cap(self, monkeypatch):
        async def fake_request(method, path, *, token, **kw):
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "loop"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/loop",
                },
            )

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        items = await teams_voice._graph_collect("/first", token="t", context="x")
        assert len(items) == teams_voice._PAGE_LIMIT


# ── Graph reads ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGraphReads:
    @pytest_asyncio.fixture(autouse=True)
    async def _token(self, monkeypatch):
        async def fake_token(db, *, tenant_id, require_enabled=True):
            return "app-token"

        monkeypatch.setattr(teams_voice, "get_app_only_token", fake_token)

    async def test_pstn_window_is_bounded_and_formatted(
        self, db_session, test_tenant, monkeypatch
    ):
        seen = {}

        async def fake_collect(path, *, token, context):
            seen["path"] = path
            return [_pstn_row()]

        monkeypatch.setattr(teams_voice, "_graph_collect", fake_collect)
        records = await teams_voice.fetch_pstn_calls(
            db_session, tenant_id=str(test_tenant.id), days=500
        )
        assert len(records) == 1
        assert "getPstnCalls(fromDateTime=" in seen["path"]
        # Graph caps the report span at 90 days; a larger ask is clamped rather
        # than sent through and rejected.
        assert "toDateTime=" in seen["path"]

    async def test_fetch_call_record_expands_sessions(
        self, db_session, test_tenant, monkeypatch
    ):
        seen = {}

        async def fake_request(method, path, *, token, **kw):
            seen["path"] = path
            return httpx.Response(200, json=_call_record())

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        record = await teams_voice.fetch_call_record(
            db_session, tenant_id=str(test_tenant.id), call_record_id="record-1"
        )
        assert record["id"] == "record-1"
        assert "$expand=sessions" in seen["path"]

    async def test_fetch_call_record_rejects_a_non_object(
        self, db_session, test_tenant, monkeypatch
    ):
        async def fake_request(method, path, *, token, **kw):
            return httpx.Response(200, json=["not", "a", "record"])

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        with pytest.raises(teams_voice.TeamsVoicePermanentError):
            await teams_voice.fetch_call_record(
                db_session, tenant_id=str(test_tenant.id), call_record_id="record-1"
            )

    async def test_probe_counts_inbound_calls(
        self, db_session, test_tenant, monkeypatch
    ):
        async def fake_collect(path, *, token, context):
            return [_pstn_row(), _pstn_row(id="p2", callType="UserOut")]

        monkeypatch.setattr(teams_voice, "_graph_collect", fake_collect)
        result = await teams_voice.probe_voice_connection(
            db_session, tenant_id=str(test_tenant.id)
        )
        assert result == {"status": "ok", "sample_count": 2, "inbound_count": 1}


# ── Subscription lifecycle ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubscription:
    @pytest_asyncio.fixture(autouse=True)
    async def _token(self, monkeypatch):
        async def fake_token(db, *, tenant_id, require_enabled=True):
            return "app-token"

        monkeypatch.setattr(teams_voice, "get_app_only_token", fake_token)

    async def test_requires_an_enabled_tenant(self, db_session, test_tenant):
        with pytest.raises(teams_voice.TeamsVoiceNotConfigured):
            await teams_voice.ensure_subscription(
                db_session, tenant_id=str(test_tenant.id), notification_url="https://x"
            )

    async def test_renews_an_existing_subscription(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        calls = []

        async def fake_request(method, path, *, token, json_body=None, **kw):
            calls.append((method, path))
            return httpx.Response(
                200, json={"expirationDateTime": "2026-09-01T00:00:00Z"}
            )

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        row = await teams_voice.ensure_subscription(
            db_session,
            tenant_id=str(test_tenant.id),
            notification_url=voice_enabled.notification_url,
        )
        await db_session.commit()
        assert calls == [("PATCH", "/subscriptions/sub-1")]
        assert row.subscription_id == "sub-1"

    async def test_recreates_when_renewal_is_refused(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        calls = []

        async def fake_request(method, path, *, token, json_body=None, **kw):
            calls.append((method, path))
            if method == "PATCH":
                # Graph no longer knows this subscription.
                return httpx.Response(404, text="not found")
            return httpx.Response(
                201,
                json={"id": "sub-2", "expirationDateTime": "2026-09-01T00:00:00Z"},
            )

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        row = await teams_voice.ensure_subscription(
            db_session,
            tenant_id=str(test_tenant.id),
            notification_url=voice_enabled.notification_url,
        )
        await db_session.commit()
        assert [c[0] for c in calls] == ["PATCH", "POST"]
        assert row.subscription_id == "sub-2"

    async def test_creates_a_first_subscription_with_client_state(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        voice_enabled.subscription_id = None
        await db_session.commit()
        bodies = []

        async def fake_request(method, path, *, token, json_body=None, **kw):
            bodies.append(json_body)
            return httpx.Response(
                201,
                json={"id": "sub-new", "expirationDateTime": "2026-09-01T00:00:00Z"},
            )

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        row = await teams_voice.ensure_subscription(
            db_session,
            tenant_id=str(test_tenant.id),
            notification_url="https://app/webhook",
        )
        await db_session.commit()
        assert row.subscription_id == "sub-new"
        body = bodies[0]
        assert body["resource"] == "communications/callRecords"
        assert body["changeType"] == "created"
        assert body["notificationUrl"] == "https://app/webhook"
        # The secret every later notification is checked against.
        assert body["clientState"] == "secret-client-state"

    async def test_creation_failure_raises(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        voice_enabled.subscription_id = None
        await db_session.commit()

        async def fake_request(method, path, *, token, json_body=None, **kw):
            return httpx.Response(403, text="denied")

        monkeypatch.setattr(teams_voice, "_graph_request", fake_request)
        with pytest.raises(teams_voice.TeamsVoicePermanentError):
            await teams_voice.ensure_subscription(
                db_session,
                tenant_id=str(test_tenant.id),
                notification_url="https://app/webhook",
            )

    async def test_delete_without_a_subscription_is_a_noop(
        self, db_session, test_tenant
    ):
        assert (
            await teams_voice.delete_subscription(
                db_session, tenant_id=str(test_tenant.id)
            )
            is False
        )


# ── Durable webhook job and reconciliation ────────────────────────────────


@pytest.mark.asyncio
class TestWebhookJobImport:
    async def test_imports_the_authoritative_record(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        async def fake_fetch(db, *, tenant_id, call_record_id):
            return _call_record(id=call_record_id)

        monkeypatch.setattr(teams_voice, "fetch_call_record", fake_fetch)
        result = await teams_voice.import_teams_voice_webhook_job(
            db_session,
            tenant_id=str(test_tenant.id),
            payload={"call_record_id": "record-1", "change_type": "created"},
        )
        await db_session.commit()
        assert result.imported == 1

        row = await db_session.scalar(
            select(CommunicationLog).where(
                CommunicationLog.external_ref == "teams_voice:call:record-1"
            )
        )
        assert row.participants["webhook_change_type"] == "created"
        assert row.participants["capture_source"] == "notification"

    async def test_missing_id_is_rejected(self, db_session, test_tenant):
        with pytest.raises(ValueError):
            await teams_voice.import_teams_voice_webhook_job(
                db_session, tenant_id=str(test_tenant.id), payload={}
            )

    async def test_a_different_record_is_refused(
        self, db_session, test_tenant, monkeypatch
    ):
        async def fake_fetch(db, *, tenant_id, call_record_id):
            # Graph answered with someone else's call.
            return _call_record(id="a-different-record")

        monkeypatch.setattr(teams_voice, "fetch_call_record", fake_fetch)
        with pytest.raises(teams_voice.TeamsVoicePermanentError):
            await teams_voice.import_teams_voice_webhook_job(
                db_session,
                tenant_id=str(test_tenant.id),
                payload={"call_record_id": "record-1"},
            )

    async def test_sync_records_its_outcome_on_the_settings_row(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        async def fake_fetch(db, *, tenant_id, days):
            return [_pstn_row()]

        monkeypatch.setattr(teams_voice, "fetch_pstn_calls", fake_fetch)
        result = await teams_voice.sync_teams_voice_call_history(
            db_session, tenant_id=str(test_tenant.id), days=3
        )
        await db_session.commit()
        assert result.imported == 1
        await db_session.refresh(voice_enabled)
        assert voice_enabled.last_sync_status == "ok"
        assert voice_enabled.last_sync_at is not None


# ── Small helpers and edge cases ──────────────────────────────────────────


class TestNormalizeEdges:
    def test_datetime_passthrough_and_fallbacks(self):
        aware = datetime(2026, 8, 1, tzinfo=timezone.utc)
        assert teams_voice._parse_datetime(aware) is aware
        naive = datetime(2026, 8, 1)
        assert teams_voice._parse_datetime(naive).tzinfo == timezone.utc
        # An unparseable timestamp must not lose the call; it lands at "now".
        assert teams_voice._parse_datetime("not-a-date").tzinfo == timezone.utc
        assert teams_voice._parse_optional_datetime(None) is None
        assert teams_voice._parse_optional_datetime("2026-08-01T00:00:00Z") is not None

    def test_endpoint_identity_handles_junk(self):
        assert teams_voice._endpoint_identity(None) == (None, None)
        assert teams_voice._endpoint_identity({"identity": "not-a-dict"}) == (
            None,
            None,
        )
        number, name = teams_voice._endpoint_identity(
            {"identity": {"phone": {"id": "+15125550143"}}, "name": "Reception"}
        )
        assert number == "+15125550143"
        assert name == "Reception"

    def test_parties_fall_back_to_organizer_and_participants(self):
        record = {
            "id": "r",
            "organizer": {"identity": {"user": {"displayName": "Организатор"}}},
            "participants": [
                {"identity": {"phone": {"id": "+15125559000"}}},
            ],
        }
        caller, callee = teams_voice._call_record_parties(record)
        assert caller["name"] == "Организатор"
        assert callee["number"] == "+15125559000"

    def test_parties_ignore_malformed_sessions(self):
        caller, callee = teams_voice._call_record_parties(
            {"id": "r", "sessions": ["not-a-dict"]}
        )
        assert caller == {"number": None, "name": None}

    def test_unclassifiable_direction_is_left_alone(self):
        assert teams_voice._pstn_direction({"callType": "Sideways"}) is None
        assert teams_voice._pstn_direction({}) is None

    def test_stringify_and_first(self):
        assert teams_voice._stringify(None) is None
        assert teams_voice._stringify("") is None
        assert teams_voice._stringify(7) == "7"
        assert teams_voice._first({"a": "", "b": "x"}, "a", "b") == "x"
        assert teams_voice._first({}, "a") is None


@pytest.mark.asyncio
async def test_client_state_survives_an_undecryptable_secret(
    db_session, test_tenant, voice_enabled
):
    # A rotated encryption key must degrade to "reject notifications", not to a
    # crash inside the webhook handler.
    voice_enabled.encrypted_client_state = "not-a-valid-ciphertext"
    await db_session.commit()
    assert teams_voice.client_state_of(voice_enabled) is None


@pytest.mark.asyncio
async def test_missing_client_state_is_regenerated_on_subscribe(
    db_session, test_tenant, voice_enabled, monkeypatch
):
    voice_enabled.encrypted_client_state = None
    voice_enabled.subscription_id = None
    await db_session.commit()

    async def fake_token(db, *, tenant_id, require_enabled=True):
        return "app-token"

    bodies = []

    async def fake_request(method, path, *, token, json_body=None, **kw):
        bodies.append(json_body)
        return httpx.Response(
            201, json={"id": "sub-x", "expirationDateTime": "2026-09-01T00:00:00Z"}
        )

    monkeypatch.setattr(teams_voice, "get_app_only_token", fake_token)
    monkeypatch.setattr(teams_voice, "_graph_request", fake_request)

    await teams_voice.ensure_subscription(
        db_session, tenant_id=str(test_tenant.id), notification_url="https://x"
    )
    await db_session.commit()
    assert bodies[0]["clientState"]
    await db_session.refresh(voice_enabled)
    assert voice_enabled.encrypted_client_state


# ── Cross-feed deduplication ──────────────────────────────────────────────
#
# Microsoft gives the two feeds unrelated identifiers: "the ID of a
# pstnCallLogRow can't be used to retrieve a callRecord object", and callId is
# documented as not unique. So one real call arrives under two different keys,
# and only a natural-key correlation keeps it one row in the intake feed.


@pytest.mark.asyncio
class TestCrossFeedDeduplication:
    async def test_the_feeds_use_separate_namespaces(self):
        report = teams_voice.normalize_teams_voice_record(
            _pstn_row(), feed=teams_voice.USAGE_REPORT_FEED
        )
        notification = teams_voice.normalize_teams_voice_record(
            _call_record(), feed=teams_voice.NOTIFICATION_FEED
        )
        assert report["external_ref"].startswith("teams_voice:pstn:")
        assert notification["external_ref"].startswith("teams_voice:call:")

    async def test_an_unknown_feed_is_refused(self):
        with pytest.raises(ValueError):
            teams_voice.normalize_teams_voice_record(_pstn_row(), feed="guesswork")

    async def test_the_sweep_does_not_duplicate_a_notified_call(
        self, db_session, test_tenant
    ):
        """The regression this whole namespace split exists to prevent."""
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_call_record()],
            feed=teams_voice.NOTIFICATION_FEED,
        )
        await db_session.commit()

        # The same physical call, an hour later, in the usage report — under a
        # completely different GUID.
        sweep = await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(id="a-totally-different-guid")],
            feed=teams_voice.USAGE_REPORT_FEED,
        )
        await db_session.commit()

        assert sweep.imported == 0
        rows = (
            (
                await db_session.execute(
                    select(CommunicationLog).where(
                        CommunicationLog.tenant_id == test_tenant.id,
                        CommunicationLog.external_ref.like("teams_voice:%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].external_ref == "teams_voice:call:record-1"

    async def test_the_notification_does_not_duplicate_a_swept_call(
        self, db_session, test_tenant
    ):
        # The reverse order: the sweep caught it first (notifications were
        # down), then a late notification arrives.
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row()],
            feed=teams_voice.USAGE_REPORT_FEED,
        )
        await db_session.commit()

        late = await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_call_record()],
            feed=teams_voice.NOTIFICATION_FEED,
        )
        await db_session.commit()
        assert late.imported == 0

        rows = (
            (
                await db_session.execute(
                    select(CommunicationLog).where(
                        CommunicationLog.external_ref.like("teams_voice:%")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].external_ref == "teams_voice:pstn:pstn-1"
        # Both feeds are recorded as having seen it.
        assert set(rows[0].participants["capture_feeds"]) == {
            teams_voice.USAGE_REPORT_FEED,
            teams_voice.NOTIFICATION_FEED,
        }

    async def test_the_second_feed_fills_gaps_without_overwriting(
        self, db_session, test_tenant
    ):
        # A call record with no duration; the usage report knows the billed one.
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_call_record(endDateTime=None)],
            feed=teams_voice.NOTIFICATION_FEED,
        )
        await db_session.commit()

        row = await db_session.scalar(
            select(CommunicationLog).where(
                CommunicationLog.external_ref == "teams_voice:call:record-1"
            )
        )
        assert row.participants.get("callee_name") == "Front Desk"

        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(id="report-1", userDisplayName="Billing Export")],
            feed=teams_voice.USAGE_REPORT_FEED,
        )
        await db_session.commit()
        await db_session.refresh(row)

        # The first feed owns identity; the second only fills what was missing.
        assert row.participants["callee_name"] == "Front Desk"
        assert row.participants["duration_seconds"] == 133

    async def test_calls_outside_the_window_stay_separate(
        self, db_session, test_tenant
    ):
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_call_record()],
            feed=teams_voice.NOTIFICATION_FEED,
        )
        # The same number calling back an hour later is a different call.
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(id="later", startDateTime="2026-08-01T16:30:00Z")],
            feed=teams_voice.USAGE_REPORT_FEED,
        )
        await db_session.commit()

        rows = (
            (
                await db_session.execute(
                    select(CommunicationLog).where(
                        CommunicationLog.external_ref.like("teams_voice:%")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2

    async def test_a_different_caller_is_never_merged(self, db_session, test_tenant):
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_call_record()],
            feed=teams_voice.NOTIFICATION_FEED,
        )
        await teams_voice.import_teams_voice_records(
            db_session,
            tenant_id=str(test_tenant.id),
            records=[_pstn_row(id="other", callerNumber="+15125559999")],
            feed=teams_voice.USAGE_REPORT_FEED,
        )
        await db_session.commit()

        rows = (
            (
                await db_session.execute(
                    select(CommunicationLog).where(
                        CommunicationLog.external_ref.like("teams_voice:%")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2

    async def test_a_record_without_a_number_is_not_correlated(
        self, db_session, test_tenant
    ):
        # Better a visible duplicate than a silent merge into the wrong call.
        assert (
            await teams_voice._find_cross_feed_match(
                db_session,
                tenant_uuid=test_tenant.id,
                normalized={
                    "external_ref": "teams_voice:pstn:x",
                    "occurred_at": datetime.now(timezone.utc),
                    "participants": {"normalized_phone": None},
                },
            )
            is None
        )


@pytest.mark.asyncio
async def test_url_change_recreates_the_subscription(
    db_session, test_tenant, voice_enabled, monkeypatch
):
    """A PATCH extends a subscription's life but cannot repoint it."""
    calls = []

    async def fake_token(db, *, tenant_id, require_enabled=True):
        return "app-token"

    async def fake_request(method, path, *, token, json_body=None, **kw):
        calls.append((method, path))
        if method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            201, json={"id": "sub-new", "expirationDateTime": "2026-09-01T00:00:00Z"}
        )

    monkeypatch.setattr(teams_voice, "get_app_only_token", fake_token)
    monkeypatch.setattr(teams_voice, "_graph_request", fake_request)

    row = await teams_voice.ensure_subscription(
        db_session,
        tenant_id=str(test_tenant.id),
        notification_url="https://new-host/api/integrations/teams/voice/webhook/x",
    )
    await db_session.commit()

    # The stale subscription is torn down rather than renewed in place, which
    # would leave Graph posting to the old host forever.
    assert [c[0] for c in calls] == ["DELETE", "POST"]
    assert row.subscription_id == "sub-new"
    assert row.notification_url.startswith("https://new-host/")


def test_graph_error_maps_status_to_retryability():
    assert isinstance(
        teams_voice._graph_error(httpx.Response(403), "reading"),
        teams_voice.TeamsVoicePermanentError,
    )
    assert isinstance(
        teams_voice._graph_error(httpx.Response(404), "reading"),
        teams_voice.TeamsVoicePermanentError,
    )
    # A 500 is worth retrying; a 403 never is.
    transient = teams_voice._graph_error(httpx.Response(500), "reading")
    assert not isinstance(transient, teams_voice.TeamsVoicePermanentError)
    assert "did not respond" in str(teams_voice._graph_error(None, "reading"))
