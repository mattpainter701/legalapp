import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.rbac import Role, UserRole
from app.models.user import User
from app.models.tenant import Tenant, TenantSettings
from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.models.workspace_mcp_audit import WorkspaceMCPAuditEvent
from app.routers import admin as admin_router
from app.routers import workspace_mcp_oauth as workspace_mcp_router
from app.schemas.admin import TenantSettingsUpdate
from app.services import email as email_module


async def _assign_role(db_session, tenant_id, user_id, name, capabilities):
    role = Role(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        capabilities=capabilities,
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            user_id=user_id,
            role_id=role.id,
            tenant_id=tenant_id,
            source="manual",
        )
    )
    return role


def test_workspace_mcp_tenant_gate_rejects_explicit_null():
    with pytest.raises(ValidationError, match="must be true or false"):
        TenantSettingsUpdate(workspace_mcp_enabled=None)

    assert "workspace_mcp_enabled" not in TenantSettingsUpdate().model_dump(
        exclude_unset=True
    )


@pytest.mark.asyncio
async def test_admin_users_returns_billing_and_role_assignment_fields(
    client, db_session, test_tenant
):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="billing-user@testfirm.com",
        full_name="Billing User",
        role="user",
        is_active=True,
        license_active=False,
        default_billing_rate=Decimal("250.00"),
        payg_monthly_budget=Decimal("500.00"),
    )
    db_session.add(user)
    await db_session.flush()
    role = await _assign_role(
        db_session,
        test_tenant.id,
        user.id,
        "Billing Manager",
        ["view_billing", "manage_billing"],
    )
    await db_session.commit()

    resp = await client.get("/api/admin/users")

    assert resp.status_code == 200
    payload = resp.json()["users"]
    row = next(u for u in payload if u["id"] == str(user.id))
    assert row["role"] == "user"
    assert row["license_active"] is False
    assert row["default_billing_rate"] == 250.0
    assert row["payg_monthly_budget"] == 500.0
    assert row["role_ids"] == [str(role.id)]
    assert row["roles"] == [{"id": str(role.id), "name": "Billing Manager"}]
    assert row["workspace_mcp_enabled"] is True
    assert row["privacy_mode"] is False


@pytest.mark.asyncio
async def test_admin_users_include_active_workspace_mcp_summary_and_detail_route(
    client, db_session, test_tenant, test_user
):
    active = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="claude-desktop",
        client_name="Claude",
        scopes=["tasks:read", "matters:read"],
        consent_version="v1",
        consent_sha256="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        last_used_at=datetime.now(timezone.utc),
    )
    research = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="research.claude-desktop",
        client_name="Claude Research",
        scopes=["research:read"],
        consent_version="research-v1",
        consent_sha256="r" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    expired = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="old-codex",
        client_name="Old Codex",
        scopes=["matters:read"],
        consent_version="v1",
        consent_sha256="e" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add_all([active, research, expired])
    await db_session.commit()

    listed = await client.get("/api/admin/users")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["users"] if item["id"] == str(test_user.id))
    assert row["workspace_mcp_active_grant_count"] == 1

    detail = await client.get(f"/api/admin/users/{test_user.id}/workspace-mcp/grants")
    assert detail.status_code == 200, detail.text
    assert [item["id"] for item in detail.json()["items"]] == [str(active.id)]


