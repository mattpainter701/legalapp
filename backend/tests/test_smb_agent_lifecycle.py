from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.routers import smb as smb_router
from app.models.smb_agent import SmbAgent
from app.schemas.smb import AgentInfo, FileSyncEntry, ShareCreate, ShareUpdate
from app.services.scheduler import AGENT_REGISTRY
from app.services import smb as smb_module
from app.services.smb import SmbService
from app.services.smb_credentials import SmbCredentialError, SmbCredentialService


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def test_agent_info_exposes_registration_state_without_credentials():
    placeholder = SmbAgent(
        id=uuid4(),
        tenant_id=uuid4(),
        agent_name="pending-test",
        api_key_hash="pending",
        status="pending",
        created_at=datetime.now(timezone.utc),
        update_status="idle",
    )
    registered = SmbAgent(
        id=uuid4(),
        tenant_id=placeholder.tenant_id,
        agent_name="FS01",
        api_key_hash="a" * 64,
        status="active",
        created_at=datetime.now(timezone.utc),
        update_status="idle",
    )

    assert AgentInfo.model_validate(placeholder).is_registered is False
    assert AgentInfo.model_validate(registered).is_registered is True


def test_file_sync_accepts_legacy_empty_timestamp_sentinels():
    entry = FileSyncEntry(
        path=r"\\FS01\Legal\brief.pdf",
        filename="brief.pdf",
        modified_time="",
        created_time="",
    )
    assert entry.modified_time is None
    assert entry.created_time is None


def test_share_paths_are_canonical_and_safe_for_create_and_update():
    assert ShareCreate(share_path="//FS01/Legal/").share_path == r"\\FS01\Legal"
    assert ShareUpdate(share_path="//FS01/Legal/sub").share_path == r"\\FS01\Legal\sub"
    with pytest.raises(ValueError, match="server and share"):
        ShareCreate(share_path=r"\\FS01")
    with pytest.raises(ValueError, match="cannot contain"):
        ShareUpdate(share_path=r"\\FS01\Legal\..\Secrets")
    with pytest.raises(ValueError, match="Windows does not support"):
        ShareUpdate(share_path=r"\\FS01\Legal\bad?.txt")


