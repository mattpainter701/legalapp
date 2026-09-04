import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter
from app.models.user import User
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
    authorized_matter_ids,
    expand_effective_group_sids,
    require_matter_authorization,
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


@pytest.mark.asyncio
async def test_resolve_native_identity_rejects_unhealthy_or_expired_or_orphaned_primary():
    now = datetime.now(timezone.utc)

    class FakeDb:
        def __init__(self, row):
            self.row = row

        async def scalar(self, _statement):
            return self.row

    base = {
        "state": "healthy",
        "expires_at": now + timedelta(hours=1),
        "resolved_at": now,
        "primary_sid": "S-1-5-21-100",
        "effective_sids": ["S-1-5-21-200"],
        "version": 3,
        "provider": "ad",
    }
    # Line 76: state != "healthy"
    with pytest.raises(NativeAuthorizationError, match="not healthy"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**{**base, "state": "error"})),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )
    # Line 76: row is None (no mapping at all)
    with pytest.raises(NativeAuthorizationError, match="not healthy"):
        await resolve_native_identity(
            FakeDb(None),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )
    # Line 78: expires_at is None
    with pytest.raises(NativeAuthorizationError, match="stale"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**{**base, "expires_at": None})),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )
    # Line 78: expires_at already in the past
    with pytest.raises(NativeAuthorizationError, match="stale"):
        await resolve_native_identity(
            FakeDb(SimpleNamespace(**{**base, "expires_at": now - timedelta(seconds=1)})),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            now=now,
        )


