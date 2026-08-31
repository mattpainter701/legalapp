import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from app.services.search_identity_ticket import (
    SearchIdentity,
    SearchIdentityTicketError,
    mint_search_identity_ticket,
)
from app.services.smb import _path_is_within_binding
import app.routers.smb as smb_router
import app.services.smb as smb_service_module
from app.schemas.smb import NativeIdentityUpdate
from app.services.native_authorization import (
    NativeAuthorizationError,
    expand_effective_group_sids,
    resolve_native_identity,
)


def test_ticket_contains_only_server_resolved_identity_scope():
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    ticket = mint_search_identity_ticket(
        SearchIdentity(
            tenant_id="tenant-1",
            user_id="user-1",
            source_ids=("share-1",),
            principal_sids=("S-1-5-21-100",),
            identity_version=9,
        ),
        private_key=base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
        audience="agent-1",
        filters={"matter_id": "matter-1"},
        now=1_000,
    )
    payload = json.loads(base64.urlsafe_b64decode(ticket.split(".")[1] + "=="))
    assert payload["tenant_id"] == "tenant-1"
    assert payload["source_ids"] == ["share-1"]
    assert payload["filters"] == {"matter_id": "matter-1"}
    assert "role" not in payload and "browser_scope" not in payload


def test_ticket_rejects_oversized_principal_sets():
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    with pytest.raises(SearchIdentityTicketError, match="too large"):
        mint_search_identity_ticket(
            SearchIdentity(
                tenant_id="tenant-1",
                user_id="user-1",
                source_ids=("share-1",),
                principal_sids=tuple(f"S-1-5-21-{value}" for value in range(4097)),
                identity_version=1,
            ),
            private_key=base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            audience="agent-1",
        )


def test_file_path_must_be_within_exact_matter_binding():
    share = r"\\FS01\Legal"
    assert _path_is_within_binding(
        r"\\FS01\Legal\Client-1\brief.pdf", share, "Client-1"
    )
    assert not _path_is_within_binding(
        r"\\FS01\Legal\Client-10\brief.pdf", share, "Client-1"
    )
    assert not _path_is_within_binding(
        r"\\FS01\Other\Client-1\brief.pdf", share, "Client-1"
    )


def test_nested_group_sid_expansion_is_cycle_safe_and_complete():
    expanded = expand_effective_group_sids(
        "S-1-5-21-100",
        ["S-1-5-21-200"],
        {
            "S-1-5-21-200": ["S-1-5-21-300"],
            "S-1-5-21-300": ["S-1-5-21-200"],
        },
    )
    assert {"S-1-5-21-100", "S-1-5-21-200", "S-1-5-21-300"}.issubset(expanded)


def test_partial_or_oversized_group_expansion_fails_closed():
    with pytest.raises(NativeAuthorizationError):
        expand_effective_group_sids("S-1-5-21-100", ["not-a-sid"], {})
    with pytest.raises(NativeAuthorizationError):
        expand_effective_group_sids(
            "S-1-5-21-100", ["S-1-5-21-200"], {}, max_principals=1
        )


@pytest.mark.asyncio
async def test_stale_or_malformed_stored_identity_fails_closed():
    now = datetime.now(timezone.utc)

    class FakeDb:
        def __init__(self, row):
            self.row = row

        async def scalar(self, _statement):
            return self.row

    base = {
        "state": "healthy",
        "expires_at": now + timedelta(hours=1),
        "resolved_at": now - timedelta(hours=1),
        "primary_sid": "S-1-5-21-100",
        "effective_sids": ["S-1-5-21-200"],
        "version": 1,
        "provider": "ad",
    }
    with pytest.raises(NativeAuthorizationError, match="stale"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**base)),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )
    malformed = {**base, "resolved_at": now, "effective_sids": ["not-a-sid"]}
    with pytest.raises(NativeAuthorizationError, match="SID set"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**malformed)),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )


def test_native_identity_resolution_timestamps_require_timezone():
    with pytest.raises(ValueError, match="timezone"):
        NativeIdentityUpdate(
            provider="ad",
            directory_tenant_id="directory-1",
            object_id="object-1",
            primary_sid="S-1-5-21-100",
            effective_sids=["S-1-5-21-200"],
            group_expansion_complete=True,
            state="healthy",
            resolved_at=datetime(2026, 1, 1),
            expires_at=datetime(2026, 1, 2),
        )


