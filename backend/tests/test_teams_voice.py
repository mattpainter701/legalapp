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
        assert normalized["external_ref"] == "teams_voice:call:pstn-1"
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
        assert normalized["external_ref"] == "teams_voice:call:webhook-id"


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
                CommunicationLog.external_ref == "teams_voice:call:pstn-1"
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
                CommunicationLog.external_ref == "teams_voice:call:pstn-1"
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
