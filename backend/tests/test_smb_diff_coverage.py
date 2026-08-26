"""Focused branch coverage for the SMB hardening changes."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.routers.smb as router
import app.services.smb as smb
from app.schemas.smb import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    ContentFetchResult,
)


def _request(redis=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))


def _body():
    return AgentRegisterRequest(
        pairing_code="pairing-secret", agent_name="test", agent_version="0.15.0"
    )


@pytest.mark.asyncio
async def test_registration_receipt_handles_bytes_mismatch_and_malformed(monkeypatch):
    body = _body()
    response = AgentRegisterResponse(agent_id="a", api_key="k")

    class Redis:
        def __init__(self, value):
            self.value = value

        async def get(self, _key):
            return self.value

        async def set(self, *_args, **_kwargs):
            return None

    await router._store_registration_receipt(None, body, response)

    # Exercise the bytes decode path, then fingerprint rejection.
    class Store:
        async def set(self, key, value, ex):
            self.value = value.encode()

    store = Store()
    await router._store_registration_receipt(store, body, response)
    assert (
        await router._load_registration_receipt(StoreWith(store.value), body)
        == response
    )
    other = _body()
    other.agent_name = "different"
    assert (
        await router._load_registration_receipt(StoreWith(store.value), other) is None
    )
    assert await router._load_registration_receipt(Redis(b"not-a-token"), body) is None


class StoreWith:
    def __init__(self, value):
        self.value = value

    async def get(self, _key):
        return self.value


@pytest.mark.asyncio
async def test_receipt_store_and_wait_swallow_redis_failures(monkeypatch):
    body = _body()
    response = AgentRegisterResponse(agent_id="a", api_key="k")

    class Broken:
        async def set(self, *args, **kwargs):
            raise RuntimeError("redis down")

        async def get(self, _key):
            raise RuntimeError("redis down")

    await router._store_registration_receipt(Broken(), body, response)
    monkeypatch.setattr(router.asyncio, "sleep", AsyncMock())
    assert await router._wait_for_registration_receipt(Broken(), body) is None
    assert router._registration_receipt_key("secret") != "secret"


@pytest.mark.asyncio
async def test_register_agent_returns_cached_and_maps_failure(monkeypatch):
    body = _body()
    cached = AgentRegisterResponse(agent_id="a", api_key="k")
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(
        router, "_load_registration_receipt", AsyncMock(return_value=cached)
    )
    assert await router.register_agent(body, _request(), db) == cached

    monkeypatch.setattr(
        router, "_load_registration_receipt", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        router.smb_service, "register_agent", AsyncMock(side_effect=ValueError("bad"))
    )
    monkeypatch.setattr(
        router, "_wait_for_registration_receipt", AsyncMock(return_value=None)
    )
    with pytest.raises(router.HTTPException) as exc:
        await router.register_agent(body, _request(), db)
    assert exc.value.status_code == 400


def test_manifest_redirect_parser_rejects_bad_port_and_query():
    assert not smb._is_official_manifest_redirect("https://github.com:bad/path")
    assert not smb._is_official_manifest_redirect(
        "https://github.com/mattpainter701/legalapp/releases/download/agent-v0.15.0/agent-update.json?x=1"
    )


@pytest.mark.asyncio
async def test_restore_queued_update_noops_without_redis_or_existing_task():
    agent = SimpleNamespace(
        id="a",
        update_status="queued",
        update_task_id="t",
        update_target_version="1.0.0",
        update_manifest_id="agent-v1.0.0",
    )
    manifest = {"target_version": "1.0.0", "manifest_id": "agent-v1.0.0"}
    assert (
        await smb.restore_queued_agent_update(agent, "tenant", None, manifest=manifest)
        is False
    )

    class Redis:
        async def exists(self, _key):
            return True

    assert (
        await smb.restore_queued_agent_update(
            agent, "tenant", Redis(), manifest=manifest
        )
        is False
    )


@pytest.mark.asyncio
async def test_pending_tasks_scans_until_deadline(monkeypatch):
    class Redis:
        async def scan_iter(self, **kwargs):
            yield "key"

        async def get(self, _key):
            return None

    monkeypatch.setattr(smb.time, "monotonic", lambda: 10.0)
    assert (
        await smb.smb_service.get_pending_tasks(
            SimpleNamespace(), "agent", redis=Redis(), limit=1, wait_seconds=0
        )
        == []
    )


@pytest.mark.asyncio
async def test_task_result_rejects_missing_redis_and_bad_completed_payload():
    result = ContentFetchResult(task_id="t", content="x")
    with pytest.raises(RuntimeError):
        await smb.smb_service.submit_task_result(SimpleNamespace(), "a", "t", result)

    class Redis:
        async def get(self, key):
            if key.startswith("smb_task_pending"):
                return None
            return b"not-json"

    with pytest.raises(ValueError, match="Task not found"):
        await smb.smb_service.submit_task_result(
            SimpleNamespace(), "a", "t", result, Redis(), tenant_id="tenant"
        )


@pytest.mark.asyncio
async def test_task_result_validates_tenant_and_access_log(monkeypatch):
    class DB:
        async def execute(self, _stmt):
            return SimpleNamespace(rowcount=0)

    class Redis:
        async def get(self, _key):
            return json.dumps(
                {
                    "tenant_id": "00000000-0000-0000-0000-000000000001",
                    "access_log_id": "00000000-0000-0000-0000-000000000001",
                }
            )

    result = ContentFetchResult(task_id="t", content="x")
    with pytest.raises(ValueError, match="access log"):
        await smb.smb_service.submit_task_result(
            DB(),
            "00000000-0000-0000-0000-000000000002",
            "t",
            result,
            Redis(),
            tenant_id="00000000-0000-0000-0000-000000000001",
        )


@pytest.mark.asyncio
async def test_content_result_binding_guards_and_poll(monkeypatch):
    service = smb.smb_service

    class Redis:
        async def get(self, _key):
            return json.dumps(
                {
                    "tenant_id": "other",
                    "file_id": "f",
                    "share_id": "s",
                    "kind": "content_fetch",
                }
            )

    with pytest.raises(ValueError, match="tenant"):
        await service.get_task_result("t", "tenant", Redis())
    monkeypatch.setattr(service, "get_content_result", AsyncMock(return_value=None))
    monkeypatch.setattr(smb.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(smb.asyncio, "sleep", AsyncMock())
    assert (
        await service.poll_content_result(
            "t", "tenant", "f", Redis(), timeout_seconds=0
        )
        is None
    )


@pytest.mark.asyncio
async def test_router_task_and_content_error_mappings(monkeypatch):
    agent = SimpleNamespace(id="a", tenant_id="tenant")
    body = ContentFetchResult(task_id="other")
    with pytest.raises(router.HTTPException) as exc:
        await router.submit_task_result(
            "a", "t", body, _request(), SimpleNamespace(), agent
        )
    assert exc.value.status_code == 400

    body = ContentFetchResult(task_id="t")
    monkeypatch.setattr(
        router.smb_service,
        "submit_task_result",
        AsyncMock(side_effect=RuntimeError("down")),
    )
    with pytest.raises(router.HTTPException) as exc:
        await router.submit_task_result(
            "a", "t", body, _request(), SimpleNamespace(), agent
        )
    assert exc.value.status_code == 503
    user = SimpleNamespace(id="u", tenant_id="tenant")
    monkeypatch.setattr(
        router.smb_service,
        "request_content_fetch",
        AsyncMock(side_effect=RuntimeError("down")),
    )
    with pytest.raises(router.HTTPException) as exc:
        await router.request_content_fetch(
            "f", request=_request(), db=SimpleNamespace(), user=user
        )
    assert exc.value.status_code == 503


def test_smb_path_helpers_and_pairing_code_cover_normalization_edges():
    assert smb._normalize_extensions([" PDF ", ".pdf", "docx", "", None]) == [
        ".pdf",
        ".docx",
    ]
    assert smb._normalize_extensions([]) is None
    assert smb._parse_unc(r"\\server\share\folder\file") == (
        "server",
        "share",
        r"folder\file",
    )
    assert smb._parse_unc("relative") == (None, None, None)
    assert smb._normalize_folder_path(" /Client/./Matter ") == "Client/Matter"
    with pytest.raises(ValueError, match="within"):
        smb._normalize_folder_path("Client/../Other")
    assert smb._escape_like("a!_%") == "a!!!_!%"
    code = smb._pairing_code()
    assert len(code) == 19 and code.count("-") == 3
    assert all(set(part) <= set(smb.PAIRING_CODE_ALPHABET) for part in code.split("-"))


def test_expiration_handles_idle_missing_and_malformed_versions():
    now = datetime.now(timezone.utc)
    for agent in [
        SimpleNamespace(update_status="idle"),
        SimpleNamespace(
            update_status="queued", update_requested_at=None, update_target_version=None
        ),
    ]:
        assert smb.expire_stale_agent_update(agent, now=now) is False
    malformed = SimpleNamespace(
        update_status="queued",
        update_requested_at=now - timedelta(hours=1),
        update_target_version="bad",
        agent_version="also-bad",
        update_error=None,
    )
    assert smb.expire_stale_agent_update(malformed, now=now) is True


class _RedisValues:
    def __init__(self, values=None):
        self.values = values or {}
        self.set_calls = []
        self.deleted = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    async def delete(self, key):
        self.deleted.append(key)

    async def scan_iter(self, **kwargs):
        prefix = kwargs["match"].rstrip("*")
        for key in self.values:
            if key.startswith(prefix):
                yield key


class _DbResult:
    rowcount = 1


class _ResultDb:
    def __init__(self):
        self.commits = 0

    async def execute(self, _stmt):
        return _DbResult()

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_pending_task_scan_decodes_payload_and_empty_relay_is_safe():
    redis = _RedisValues({"smb_task_pending:a:t": json.dumps({"task_id": "t"})})
    tasks = await smb.smb_service.get_pending_tasks(None, "a", redis, limit=1)
    assert [task.task_id for task in tasks] == ["t"]
    assert await smb.smb_service.get_pending_tasks(None, "a", None) == []


@pytest.mark.asyncio
async def test_task_result_publishes_content_and_rejects_tenant_or_missing_log():
    tenant = "00000000-0000-0000-0000-000000000010"
    agent = "00000000-0000-0000-0000-000000000020"
    meta = {
        "tenant_id": tenant,
        "kind": "content_fetch",
        "file_id": "00000000-0000-0000-0000-000000000011",
        "share_id": "00000000-0000-0000-0000-000000000012",
        "access_log_id": "00000000-0000-0000-0000-000000000001",
    }
    meta["agent_id"] = agent
    redis = _RedisValues({f"smb_task_pending:{agent}:t": json.dumps(meta)})
    db = _ResultDb()
    await smb.smb_service.submit_task_result(
        db,
        agent,
        "t",
        ContentFetchResult(task_id="t", content="abc"),
        redis,
        tenant_id=tenant,
    )
    payload = json.loads(redis.values[f"smb_task:{tenant}:t"])
    assert payload["ok"] and payload["content"] == "abc" and db.commits == 1
    bad = _RedisValues(
        {
            f"smb_task_pending:{agent}:t": json.dumps(
                {**meta, "tenant_id": "00000000-0000-0000-0000-000000000099"}
            )
        }
    )
    with pytest.raises(ValueError, match="tenant mismatch"):
        await smb.smb_service.submit_task_result(
            _ResultDb(),
            agent,
            "t",
            ContentFetchResult(task_id="t"),
            bad,
            tenant_id=tenant,
        )
    no_log = _RedisValues(
        {
            f"smb_task_pending:{agent}:t": json.dumps(
                {"tenant_id": tenant, "kind": "content_fetch"}
            )
        }
    )
    with pytest.raises(ValueError, match="access log"):
        await smb.smb_service.submit_task_result(
            _ResultDb(),
            agent,
            "t",
            ContentFetchResult(task_id="t"),
            no_log,
            tenant_id=tenant,
        )
