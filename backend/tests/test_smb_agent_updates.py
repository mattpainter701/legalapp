import pytest
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import app.services.smb as smb


def _manifest(**overrides):
    assets = {
        "windows-x86_64": {
            "name": "lawhand-agent-x64.msi",
            "sha256": "a" * 64,
        },
        "linux-x86_64": {
            "name": "lawhand-agent-linux-x86_64.tar.gz",
            "sha256": "b" * 64,
        },
    }
    value = {"schema_version": 1, "version": "0.15.0", "assets": assets}
    value.update(overrides)
    return value


def test_agent_versions_require_exact_numeric_semver():
    assert smb._version_key("0.15.0") == (0, 15, 0)
    with pytest.raises(ValueError):
        smb._version_key("0.15")


def test_stale_update_without_confirming_version_fails():
    agent = SimpleNamespace(
        update_status="in_progress",
        update_requested_at=datetime.now(timezone.utc) - timedelta(minutes=31),
        update_target_version="0.15.1",
        agent_version="0.15.0",
        update_error=None,
    )

    assert smb.expire_stale_agent_update(agent) is True
    assert agent.update_status == "failed"
    assert "30 minutes" in agent.update_error


def test_stale_update_does_not_fail_after_target_heartbeat():
    agent = SimpleNamespace(
        update_status="in_progress",
        update_requested_at=datetime.now(timezone.utc) - timedelta(minutes=31),
        update_target_version="0.15.1",
        agent_version="0.15.1",
        update_error=None,
    )

    assert smb.expire_stale_agent_update(agent) is False
    assert agent.update_status == "in_progress"


@pytest.mark.asyncio
async def test_queued_update_is_restored_with_fixed_contract():
    class Redis:
        def __init__(self):
            self.value = None

        async def exists(self, _key):
            return False

        async def set(self, key, value, ex):
            self.value = (key, json.loads(value), ex)

    agent = SimpleNamespace(
        id="agent-1",
        update_status="queued",
        update_task_id="task-1",
        update_target_version="0.15.1",
        update_manifest_id="agent-v0.15.1",
    )
    redis = Redis()

    manifest = {
        "manifest_id": "agent-v0.15.1",
        "target_version": "0.15.1",
    }
    assert (
        await smb.restore_queued_agent_update(
            agent, "tenant-1", redis, manifest=manifest
        )
        is True
    )
    key, payload, ttl = redis.value
    assert key == "smb_task_pending:agent-1:task-1"
    assert payload["kind"] == "agent_update"
    assert payload["target_version"] == "0.15.1"
    assert payload["manifest_id"] == "agent-v0.15.1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["agent_id"] == "agent-1"
    assert "url" not in payload and "apply" not in payload
    assert ttl == smb.REDIS_ADMIN_TASK_TTL


@pytest.mark.asyncio
async def test_direct_restore_rejects_stale_release_before_redis_publish():
    class Redis:
        async def exists(self, _key):
            pytest.fail("stale task must be rejected before consulting Redis")

    agent = SimpleNamespace(
        id="agent-1",
        update_status="queued",
        update_task_id="task-1",
        update_target_version="0.15.0",
        update_manifest_id="agent-v0.15.0",
        update_error=None,
    )
    current = {
        "manifest_id": "agent-v0.15.1",
        "target_version": "0.15.1",
    }

    assert (
        await smb.restore_queued_agent_update(
            agent, "tenant-1", Redis(), manifest=current
        )
        is False
    )
    assert agent.update_status == "failed"
    assert "no longer the official release" in agent.update_error


@pytest.mark.asyncio
async def test_reconciliation_republishes_only_current_official_release(monkeypatch):
    class Redis:
        def __init__(self, db):
            self.db = db
            self.values = {}

        async def exists(self, key):
            assert self.db.committed is True
            return key in self.values

        async def set(self, key, value, ex):
            assert self.db.committed is True
            self.values[key] = (json.loads(value), ex)

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class DB:
        def __init__(self):
            self.committed = False

        async def execute(self, _statement):
            return Result([current, stale])

        async def flush(self):
            return None

        async def commit(self):
            self.committed = True

    current = SimpleNamespace(
        id="agent-current",
        tenant_id="tenant-1",
        update_status="queued",
        update_task_id="task-current",
        update_target_version="0.15.0",
        update_manifest_id="agent-v0.15.0",
        update_requested_at=datetime.now(timezone.utc),
        agent_version="0.14.0",
        update_error=None,
    )
    stale = SimpleNamespace(
        id="agent-stale",
        tenant_id="tenant-2",
        update_status="queued",
        update_task_id="task-stale",
        update_target_version="0.14.0",
        update_manifest_id="agent-v0.14.0",
        update_requested_at=datetime.now(timezone.utc),
        agent_version="0.13.0",
        update_error=None,
    )

    async def manifest():
        return {
            "manifest_id": "agent-v0.15.0",
            "target_version": "0.15.0",
            "assets": _manifest()["assets"],
        }

    monkeypatch.setattr(smb, "fetch_agent_manifest", manifest)
    db = DB()
    redis = Redis(db)
    assert await smb.reconcile_queued_agent_updates(db, redis) == 1
    assert db.committed is True
    assert "smb_task_pending:agent-current:task-current" in redis.values
    assert stale.update_status == "failed"
    assert "no longer the official release" in stale.update_error