@pytest.mark.asyncio
async def test_share_move_revalidates_credential_and_hides_stale_index(monkeypatch):
    tenant_id = uuid4()
    share_id = uuid4()
    old_agent_id = uuid4()
    new_agent_id = uuid4()
    credential_id = uuid4()
    share = SimpleNamespace(
        id=share_id,
        tenant_id=tenant_id,
        agent_id=old_agent_id,
        credential_id=credential_id,
        share_path=r"\\old-server\Legal",
        last_scan_at=datetime.now(timezone.utc),
        last_scan_status="success",
        last_scan_file_count=7,
        last_scan_error=None,
        last_verified_at=datetime.now(timezone.utc),
        last_verify_status="ok",
        last_verify_error=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(share),
                _ScalarResult(None),
                SimpleNamespace(rowcount=7),
            ]
        ),
        flush=AsyncMock(),
    )
    service = SmbService()
    resolve_credential = AsyncMock(return_value=credential_id)
    require_agent = AsyncMock()
    advance_revision = AsyncMock()
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(service, "_resolve_share_credential", resolve_credential)
    monkeypatch.setattr(
        smb_module.smb_credential_service,
        "require_registered_agent",
        require_agent,
    )
    monkeypatch.setattr(smb_module, "advance_rag_corpus_revision", advance_revision)

    result = await service.update_share(
        db,
        str(share_id),
        str(tenant_id),
        ShareUpdate(agent_id=str(new_agent_id), share_path="//home/share"),
    )

    assert result.agent_id == new_agent_id
    assert result.share_path == r"\\home\share"
    require_agent.assert_awaited_once_with(db, str(tenant_id), str(new_agent_id))
    resolve_credential.assert_awaited_once_with(
        db, str(tenant_id), str(new_agent_id), str(credential_id)
    )
    stale_update = db.execute.await_args_list[2].args[0]
    stale_sql = str(stale_update.compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE smb_file_index SET is_deleted=true" in stale_sql
    assert share_id.hex in stale_sql
    advance_revision.assert_awaited_once_with(db, tenant_id)
    assert result.last_scan_at is None
    assert result.last_scan_status is None
    assert result.last_scan_file_count is None
    assert result.last_verified_at is None
    assert result.last_verify_status is None
    db.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_unregistered_placeholders():
    tenant_id = uuid4()
    delete_result = SimpleNamespace(rowcount=3)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[None, delete_result]),
        commit=AsyncMock(),
    )

    deleted = await SmbService().cleanup_expired_pairing_agents(
        db,
        str(tenant_id),
        now=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert deleted == 3
    db.commit.assert_awaited_once_with()
    statement = db.execute.await_args_list[1].args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "api_key_hash = 'pending'" in sql
    assert "pairing_expires_at" in sql
    assert "tenant_id" in sql
    assert "status = 'pending'" in sql
    assert "status = 'revoked'" in sql
    for table in ("smb_shares", "smb_credentials", "smb_file_index", "smb_access_log"):
        assert f"EXISTS (SELECT {table}" in sql


@pytest.mark.asyncio
async def test_revoke_deletes_only_never_registered_placeholder(monkeypatch):
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    delete_placeholder = AsyncMock(return_value=True)
    monkeypatch.setattr(
        smb_router.smb_service,
        "delete_pairing_placeholder_if_empty",
        delete_placeholder,
    )
    placeholder = SimpleNamespace(api_key_hash="pending")
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(placeholder)),
        commit=AsyncMock(),
    )

    result = await smb_router.revoke_agent(
        str(uuid4()),
        request=None,
        db=db,
        admin=SimpleNamespace(tenant_id=uuid4()),
    )

    assert result["status"] == "deleted"
    delete_placeholder.assert_awaited_once()
    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_revoke_retains_legacy_placeholder_with_related_state(monkeypatch):
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        smb_router.smb_service,
        "delete_pairing_placeholder_if_empty",
        AsyncMock(return_value=False),
    )
    placeholder = SimpleNamespace(
        api_key_hash="pending",
        status="pending",
        pairing_code="legacy-code",
        pairing_expires_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(placeholder)),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )

    result = await smb_router.revoke_agent(
        str(uuid4()),
        request=None,
        db=db,
        admin=SimpleNamespace(tenant_id=uuid4()),
    )

    assert result["status"] == "revoked"
    assert placeholder.status == "revoked"
    assert placeholder.pairing_code is None
    assert placeholder.pairing_expires_at is None
    db.flush.assert_awaited_once_with()
    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_revoke_preserves_registered_agent_audit_row(monkeypatch):
    monkeypatch.setattr(smb_router, "set_tenant_context", AsyncMock())
    registered = SimpleNamespace(
        api_key_hash="hashed-device-key",
        status="active",
        pairing_code="legacy-code",
        pairing_expires_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(registered)),
        delete=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )

    result = await smb_router.revoke_agent(
        str(uuid4()),
        request=None,
        db=db,
        admin=SimpleNamespace(tenant_id=uuid4()),
    )

    assert result["status"] == "revoked"
    assert registered.status == "revoked"
    assert registered.pairing_code is None
    assert registered.pairing_expires_at is None
    db.delete.assert_not_awaited()
    db.flush.assert_awaited_once_with()
    db.commit.assert_awaited_once_with()


def test_pairing_cleanup_is_registered_for_operations():
    metadata = {agent["name"]: agent for agent in AGENT_REGISTRY}
    assert metadata["smb-pairing-cleanup"]["schedule"] == "Every 10 minutes"


@pytest.mark.asyncio
async def test_agent_assignments_require_a_registered_operational_agent():
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))

    with pytest.raises(SmbCredentialError, match="Registered agent"):
        await SmbCredentialService().require_registered_agent(
            db, str(uuid4()), str(uuid4())
        )

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "status IN ('active', 'paused')" in sql
    assert "api_key_hash != 'pending'" in sql
