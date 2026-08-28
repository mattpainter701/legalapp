import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.demo_session import DemoSession
from app.models.tenant import Tenant
from app.routers import platform
from tests.platform_auth_helpers import platform_headers


def _demo_tenant(*, tenant_id: uuid.UUID, expires_at: datetime, active: bool = True):
    return Tenant(
        id=tenant_id,
        name="LawHand Demo Workspace",
        domain=f"demo-{tenant_id.hex[:16]}.demo.invalid",
        billing_tier="demo",
        is_active=active,
        expires_at=expires_at,
    )


def _demo_session(
    *,
    tenant_id: uuid.UUID,
    fixture_id: uuid.UUID,
    session_id: uuid.UUID,
    expires_at: datetime,
    name: str,
    status: str = "active",
):
    return DemoSession(
        id=session_id,
        tenant_id=tenant_id,
        fixture_tenant_id=fixture_id,
        fixture_version="platform-demo-test",
        prospect_name=name,
        prospect_email=f"{name.lower()}@example.invalid",
        status=status,
        quota=20,
        used=3,
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_platform_lists_demo_capacity_without_counting_expired_or_regular_tenants(
    client, db_session, test_tenant, monkeypatch
):
    now = datetime.now(timezone.utc)
    fixture_id = uuid.uuid4()
    active_id = uuid.uuid4()
    expired_id = uuid.uuid4()
    active_session_id = uuid.uuid4()
    expired_session_id = uuid.uuid4()
    monkeypatch.setattr(platform.settings, "DEMO_MAX_ACTIVE", 10)

    db_session.add(
        Tenant(
            id=fixture_id,
            name="Synthetic Fixture",
            domain="demo-fixture.example.invalid",
            billing_tier="payg",
            is_active=True,
        )
    )
    db_session.add_all(
        [
            _demo_tenant(
                tenant_id=active_id,
                expires_at=now + timedelta(hours=2),
            ),
            _demo_tenant(
                tenant_id=expired_id,
                expires_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            _demo_session(
                tenant_id=active_id,
                fixture_id=fixture_id,
                session_id=active_session_id,
                expires_at=now + timedelta(hours=2),
                name="Active Prospect",
            ),
            _demo_session(
                tenant_id=expired_id,
                fixture_id=fixture_id,
                session_id=expired_session_id,
                expires_at=now - timedelta(minutes=1),
                name="Expired Prospect",
            ),
        ]
    )
    await db_session.commit()

    response = await client.get(
        "/api/platform/demo-workspaces",
        headers=platform_headers(["platform:read"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["capacity"] == {"limit": 10, "active": 1, "available": 9}
    assert {row["tenant_id"] for row in payload["workspaces"]} == {
        str(active_id),
        str(expired_id),
    }
    rows = {row["tenant_id"]: row for row in payload["workspaces"]}
    assert rows[str(active_id)]["counts_toward_capacity"] is True
    assert rows[str(active_id)]["session_id"] == str(active_session_id)
    assert rows[str(expired_id)]["counts_toward_capacity"] is False
    assert rows[str(expired_id)]["status"] == "expired"
    assert str(test_tenant.id) not in rows


@pytest.mark.asyncio
async def test_platform_tenant_inventory_identifies_demos_and_their_expiration(
    client, db_session, test_tenant
):
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    demo = _demo_tenant(tenant_id=uuid.uuid4(), expires_at=expires_at)
    db_session.add(demo)
    await db_session.commit()

    response = await client.get(
        "/api/platform/tenants", headers=platform_headers(["platform:read"])
    )

    assert response.status_code == 200
    tenants = {row["id"]: row for row in response.json()["tenants"]}
    assert tenants[str(demo.id)]["tenant_type"] == "demo"
    assert tenants[str(demo.id)]["expires_at"] == expires_at.isoformat()
    assert tenants[str(test_tenant.id)]["tenant_type"] == "platform"
    assert tenants[str(test_tenant.id)]["expires_at"] is None


@pytest.mark.asyncio
async def test_platform_termination_requires_write_scope_and_both_workspace_ids(
    client, monkeypatch
):
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    calls = []

    async def fake_terminate(db, selected_tenant_id, selected_session_id, **kwargs):
        calls.append((selected_tenant_id, selected_session_id, kwargs))
        return {"users": 1, "documents": 2}

    monkeypatch.setattr(platform, "terminate_demo_workspace", fake_terminate)
    path = f"/api/platform/demo-workspaces/{tenant_id}/terminate"
    body = {
        "session_id": str(session_id),
        "reason": "Demo completed",
    }

    denied = await client.post(
        path,
        json=body,
        headers=platform_headers(["platform:read"]),
    )
    assert denied.status_code == 403
    assert calls == []

    response = await client.post(
        path,
        json=body,
        headers=platform_headers(["platform:write"]),
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "terminated",
        "tenant_id": str(tenant_id),
        "session_id": str(session_id),
        "deleted_rows": 3,
    }
    assert calls == [
        (
            tenant_id,
            session_id,
            {
                "actor_id": "test-operator",
                "reason": "Demo completed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_platform_termination_returns_conflict_when_guard_refuses(
    client, monkeypatch
):
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def refuse(*args, **kwargs):
        raise platform.DemoPurgeRefused("Demo workspace is still provisioning")

    monkeypatch.setattr(platform, "terminate_demo_workspace", refuse)
    response = await client.post(
        f"/api/platform/demo-workspaces/{tenant_id}/terminate",
        json={"session_id": str(session_id)},
        headers=platform_headers(["platform:write"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Demo workspace is still provisioning"


@pytest.mark.asyncio
async def test_generic_tenant_controls_cannot_interrupt_disposable_demo(
    client, db_session
):
    tenant_id = uuid.uuid4()
    demo = _demo_tenant(
        tenant_id=tenant_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db_session.add(demo)
    await db_session.commit()

    response = await client.put(
        f"/api/platform/tenants/{tenant_id}",
        json={"is_active": False},
        headers=platform_headers(["platform:write"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Use the demo workspace panel to terminate disposable demos"
    )
    await db_session.refresh(demo)
    assert demo.is_active is True
