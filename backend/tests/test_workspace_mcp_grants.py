from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.services.workspace_mcp_grants import (
    WorkspaceMCPGrantError,
    require_active_workspace_grant,
)


class ScalarDB:
    def __init__(self, value):
        self.value = value

    async def scalar(self, _statement):
        return self.value


def _grant(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "client_id": "claude-desktop",
        "client_name": "Claude Desktop",
        "scopes": ["matters:read", "tasks:read"],
        "status": "active",
        "consent_version": "v1",
        "consent_sha256": "a" * 64,
        "expires_at": now + timedelta(days=30),
        "revoked_at": None,
        "last_used_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(
        **values,
        is_active=lambda moment: (
            values["status"] == "active"
            and values["revoked_at"] is None
            and values["expires_at"] > moment
        ),
        scope_set=frozenset(values["scopes"]),
    )


@pytest.mark.asyncio
async def test_access_token_requires_matching_active_database_grant():
    grant = _grant()
    used_at = datetime.now(timezone.utc)

    resolved = await require_active_workspace_grant(
        ScalarDB(grant),
        grant_id=str(grant.id),
        tenant_id=grant.tenant_id,
        user_id=grant.user_id,
        client_id=grant.client_id,
        token_scopes=frozenset({"matters:read"}),
        now=used_at,
    )

    assert resolved is grant
    assert grant.last_used_at == used_at


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["tenant_id", "user_id", "client_id"])
async def test_grant_identity_mismatches_fail_closed(mismatch):
    grant = _grant()
    identity = {
        "tenant_id": grant.tenant_id,
        "user_id": grant.user_id,
        "client_id": grant.client_id,
    }
    identity[mismatch] = "different-client" if mismatch == "client_id" else uuid4()

    with pytest.raises(WorkspaceMCPGrantError):
        await require_active_workspace_grant(
            ScalarDB(grant),
            grant_id=str(grant.id),
            token_scopes=frozenset({"matters:read"}),
            **identity,
        )


@pytest.mark.asyncio
async def test_token_scopes_cannot_exceed_persisted_consent():
    grant = _grant(scopes=["matters:read"])

    with pytest.raises(WorkspaceMCPGrantError, match="exceeds"):
        await require_active_workspace_grant(
            ScalarDB(grant),
            grant_id=str(grant.id),
            tenant_id=grant.tenant_id,
            user_id=grant.user_id,
            client_id=grant.client_id,
            token_scopes=frozenset({"matters:read", "documents:read"}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grant",
    [
        _grant(status="revoked", revoked_at=datetime.now(timezone.utc)),
        _grant(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
    ],
)
async def test_revoked_or_expired_grants_fail_closed(grant):
    with pytest.raises(WorkspaceMCPGrantError, match="unavailable"):
        await require_active_workspace_grant(
            ScalarDB(grant),
            grant_id=str(grant.id),
            tenant_id=grant.tenant_id,
            user_id=grant.user_id,
            client_id=grant.client_id,
            token_scopes=frozenset({"matters:read"}),
        )


def test_grant_model_scope_normalization_and_expiry():
    grant = WorkspaceMCPGrant(
        tenant_id=uuid4(),
        user_id=uuid4(),
        client_id="codex",
        client_name="Codex",
        scopes=[" matters:read ", "", 4, "tasks:read"],
        consent_version="v1",
        consent_sha256="b" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        status="active",
        revoked_at=None,
    )

    assert grant.scope_set == frozenset({"matters:read", "tasks:read"})
    assert grant.is_active()
