import pytest
from sqlalchemy import select

from app.models.tenant import TenantSettings


@pytest.mark.asyncio
async def test_integration_readiness_is_redacted(client):
    resp = await client.get("/api/admin/integrations/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data["env"]["MICROSOFT_CLIENT_ID"].keys()) == {"configured"}
    assert (
        "https://getlawhand.com/api/integrations/microsoft/callback"
        in data["expected_redirect_uris"]["microsoft"]
        or any(
            uri.endswith("/api/integrations/microsoft/callback")
            for uri in data["expected_redirect_uris"]["microsoft"]
        )
    )
    assert "web.redirectUris" in data["entra_verification_command"]


@pytest.mark.asyncio
async def test_sharepoint_binding_persists_and_marks_primary(
    client, db_session, test_tenant
):
    resp = await client.put(
        "/api/admin/sharepoint/binding",
        json={
            "site_id": "contoso.sharepoint.com,site-collection,site-id",
            "site_web_url": "https://contoso.sharepoint.com/sites/legal",
            "drive_id": "drive-123",
            "drive_name": "Documents",
            "root_item_id": "root",
            "folder_path": "/",
            "is_primary": True,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["binding"]["drive_id"] == "drive-123"

    get_resp = await client.get("/api/admin/sharepoint/binding")
    assert get_resp.status_code == 200
    assert get_resp.json()["binding"]["site_id"].startswith("contoso")

    result = await db_session.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
    )
    row = result.scalar_one()
    assert row.primary_cloud_provider == "sharepoint"
    assert row.custom_config["sharepoint_binding"]["drive_name"] == "Documents"
