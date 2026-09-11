"""Cloud sync must keep writing under its tenant after an OAuth refresh commits.

Production symptom (IONOS, 2026-09-11): the scheduler's OneDrive sync logged
``invalid input syntax for type uuid: ""`` or ``new row violates row-level
security policy`` once or twice an hour and upserted nothing. Resolving an
expired token commits the session so token_vault can persist the refreshed
token, and that commit clears the transaction-local tenant setting. The next
metadata write then ran with no tenant. ``_sync_graph_files`` swallowed the
error, so the scheduler still recorded a successful run with zero items.

This proof drives the real refresh commit under the least-privilege runtime
role, where RLS is enforced exactly as it is in production.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import set_tenant_context
from app.models.cloud_metadata import CloudMetadata
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.services import token_vault
from app.services.cloud_sync import CloudSyncService
from app.services.token_vault import decrypt_token, encrypt_token

RLS_URL = os.getenv("RLS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not RLS_URL,
    reason="RLS_TEST_DATABASE_URL is required for runtime-role integration",
)

GRAPH_ITEM = {
    "id": "graph-item-1",
    "name": "Engagement Letter.pdf",
    "file": {"mimeType": "application/pdf"},
    "size": 1024,
    "lastModifiedDateTime": "2026-09-01T12:00:00Z",
    "createdDateTime": "2026-09-01T12:00:00Z",
    "webUrl": "https://example.test/engagement-letter",
    "parentReference": {"id": "graph-parent-1", "path": "/drive/root:"},
    "createdBy": {"user": {"email": "attorney@testfirm.com"}},
}


class _GraphClient:
    """Stands in for httpx.AsyncClient on the Graph children listing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url, **_kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"value": [GRAPH_ITEM]})


async def _successful_refresh(_token_url, **_request_kwargs):
    return SimpleNamespace(
        status_code=200,
        text="",
        json=lambda: {"access_token": "refreshed-graph-token", "expires_in": 3600},
    )


@pytest.mark.asyncio
async def test_onedrive_sync_writes_under_its_tenant_after_token_refresh(
    db_session, test_tenant, monkeypatch
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="otherfirm.example",
        billing_tier="payg",
        is_active=True,
    )
    credential = TenantCredential(
        tenant_id=test_tenant.id,
        provider="microsoft",
        encrypted_access_token=encrypt_token("expired-graph-token"),
        encrypted_refresh_token=encrypt_token("graph-refresh-token"),
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        is_active=True,
    )
    db_session.add_all([other_tenant, credential])
    await db_session.commit()
    tenant_id = test_tenant.id
    other_tenant_id = other_tenant.id
    credential_id = credential.id

    monkeypatch.setattr(token_vault, "_post_token_with_retry", _successful_refresh)
    monkeypatch.setattr(
        "app.services.cloud_sync.httpx.AsyncClient", lambda *_a, **_k: _GraphClient()
    )

    engine = create_async_engine(RLS_URL, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await set_tenant_context(session, str(tenant_id))
            synced = await CloudSyncService().sync_onedrive(session, str(tenant_id))

        # The expired token really was refreshed, so the commit that drops the
        # tenant setting really ran before the metadata write.
        async with maker() as session:
            await set_tenant_context(session, str(tenant_id))
            stored_token = await session.scalar(
                select(TenantCredential.encrypted_access_token).where(
                    TenantCredential.id == credential_id
                )
            )
        assert decrypt_token(stored_token) == "refreshed-graph-token"

        assert synced == 1
        async with maker() as session:
            await set_tenant_context(session, str(tenant_id))
            rows = (
                await session.execute(
                    select(CloudMetadata.tenant_id, CloudMetadata.object_id)
                )
            ).all()
        assert [tuple(row) for row in rows] == [(tenant_id, "graph-item-1")]

        # Isolation: another tenant sees none of the synced metadata.
        async with maker() as session:
            await set_tenant_context(session, str(other_tenant_id))
            visible = (await session.execute(select(CloudMetadata.id))).all()
        assert visible == []
    finally:
        await engine.dispose()