@pytest.mark.asyncio
async def test_resolve_native_identity_returns_normalized_scope_for_healthy_user():
    now = datetime.now(timezone.utc)

    class FakeDb:
        def __init__(self, row):
            self.row = row

        async def scalar(self, _statement):
            return self.row

    row = SimpleNamespace(
        state="healthy",
        expires_at=now + timedelta(hours=1),
        resolved_at=now,
        primary_sid="s-1-5-21-100",
        effective_sids=["S-1-5-21-200", "S-1-5-21-200", "s-1-5-21-300"],
        version=7,
        provider="entra",
    )
    resolved = await resolve_native_identity(
        FakeDb(row),
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        now=now,
    )
    # Lines 89-98: normalized scope, version, and provider carried through.
    assert resolved.principal_sids[0] == "S-1-1-0"
    assert resolved.principal_sids[-1] == "S-1-5-21-300"
    assert "S-1-5-21-100" in resolved.principal_sids
    assert resolved.version == 7
    assert resolved.provider == "entra"
    assert resolved.tenant_id == "00000000-0000-0000-0000-000000000001"
    assert resolved.user_id == "00000000-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_require_matter_authorization_missing_matter_fails_closed(
    db_session, test_tenant, test_user
):
    with pytest.raises(NativeAuthorizationError, match="unavailable"):
        await require_matter_authorization(
            db_session,
            str(test_tenant.id),
            str(test_user.id),
            str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_require_matter_authorization_unrestricted_matter_returns_row(
    db_session, test_tenant, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"open-matter-{uuid.uuid4().hex[:8]}",
        matter_name="Open matter",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    await db_session.refresh(matter)

    returned = await require_matter_authorization(
        db_session,
        str(test_tenant.id),
        str(test_user.id),
        str(matter.id),
    )
    assert returned.id == matter.id


@pytest.mark.asyncio
async def test_require_matter_authorization_restricted_matter_denies_outsider(
    db_session, test_tenant, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"restricted-{uuid.uuid4().hex[:8]}",
        matter_name="Restricted matter",
        status="open",
        plugin_workflow_state={
            "security_policy": {"restricted": True, "allowed_user_ids": []}
        },
    )
    db_session.add(matter)
    await db_session.commit()
    await db_session.refresh(matter)

    outsider_id = uuid.uuid4()
    db_session.add(
        User(
            id=outsider_id,
            tenant_id=test_tenant.id,
            email=f"outsider-{outsider_id.hex[:6]}@example.com",
            full_name="Outsider",
            role="user",
            is_active=True,
        )
    )
    await db_session.commit()

    with pytest.raises(NativeAuthorizationError, match="unavailable"):
        await require_matter_authorization(
            db_session,
            str(test_tenant.id),
            str(outsider_id),
            str(matter.id),
        )


@pytest.mark.asyncio
async def test_require_matter_authorization_restricted_matter_allows_explicit_user(
    db_session, test_tenant, test_user
):
    outsider_id = uuid.uuid4()
    db_session.add(
        User(
            id=outsider_id,
            tenant_id=test_tenant.id,
            email=f"allowed-{outsider_id.hex[:6]}@example.com",
            full_name="Allowed outsider",
            role="user",
            is_active=True,
        )
    )
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"restricted-allow-{uuid.uuid4().hex[:8]}",
        matter_name="Restricted allowlist",
        status="open",
        plugin_workflow_state={
            "security_policy": {
                "restricted": True,
                "allowed_user_ids": [str(outsider_id)],
            }
        },
    )
    db_session.add(matter)
    await db_session.commit()
    await db_session.refresh(matter)

    returned = await require_matter_authorization(
        db_session,
        str(test_tenant.id),
        str(outsider_id),
        str(matter.id),
    )
    assert returned.id == matter.id


@pytest.mark.asyncio
async def test_require_matter_authorization_restricted_matter_allows_assigned_user(
    db_session, test_tenant, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"restricted-assign-{uuid.uuid4().hex[:8]}",
        matter_name="Restricted assigned",
        status="open",
        plugin_workflow_state={"security_policy": {"ethical_wall": True}},
    )
    associate_id = uuid.uuid4()
    db_session.add(matter)
    db_session.add(
        User(
            id=associate_id,
            tenant_id=test_tenant.id,
            email=f"associate-{associate_id.hex[:6]}@example.com",
            full_name="Associate",
            role="user",
            is_active=True,
        )
    )
    db_session.add(
        MatterAssignment(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=matter.id,
            user_id=associate_id,
            role="associate",
            is_primary=False,
        )
    )
    await db_session.commit()
    await db_session.refresh(matter)

    returned = await require_matter_authorization(
        db_session,
        str(test_tenant.id),
        str(associate_id),
        str(matter.id),
    )
    assert returned.id == matter.id


def test_mint_ticket_rejects_invalid_signing_key_encoding():
    identity = SearchIdentity(
        tenant_id="tenant-1",
        user_id="user-1",
        source_ids=("share-1",),
        principal_sids=("S-1-5-21-100",),
        identity_version=1,
    )
    # Lines 29-30: garbage base64 that cannot be decoded at all.
    with pytest.raises(SearchIdentityTicketError, match="invalid"):
        mint_search_identity_ticket(
            identity,
            private_key="@@@not-base64@@@",
            audience="agent-1",
        )
    # Line 34: well-formed base64 but not 32 raw bytes.
    with pytest.raises(SearchIdentityTicketError, match="32 bytes"):
        mint_search_identity_ticket(
            identity,
            private_key=base64.urlsafe_b64encode(b"too-short").rstrip(b"=").decode(),
            audience="agent-1",
        )


def test_mint_ticket_rejects_empty_or_missing_scope():
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    encoded = base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode()
    base_kwargs = {
        "private_key": encoded,
        "audience": "agent-1",
    }
    # Line 64: tenant_id blank.
    with pytest.raises(SearchIdentityTicketError, match="empty"):
        mint_search_identity_ticket(
            SearchIdentity(
                tenant_id="",
                user_id="user-1",
                source_ids=("share-1",),
                principal_sids=("S-1-5-21-100",),
                identity_version=1,
            ),
            **base_kwargs,
        )
    # Line 64: source_ids empty.
    with pytest.raises(SearchIdentityTicketError, match="empty"):
        mint_search_identity_ticket(
            SearchIdentity(
                tenant_id="tenant-1",
                user_id="user-1",
                source_ids=(),
                principal_sids=("S-1-5-21-100",),
                identity_version=1,
            ),
            **base_kwargs,
        )
    # Line 64: principal_sids empty.
    with pytest.raises(SearchIdentityTicketError, match="empty"):
        mint_search_identity_ticket(
            SearchIdentity(
                tenant_id="tenant-1",
                user_id="user-1",
                source_ids=("share-1",),
                principal_sids=(),
                identity_version=1,
            ),
            **base_kwargs,
        )
    # Line 64: audience missing.
    with pytest.raises(SearchIdentityTicketError, match="empty"):
        mint_search_identity_ticket(
            SearchIdentity(
                tenant_id="tenant-1",
                user_id="user-1",
                source_ids=("share-1",),
                principal_sids=("S-1-5-21-100",),
                identity_version=1,
            ),
            private_key=encoded,
            audience="",
        )


@pytest.mark.asyncio
async def test_native_identity_update_creates_row_when_no_existing_mapping(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_router, "record_operator_audit", AsyncMock())
    body = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=["S-1-5-21-200"],
        group_expansion_complete=True,
        state="healthy",
        resolved_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    # First scalar: existing user lookup → user found. Second scalar: existing
    # NativeIdentityMapping → None, so the router must create a new row.
    db = _NativeIdentityDb(
        scalar_rows=(SimpleNamespace(id=user_id), None),
    )
    result = await smb_router.update_native_identity(
        str(user_id), body, SimpleNamespace(), db, admin
    )
    assert len(db.added) == 1
    created = db.added[0]
    assert created.primary_sid == "S-1-5-21-100"
    assert created.provider == "ad"
    assert result.version == 1


@pytest.mark.asyncio
async def test_native_identity_update_rejects_immutable_mismatch(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=[],
        state="pending",
        version=1,
        resolved_at=None,
        expires_at=None,
        error_code=None,
    )
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_router, "record_operator_audit", AsyncMock())
    body = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-2",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=[],
        state="pending",
    )
    db = _NativeIdentityDb(scalar_rows=(SimpleNamespace(id=user_id), existing))
    with pytest.raises(smb_router.HTTPException) as exc:
        await smb_router.update_native_identity(
            str(user_id), body, SimpleNamespace(), db, admin
        )
    assert exc.value.status_code == 409
    assert "Immutable" in exc.value.detail
    assert db.commits == 0