def test_native_identity_update_rejects_bad_sid_and_accepts_aware_timestamps():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="invalid SID"):
        NativeIdentityUpdate(
            provider="ad",
            directory_tenant_id="directory-1",
            object_id="object-1",
            primary_sid="S-1-5-21-100",
            effective_sids=["not-a-sid"],
            state="error",
        )
    body = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=["s-1-5-21-200", "S-1-5-21-200"],
        group_expansion_complete=True,
        state="healthy",
        resolved_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    assert body.effective_sids == ["S-1-5-21-200"]


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _NativeIdentityDb:
    def __init__(self, *, execute_rows=(), scalar_rows=()):
        self.execute_rows = iter(execute_rows)
        self.scalar_rows = iter(scalar_rows)
        self.commits = 0
        self.added = []

    async def execute(self, _statement):
        return _ScalarRows(next(self.execute_rows))

    async def scalar(self, _statement):
        return next(self.scalar_rows)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


@pytest.mark.asyncio
async def test_native_identity_admin_diagnostics_and_update(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=["S-1-5-21-200"],
        state="healthy",
        version=2,
        resolved_at=now,
        expires_at=now + timedelta(minutes=5),
        error_code=None,
    )
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_router, "record_operator_audit", AsyncMock())
    monkeypatch.setattr(smb_router.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(
        smb_router.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "key"
    )
    monkeypatch.setattr(smb_router.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)

    status_db = _NativeIdentityDb(execute_rows=([SimpleNamespace(id=user_id)], [row]))
    status = await smb_router.native_authorization_status(status_db, admin)
    assert status.rollout_ready is True and status.healthy_identities == 1

    list_db = _NativeIdentityDb(execute_rows=([row],))
    diagnostics = await smb_router.list_native_identities(list_db, admin)
    assert diagnostics[0].principal_count == 2

    body = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=["S-1-5-21-200", "S-1-5-21-300"],
        group_expansion_complete=True,
        state="healthy",
        resolved_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    db = _NativeIdentityDb(scalar_rows=(SimpleNamespace(id=user_id), row))
    result = await smb_router.update_native_identity(
        str(user_id), body, SimpleNamespace(), db, admin
    )
    assert result.version == 3 and result.principal_count == 3
    assert db.commits == 1


@pytest.mark.asyncio
async def test_native_identity_update_fails_closed_on_invalid_targets(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    body = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        state="pending",
    )
    with pytest.raises(smb_router.HTTPException) as exc:
        await smb_router.update_native_identity(
            "bad-id", body, SimpleNamespace(), _NativeIdentityDb(), admin
        )
    assert exc.value.status_code == 404

    with pytest.raises(smb_router.HTTPException) as exc:
        await smb_router.update_native_identity(
            str(user_id),
            body,
            SimpleNamespace(),
            _NativeIdentityDb(scalar_rows=(None,)),
            admin,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_file_authorization_revalidation_success_and_fail_closed(monkeypatch):
    service = smb_service_module.smb_service
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    matter_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    share_id = str(uuid.uuid4())
    file_entry = SimpleNamespace(id=uuid.uuid4(), path=r"\\fs\legal\matter\file.pdf")
    identity = SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)
    monkeypatch.setattr(
        smb_service_module, "resolve_native_identity", AsyncMock(return_value=identity)
    )
    monkeypatch.setattr(
        smb_service_module,
        "mint_search_identity_ticket",
        lambda *_args, **_kwargs: "t" * 32,
    )
    publish = AsyncMock()
    monkeypatch.setattr(smb_service_module, "_commit_audit_then_publish", publish)
    monkeypatch.setattr(
        service,
        "get_task_result",
        AsyncMock(return_value={"ok": True, "detail": {"authorized": True}}),
    )
    monkeypatch.setattr(
        smb_service_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True
    )
    monkeypatch.setattr(
        smb_service_module.settings,
        "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY",
        "key",
    )
    db = _NativeIdentityDb()
    await service._revalidate_file_authorization(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        matter_id=matter_id,
        file_entry=file_entry,
        agent_id=agent_id,
        share_id=share_id,
        redis=SimpleNamespace(),
    )
    assert db.added and publish.await_count == 1

    with pytest.raises(ValueError, match="Matter file not found"):
        await service._revalidate_file_authorization(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            matter_id=matter_id,
            file_entry=file_entry,
            agent_id="",
            share_id=share_id,
            redis=SimpleNamespace(),
        )