@pytest.mark.asyncio
async def test_admin_can_revoke_user_workspace_mcp_grant_with_audit_and_scope(
    client, db_session, test_tenant, test_user, monkeypatch
):
    runtime_cleanup = AsyncMock()
    monkeypatch.setattr(
        "app.services.workspace_mcp_oauth.revoke_workspace_grant_runtime",
        runtime_cleanup,
    )
    grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="chatgpt",
        client_name="ChatGPT",
        scopes=["matters:read"],
        consent_version="v1",
        consent_sha256="b" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    research = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="research.chatgpt",
        client_name="ChatGPT Research",
        scopes=["research:read"],
        consent_version="research-v1",
        consent_sha256="s" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add_all([grant, research])
    await db_session.commit()

    response = await client.post(
        f"/api/admin/users/{test_user.id}/workspace-mcp/grants/{grant.id}/revoke",
        json={"reason": "Offboarding"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(grant.id), "status": "revoked"}
    retry = await client.post(
        f"/api/admin/users/{test_user.id}/workspace-mcp/grants/{grant.id}/revoke",
        json={"reason": "Retry runtime cleanup"},
    )
    assert retry.status_code == 200, retry.text
    assert runtime_cleanup.await_count == 2
    await db_session.refresh(grant)
    assert grant.revocation_reason == "Offboarding"
    assert grant.revoked_by_user_id == test_user.id
    assert await db_session.scalar(
        select(func.count()).select_from(WorkspaceMCPAuditEvent).where(
            WorkspaceMCPAuditEvent.grant_id == grant.id,
            WorkspaceMCPAuditEvent.event_type == "grant_revoked",
        )
    ) == 1
    await db_session.refresh(research)
    assert research.status == "active"


@pytest.mark.asyncio
async def test_admin_workspace_mcp_grant_routes_fail_closed_across_tenants(
    client, db_session, test_tenant, test_user
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other MCP Firm",
        domain="other-mcp-firm.example",
        billing_tier="payg",
        is_active=True,
    )
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="other-mcp-user@example.test",
        role="user",
        is_active=True,
    )
    foreign_grant = WorkspaceMCPGrant(
        tenant_id=other_tenant.id,
        user_id=other_user.id,
        client_id="claude-other-tenant",
        client_name="Claude Other Tenant",
        scopes=["matters:read"],
        consent_version="v1",
        consent_sha256="c" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add_all([other_tenant, other_user, foreign_grant])
    await db_session.commit()

    detail = await client.get(
        f"/api/admin/users/{other_user.id}/workspace-mcp/grants"
    )
    assert detail.status_code == 404

    revoke = await client.post(
        f"/api/admin/users/{test_user.id}/workspace-mcp/grants/{foreign_grant.id}/revoke",
        json={"reason": "Must stay isolated"},
    )
    assert revoke.status_code == 404
    await db_session.refresh(foreign_grant)
    assert foreign_grant.status == "active"


@pytest.mark.asyncio
async def test_admin_disabling_workspace_mcp_revokes_active_grants(
    client, db_session, test_tenant
):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="connected-user@testfirm.com",
        role="user",
        is_active=True,
        workspace_mcp_enabled=True,
    )
    db_session.add(user)
    await db_session.flush()
    grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=user.id,
        client_id="claude-desktop",
        client_name="Claude",
        scopes=["matters:read"],
        consent_version="v1",
        consent_sha256="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    research_grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=user.id,
        client_id="research.claude-desktop",
        client_name="Claude Research",
        scopes=["research:read"],
        consent_version="research-mcp-v1",
        consent_sha256="r" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add_all([grant, research_grant])
    await db_session.commit()

    response = await client.patch(
        f"/api/admin/users/{user.id}", json={"workspace_mcp_enabled": False}
    )

    assert response.status_code == 200, response.text
    assert response.json()["workspace_mcp_enabled"] is False
    await db_session.refresh(grant)
    assert grant.status == "revoked"
    assert grant.revoked_at is not None
    assert grant.revocation_reason == "Workspace MCP disabled by tenant administrator"
    await db_session.refresh(research_grant)
    assert research_grant.status == "active"
    assert research_grant.revoked_at is None


@pytest.mark.asyncio
async def test_admin_mcp_overview_reports_effective_users_and_tenant_gate(
    client, db_session, test_tenant, test_user
):
    db_session.add(
        TenantSettings(tenant_id=test_tenant.id, workspace_mcp_enabled=False)
    )
    test_user.workspace_mcp_enabled = True
    test_user.license_active = True
    await db_session.commit()

    response = await client.get("/api/admin/mcp")

    assert response.status_code == 200, response.text
    users = response.json()["workspace"]["users"]
    assert users["configured_enabled"] == 1
    assert users["enabled"] == 0
    assert response.json()["workspace"]["tenant_enabled"] is False


@pytest.mark.asyncio
async def test_admin_mcp_overview_publishes_workspace_setup_and_catalog(
    client, db_session, test_tenant, test_user, monkeypatch
):
    db_session.add(TenantSettings(tenant_id=test_tenant.id, workspace_mcp_enabled=True))
    test_user.workspace_mcp_enabled = True
    test_user.license_active = True
    test_user.privacy_mode = False
    db_session.add_all(
        [
            WorkspaceMCPGrant(
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                client_id="claude-desktop",
                client_name="Claude",
                scopes=["matters:read"],
                consent_version="workspace-mcp-v1",
                consent_sha256="w" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            ),
            WorkspaceMCPGrant(
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                client_id="research.claude-desktop",
                client_name="Claude Research",
                scopes=["research:read"],
                consent_version="research-mcp-v1",
                consent_sha256="r" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            ),
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(admin_router.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        admin_router.settings,
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        "https://mcp.getlawhand.com/api/mcp/workspace",
    )

    response = await client.get("/api/admin/mcp")

    assert response.status_code == 200, response.text
    workspace = response.json()["workspace"]
    assert workspace["official_url"] == ("https://mcp.getlawhand.com/api/mcp/workspace")
    assert workspace["shorthand"] == "https://mcp.getlawhand.com"
    assert workspace["users"]["enabled"] == 1
    assert workspace["active_grants"] == 1
    catalog = {tool["name"]: tool for tool in workspace["tools"]}
    assert catalog["find_matter"]["effect"] == "read"
    assert catalog["propose_task"]["effect"] == "propose"


@pytest.mark.asyncio
async def test_workspace_grant_routes_do_not_expose_or_revoke_research_grants(
    client, db_session, test_tenant, test_user, monkeypatch
):
    monkeypatch.setattr(workspace_mcp_router.settings, "WORKSPACE_MCP_ENABLED", True)
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    workspace_grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="claude-desktop",
        client_name="Claude",
        scopes=["matters:read"],
        consent_version="workspace-mcp-v1",
        consent_sha256="w" * 64,
        expires_at=expires_at,
    )
    research_grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="research.claude-desktop",
        client_name="Claude Research",
        scopes=["research:read"],
        consent_version="research-mcp-v1",
        consent_sha256="r" * 64,
        expires_at=expires_at,
    )
    db_session.add_all([workspace_grant, research_grant])
    await db_session.commit()

    listed = await client.get("/api/workspace-mcp/grants")

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [str(workspace_grant.id)]

    revoked = await client.post(
        f"/api/workspace-mcp/grants/{research_grant.id}/revoke",
        json={"reason": "Wrong product"},
    )

    assert revoked.status_code == 404, revoked.text
    await db_session.refresh(research_grant)
    assert research_grant.status == "active"
    assert research_grant.revoked_at is None