@pytest.mark.asyncio
async def test_native_identity_update_rejects_healthy_without_complete_group(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    now = datetime.now(timezone.utc)
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=[],
        state="pending",
        version=1,
        resolved_at=now,
        expires_at=now + timedelta(minutes=5),
        error_code=None,
    )
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_router, "record_operator_audit", AsyncMock())
    body = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=["S-1-5-21-200"],
        group_expansion_complete=False,
        state="healthy",
        resolved_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    db = _NativeIdentityDb(scalar_rows=(SimpleNamespace(id=user_id), existing))
    with pytest.raises(smb_router.HTTPException) as exc:
        await smb_router.update_native_identity(
            str(user_id), body, SimpleNamespace(), db, admin
        )
    assert exc.value.status_code == 422
    assert "group expansion" in exc.value.detail
    assert db.commits == 0

    # Stale ``expires_at`` must also block a healthy state.
    body_bad = NativeIdentityUpdate(
        provider="ad",
        directory_tenant_id="directory-1",
        object_id="object-1",
        primary_sid="S-1-5-21-100",
        effective_sids=["S-1-5-21-200"],
        group_expansion_complete=True,
        state="healthy",
        resolved_at=now,
        expires_at=now - timedelta(seconds=1),
    )
    db2 = _NativeIdentityDb(scalar_rows=(SimpleNamespace(id=user_id), existing))
    with pytest.raises(smb_router.HTTPException) as exc:
        await smb_router.update_native_identity(
            str(user_id), body_bad, SimpleNamespace(), db2, admin
        )
    assert exc.value.status_code == 422


async def _matter(db, tenant, *, owner, policy=None):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=owner.id,
        slug=f"m-{uuid.uuid4().hex[:8]}",
        matter_name="Matter",
        status="open",
        plugin_workflow_state={"security_policy": policy} if policy else None,
    )
    db.add(matter)
    await db.commit()
    return matter


async def _user(db, tenant, label):
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=label.title(),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    return user


async def _decided_one_by_one(db, tenant, user, matters):
    """What the per-matter gate would allow, for comparison."""
    allowed = set()
    for matter in matters:
        try:
            await require_matter_authorization(
                db, str(tenant.id), str(user.id), str(matter.id)
            )
        except NativeAuthorizationError:
            continue
        allowed.add(matter.id)
    return allowed


@pytest.mark.asyncio
async def test_bulk_matter_authorization_agrees_with_the_per_matter_gate(
    db_session, test_tenant, test_user
):
    """The set-based filter must never allow a matter the single gate denies."""
    other = await _user(db_session, test_tenant, "owner")
    stranger = await _user(db_session, test_tenant, "stranger")

    unrestricted = await _matter(db_session, test_tenant, owner=other)
    owned = await _matter(
        db_session, test_tenant, owner=test_user, policy={"restricted": True}
    )
    walled = await _matter(
        db_session, test_tenant, owner=other, policy={"ethical_wall": True}
    )
    explicit = await _matter(
        db_session,
        test_tenant,
        owner=other,
        policy={"restricted": True, "allowed_user_ids": [str(test_user.id)]},
    )
    assigned = await _matter(
        db_session, test_tenant, owner=other, policy={"restricted": True}
    )
    db_session.add(
        MatterAssignment(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=assigned.id,
            user_id=test_user.id,
        )
    )
    await db_session.commit()

    matters = [unrestricted, owned, walled, explicit, assigned]
    ids = [str(matter.id) for matter in matters]

    bulk = await authorized_matter_ids(
        db_session, str(test_tenant.id), str(test_user.id), ids
    )
    assert bulk == await _decided_one_by_one(
        db_session, test_tenant, test_user, matters
    )
    assert walled.id not in bulk
    assert {unrestricted.id, owned.id, explicit.id, assigned.id} <= bulk

    # Someone with no relationship to any of them keeps only the open matter.
    bulk_stranger = await authorized_matter_ids(
        db_session, str(test_tenant.id), str(stranger.id), ids
    )
    assert bulk_stranger == await _decided_one_by_one(
        db_session, test_tenant, stranger, matters
    )
    assert bulk_stranger == {unrestricted.id}


@pytest.mark.asyncio
async def test_bulk_matter_authorization_ignores_junk_and_other_tenants(
    db_session, test_tenant, test_user
):
    mine = await _matter(db_session, test_tenant, owner=test_user)
    assert await authorized_matter_ids(
        db_session,
        str(test_tenant.id),
        str(test_user.id),
        [str(mine.id), str(uuid.uuid4()), "not-a-uuid", "", str(mine.id)],
    ) == {mine.id}
    assert (
        await authorized_matter_ids(
            db_session, str(test_tenant.id), str(test_user.id), []
        )
        == set()
    )
