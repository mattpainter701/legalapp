"""Dedicated workspaces must honor the same live access state as AI skills."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.plugin import TenantPluginEntitlement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plugin,path",
    [
        ("family-law", "/api/plugins/domestic/cases"),
        ("trust-estate-legal", "/api/plugins/trust-estate/estates"),
        ("commercial-legal", "/api/plugins/commercial/renewals"),
    ],
)
@pytest.mark.parametrize(
    "status,starts,expires,expected",
    [
        ("disabled", -1, None, 403),
        ("trial", -1, -1, 402),
        ("trial", -1, None, 402),
        ("purchased", 1, None, 402),
    ],
)
async def test_workspace_rejects_inactive_entitlements(
    client, db_session, test_tenant, plugin, path, status, starts, expires, expected
):
    now = datetime.now(timezone.utc)
    row = TenantPluginEntitlement(
        id=uuid4(),
        tenant_id=test_tenant.id,
        plugin_name=plugin,
        status=status,
        starts_at=now + timedelta(days=starts),
        expires_at=now + timedelta(days=expires) if expires is not None else None,
    )
    db_session.add(row)
    await db_session.commit()
    assert (await client.get(path)).status_code == expected
    assert (await client.post(path, json={})).status_code == expected
    # Catalog and administrative reactivation remain reachable.
    assert (await client.get("/api/plugins")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,starts,expected", [("trial", -1, "expired"), ("purchased", 1, "scheduled")]
)
async def test_skill_setup_and_catalog_agree_on_inactive_entitlements(
    client, db_session, test_tenant, status, starts, expected
):
    row = TenantPluginEntitlement(
        id=uuid4(),
        tenant_id=test_tenant.id,
        plugin_name="mediation-legal",
        status=status,
        starts_at=datetime.now(timezone.utc) + timedelta(days=starts),
    )
    db_session.add(row)
    await db_session.commit()
    catalog = (await client.get("/api/plugins")).json()["plugins"]
    module = next(item for item in catalog if item["plugin_name"] == "mediation-legal")
    assert module["entitlement_status"] == expected
    assert not module["is_purchased"] and not module["is_trial"]
    assert (
        await client.post(
            "/api/plugins/mediation-legal/cold-start", json={"input_text": "Profile"}
        )
    ).status_code == 402
    assert (
        await client.post(
            "/api/plugins/mediation-legal/mediation-brief",
            json={"skill": "mediation-brief", "input_text": "Brief"},
        )
    ).status_code == 402