@pytest.mark.asyncio
async def test_admin_mcp_disable_revokes_grants_and_reenable_does_not_restore(
    client, db_session, test_tenant, test_user
):
    settings_row = TenantSettings(tenant_id=test_tenant.id)
    grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="claude-desktop",
        client_name="Claude",
        scopes=["matters:read"],
        consent_version="v1",
        consent_sha256="b" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    research_grant = WorkspaceMCPGrant(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        client_id="research.claude-desktop",
        client_name="Claude Research",
        scopes=["research:read"],
        consent_version="research-mcp-v1",
        consent_sha256="r" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add_all([settings_row, grant, research_grant])
    await db_session.commit()

    response = await client.put(
        "/api/admin/settings", json={"workspace_mcp_enabled": False}
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(grant)
    assert grant.status == "revoked"
    assert grant.revoked_at is not None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(WorkspaceMCPAuditEvent)
            .where(WorkspaceMCPAuditEvent.grant_id == grant.id)
        )
    ) == 1
    await db_session.refresh(research_grant)
    assert research_grant.status == "active"
    assert research_grant.revoked_at is None

    response = await client.put(
        "/api/admin/settings", json={"workspace_mcp_enabled": True}
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(grant)
    assert grant.status == "revoked"


@pytest.mark.asyncio
async def test_deactivate_admin_settings_holder_when_legacy_admin_remains(
    client, db_session, test_tenant
):
    target = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="settings-admin@testfirm.com",
        role="user",
        is_active=True,
    )
    db_session.add(target)
    await db_session.flush()
    await _assign_role(
        db_session,
        test_tenant.id,
        target.id,
        "Settings Admin",
        ["admin_settings"],
    )
    await db_session.commit()

    resp = await client.delete(f"/api/admin/users/{target.id}")

    assert resp.status_code == 204
    await db_session.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_deactivate_legacy_admin_when_another_legacy_admin_remains(
    client, db_session, test_tenant
):
    target = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="legacy-admin@testfirm.com",
        role="admin",
        is_active=True,
    )
    db_session.add(target)
    await db_session.commit()

    resp = await client.delete(f"/api/admin/users/{target.id}")

    assert resp.status_code == 204
    await db_session.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_patch_blocks_self_demoting_last_effective_admin(client, test_user):
    resp = await client.patch(f"/api/admin/users/{test_user.id}", json={"role": "user"})

    assert resp.status_code == 400
    assert "last active admin" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_accepts_accountant_role(client, db_session, test_tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="accountant-user@testfirm.com",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.patch(
        f"/api/admin/users/{user.id}", json={"role": "accountant"}
    )

    assert resp.status_code == 200
    assert resp.json()["role"] == "accountant"


@pytest.mark.asyncio
async def test_invite_rejects_invalid_role(client):
    resp = await client.post(
        "/api/admin/users/invite",
        json={"email": "bad-role@testfirm.com", "role": "owner"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "role must be 'admin', 'accountant', or 'user'"


@pytest.mark.asyncio
async def test_invite_user_reports_email_failure_but_preserves_inactive_record(
    client, db_session, test_tenant, monkeypatch
):
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    response = await client.post(
        "/api/admin/users/invite",
        json={
            "email": "new-colleague@testfirm.com",
            "full_name": "New Colleague",
            "role": "user",
        },
    )

    assert response.status_code == 503
    assert "outbound email is unavailable" in response.json()["detail"]
    invited_user = await db_session.scalar(
        select(User).where(
            User.tenant_id == test_tenant.id,
            User.email == "new-colleague@testfirm.com",
        )
    )
    assert invited_user is not None
    assert invited_user.is_active is False
    assert invited_user.password_hash.startswith("invite:")