@pytest.mark.asyncio
async def test_reconciliation_expires_old_reservation_without_republishing(
    monkeypatch,
):
    class Redis:
        async def exists(self, _key):
            pytest.fail("expired update must not consult Redis")

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [agent]

    class DB:
        async def execute(self, _statement):
            return Result()

        async def flush(self):
            return None

        async def commit(self):
            return None

    agent = SimpleNamespace(
        id="agent-old",
        tenant_id="tenant-1",
        update_status="queued",
        update_task_id="task-old",
        update_target_version="0.15.0",
        update_manifest_id="agent-v0.15.0",
        update_requested_at=datetime.now(timezone.utc) - timedelta(minutes=31),
        agent_version="0.14.0",
        update_error=None,
    )

    async def manifest():
        raise RuntimeError("official release host is unavailable")

    monkeypatch.setattr(smb, "fetch_agent_manifest", manifest)

    assert await smb.reconcile_queued_agent_updates(DB(), Redis()) == 0
    assert agent.update_status == "failed"
    assert "30 minutes" in agent.update_error


@pytest.mark.asyncio
async def test_failed_heartbeat_cannot_be_completed_by_reported_version():
    class DB:
        def __init__(self, agent):
            self.agent = agent
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return SimpleNamespace(scalar_one_or_none=lambda: self.agent)

        async def flush(self):
            return None

    agent = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        update_target_version="0.15.0",
        agent_version="0.15.0",
        update_status="queued",
        update_error=None,
        update_completed_at=None,
    )
    await smb.smb_service.record_heartbeat(
        DB(agent),
        str(agent.id),
        {
            "agent_version": "0.15.0",
            "update_target_version": "0.15.0",
            "update_status": "failed",
            "update_error": "MSI failed",
        },
    )
    assert agent.update_status == "failed"
    assert agent.update_error == "MSI failed"
    assert agent.update_completed_at is None


@pytest.mark.asyncio
async def test_manifest_rejects_urls_and_bad_assets(monkeypatch):
    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            payload = _manifest(
                assets={
                    **_manifest()["assets"],
                    "windows-x86_64": {
                        "name": "lawhand-agent-x64.msi",
                        "sha256": "a" * 64,
                        "url": "https://evil.example/update.msi",
                    },
                }
            )
            yield json.dumps(payload).encode()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(smb, "_manifest_cache", None)
    monkeypatch.setattr(smb, "_manifest_failure_until", 0.0)
    with pytest.raises(ValueError, match="unexpected asset fields"):
        await smb.fetch_agent_manifest()


def test_manifest_redirect_allowlist_rejects_downgrade_and_untrusted_hosts():
    assert not smb._is_official_manifest_redirect("http://github.com/release")
    assert not smb._is_official_manifest_redirect("https://evil.example/release")
    assert not smb._is_official_manifest_redirect(
        "https://github.com/release#fragment"
    )
    assert smb._is_official_manifest_redirect(
        "https://release-assets.githubusercontent.com/release/asset"
    )
    assert smb._is_official_manifest_redirect(
        "https://github.com/mattpainter701/legalapp/releases/download/agent-v0.15.0/agent-update.json"
    )
    assert not smb._is_official_manifest_redirect(
        "https://github.com/mattpainter701/legalapp/other"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    ["http://github.com/redirect", "https://evil.example/manifest"],
)
async def test_manifest_redirect_flow_rejects_downgrade_or_untrusted_location(
    monkeypatch, location
):
    class Response:
        status_code = 302
        headers = {"location": location}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(smb, "_manifest_cache", None)
    monkeypatch.setattr(smb, "_manifest_failure_until", 0.0)
    with pytest.raises(ValueError, match="untrusted host"):
        await smb.fetch_agent_manifest()


@pytest.mark.asyncio
async def test_manifest_outage_uses_short_negative_cache(monkeypatch):
    calls = 0

    async def unavailable():
        nonlocal calls
        calls += 1
        raise RuntimeError("release host unavailable")

    monkeypatch.setattr(smb, "_fetch_agent_manifest_uncached", unavailable)
    monkeypatch.setattr(smb.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(smb, "_manifest_cache", None)
    monkeypatch.setattr(smb, "_manifest_failure_until", 0.0)

    with pytest.raises(RuntimeError, match="release host unavailable"):
        await smb.fetch_agent_manifest()
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await smb.fetch_agent_manifest()
    assert calls == 1
