"""Durable-job and scheduler behavior for Teams Phone (voice) capture.

Covers the worker handlers, the readiness recheck that runs immediately before
Graph egress, the hourly reconciliation enqueuer, and subscription renewal —
the paths that keep capture running without anyone watching.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.durable_job import DurableJob
from app.models.teams_voice_setting import TeamsVoiceSetting
from app.services import durable_job_worker as worker
from app.services import teams_voice
from app.services.token_vault import encrypt_token


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


def _job(tenant_id, kind, payload=None):
    return DurableJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=kind,
        idempotency_key=f"key-{uuid.uuid4().hex[:8]}",
        payload=payload or {},
    )


# ── Readiness recheck ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTenantReadiness:
    async def test_ready_when_enabled_and_configured(
        self, db_session, test_tenant, voice_enabled
    ):
        row = _job(test_tenant.id, worker.TEAMS_VOICE_CALL_JOB)
        assert await worker._teams_voice_tenant_ready(db_session, row) is True

    async def test_unconfigured_tenant_is_permanent(self, db_session, test_tenant):
        # Retrying the same Graph read will not make an unconfigured tenant
        # ready, so this must not be a retryable failure.
        row = _job(test_tenant.id, worker.TEAMS_VOICE_CALL_JOB)
        with pytest.raises(teams_voice.TeamsVoiceNotConfigured):
            await worker._teams_voice_tenant_ready(db_session, row)

    async def test_disabled_tenant_is_permanent(
        self, db_session, test_tenant, voice_enabled
    ):
        voice_enabled.is_enabled = False
        await db_session.commit()
        row = _job(test_tenant.id, worker.TEAMS_VOICE_CALL_JOB)
        with pytest.raises(teams_voice.TeamsVoiceNotConfigured):
            await worker._teams_voice_tenant_ready(db_session, row)

    async def test_inactive_tenant_is_skipped(
        self, db_session, test_tenant, voice_enabled
    ):
        test_tenant.is_active = False
        await db_session.commit()
        row = _job(test_tenant.id, worker.TEAMS_VOICE_CALL_JOB)
        assert await worker._teams_voice_tenant_ready(db_session, row) is False


# ── Handlers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestHandlers:
    async def test_call_import_reports_its_result(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        async def fake_import(session, *, tenant_id, payload):
            return teams_voice.TeamsVoiceImportResult(imported=1, captured=[])

        monkeypatch.setattr(
            teams_voice, "import_teams_voice_webhook_job", fake_import
        )
        row = _job(
            test_tenant.id,
            worker.TEAMS_VOICE_CALL_JOB,
            {"call_record_id": "record-1"},
        )
        result = await worker._run_teams_voice_call_import(row)
        assert result["imported"] == 1
        assert result["announced"] == 0

    async def test_call_import_announces_new_calls(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        captured_call = {
            "communication_id": str(uuid.uuid4()),
            "caller_name": "Jane Doe",
            "caller_number": "+15125550143",
            "callee_name": "Front Desk",
            "duration_seconds": 133,
            "result": "Completed",
        }

        async def fake_import(session, *, tenant_id, payload):
            return teams_voice.TeamsVoiceImportResult(
                imported=1, captured=[captured_call]
            )

        sent = {}

        async def fake_notify(tenant_id, event_type, *, title, fields, **kw):
            sent["event"] = event_type
            sent["fields"] = fields
            return 1

        from app.services import teams_notify

        monkeypatch.setattr(
            teams_voice, "import_teams_voice_webhook_job", fake_import
        )
        monkeypatch.setattr(teams_notify, "notify", fake_notify)

        row = _job(
            test_tenant.id,
            worker.TEAMS_VOICE_CALL_JOB,
            {"call_record_id": "record-1"},
        )
        result = await worker._run_teams_voice_call_import(row)

        assert result["announced"] == 1
        assert sent["event"] == "voice_call_captured"
        assert sent["fields"]["matter_name"] == "Jane Doe"
        assert sent["fields"]["Answered by"] == "Front Desk"
        assert sent["fields"]["Duration"] == "133s"

    async def test_announcement_falls_back_to_the_number(
        self, test_tenant, monkeypatch
    ):
        sent = {}

        async def fake_notify(tenant_id, event_type, *, title, fields, **kw):
            sent["fields"] = fields
            return 1

        from app.services import teams_notify

        monkeypatch.setattr(teams_notify, "notify", fake_notify)
        await worker._announce_captured_voice_calls(
            str(test_tenant.id), [{"caller_number": "+15125550143"}]
        )
        assert sent["fields"]["matter_name"] == "+15125550143"

    async def test_announcement_names_an_unknown_caller(
        self, test_tenant, monkeypatch
    ):
        sent = {}

        async def fake_notify(tenant_id, event_type, *, title, fields, **kw):
            sent["fields"] = fields
            return 0

        from app.services import teams_notify

        monkeypatch.setattr(teams_notify, "notify", fake_notify)
        assert await worker._announce_captured_voice_calls(
            str(test_tenant.id), [{}]
        ) == 0
        assert sent["fields"]["matter_name"] == "Unknown caller"

    async def test_reconcile_clamps_the_window(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        seen = {}

        async def fake_sync(session, *, tenant_id, days):
            seen["days"] = days
            return teams_voice.TeamsVoiceImportResult(imported=2, skipped=1)

        monkeypatch.setattr(teams_voice, "sync_teams_voice_call_history", fake_sync)
        row = _job(test_tenant.id, worker.TEAMS_VOICE_RECONCILE_JOB, {"days": 99})
        result = await worker._run_teams_voice_reconcile(row)
        # A caller cannot ask the sweep for an unbounded history window.
        assert seen["days"] == 7
        assert result["imported"] == 2

    async def test_reconcile_defaults_its_window(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        seen = {}

        async def fake_sync(session, *, tenant_id, days):
            seen["days"] = days
            return teams_voice.TeamsVoiceImportResult()

        monkeypatch.setattr(teams_voice, "sync_teams_voice_call_history", fake_sync)
        row = _job(test_tenant.id, worker.TEAMS_VOICE_RECONCILE_JOB, {})
        await worker._run_teams_voice_reconcile(row)
        assert seen["days"] == 2


# ── Reconciliation enqueuer ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestReconciliationEnqueuer:
    async def _jobs(self, db_session, tenant_id):
        result = await db_session.execute(
            select(DurableJob).where(
                DurableJob.tenant_id == tenant_id,
                DurableJob.kind == worker.TEAMS_VOICE_RECONCILE_JOB,
            )
        )
        return list(result.scalars().all())

    async def test_enqueues_for_a_configured_tenant(
        self, db_session, test_tenant, voice_enabled
    ):
        await worker.enqueue_teams_voice_reconciliation_jobs()
        assert len(await self._jobs(db_session, test_tenant.id)) == 1

    async def test_is_idempotent_within_the_hour(
        self, db_session, test_tenant, voice_enabled
    ):
        await worker.enqueue_teams_voice_reconciliation_jobs()
        await worker.enqueue_teams_voice_reconciliation_jobs()
        # One outstanding sweep per tenant, not one per scheduler tick.
        assert len(await self._jobs(db_session, test_tenant.id)) == 1

    async def test_skips_a_tenant_without_voice(self, db_session, test_tenant):
        await worker.enqueue_teams_voice_reconciliation_jobs()
        assert await self._jobs(db_session, test_tenant.id) == []

    async def test_skips_a_tenant_missing_its_directory(
        self, db_session, test_tenant, voice_enabled
    ):
        voice_enabled.entra_tenant_id = None
        await db_session.commit()
        await worker.enqueue_teams_voice_reconciliation_jobs()
        assert await self._jobs(db_session, test_tenant.id) == []


# ── Subscription renewal ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubscriptionRenewal:
    async def test_renews_when_expiry_is_near(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        voice_enabled.subscription_expires_at = datetime.now(timezone.utc) + timedelta(
            hours=1
        )
        await db_session.commit()
        seen = {}

        async def fake_ensure(session, *, tenant_id, notification_url):
            seen["url"] = notification_url
            return voice_enabled

        monkeypatch.setattr(teams_voice, "ensure_subscription", fake_ensure)
        await worker.renew_teams_voice_subscriptions()
        assert seen["url"] == voice_enabled.notification_url

    async def test_leaves_a_healthy_subscription_alone(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        called = {"n": 0}

        async def fake_ensure(session, *, tenant_id, notification_url):
            called["n"] += 1
            return voice_enabled

        monkeypatch.setattr(teams_voice, "ensure_subscription", fake_ensure)
        await worker.renew_teams_voice_subscriptions()
        assert called["n"] == 0

    async def test_records_a_renewal_failure_for_the_admin(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        voice_enabled.subscription_id = None
        await db_session.commit()

        async def fake_ensure(session, *, tenant_id, notification_url):
            raise teams_voice.TeamsVoicePermanentError("Graph denied the subscription")

        monkeypatch.setattr(teams_voice, "ensure_subscription", fake_ensure)
        await worker.renew_teams_voice_subscriptions()

        await db_session.refresh(voice_enabled)
        # Capture keeps working through the hourly sweep; the admin panel needs
        # to say why the fast path is down.
        assert voice_enabled.last_sync_status == "subscription_error"
        assert "denied" in voice_enabled.last_sync_error

    async def test_unexpected_errors_are_contained(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        voice_enabled.subscription_id = None
        await db_session.commit()

        async def fake_ensure(session, *, tenant_id, notification_url):
            raise RuntimeError("boom")

        monkeypatch.setattr(teams_voice, "ensure_subscription", fake_ensure)
        # One tenant's failure must not stop the sweep for everyone else.
        await worker.renew_teams_voice_subscriptions()

        await db_session.refresh(voice_enabled)
        assert voice_enabled.last_sync_status == "subscription_error"

    async def test_skips_a_disabled_tenant(
        self, db_session, test_tenant, voice_enabled, monkeypatch
    ):
        voice_enabled.is_enabled = False
        voice_enabled.subscription_id = None
        await db_session.commit()
        called = {"n": 0}

        async def fake_ensure(session, *, tenant_id, notification_url):
            called["n"] += 1
            return voice_enabled

        monkeypatch.setattr(teams_voice, "ensure_subscription", fake_ensure)
        await worker.renew_teams_voice_subscriptions()
        assert called["n"] == 0


def test_voice_jobs_share_the_low_latency_drain():
    # Both providers capture inbound calls into intake, so neither should sit
    # behind general background work.
    assert worker.TEAMS_VOICE_JOB_KINDS <= worker.VOICE_JOB_KINDS
    assert worker.ZOOM_PHONE_JOB_KINDS <= worker.VOICE_JOB_KINDS


def test_graph_notification_body_is_not_trusted_for_call_content():
    jobs = teams_voice.teams_voice_webhook_jobs(
        {
            "value": [
                {
                    "changeType": "created",
                    "resourceData": {
                        "id": "record-1",
                        "callerNumber": "+15550000000",
                        "duration": 9999,
                    },
                }
            ]
        }
    )
    # Only the id survives; everything else is re-read from Graph.
    assert jobs[0].payload["call_record_id"] == "record-1"
    assert set(jobs[0].payload) == {
        "call_record_id",
        "change_type",
        "subscription_id",
    }
