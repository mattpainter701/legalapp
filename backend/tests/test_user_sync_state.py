import pytest
from sqlalchemy import select

from app.models.tenant_credential import TenantCredential


@pytest.mark.asyncio
async def test_tenant_credential_has_sync_state_columns(db_session, test_tenant):
    cred = TenantCredential(
        tenant_id=test_tenant.id,
        provider="google",
        encrypted_access_token="enc",
        scopes="https://www.googleapis.com/auth/admin.directory.user.readonly",
        is_active=True,
        last_user_sync_total=3,
        last_user_sync_created=2,
        last_user_sync_updated=1,
        last_user_sync_status="ok",
    )
    db_session.add(cred)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == test_tenant.id,
                TenantCredential.provider == "google",
            )
        )
    ).scalar_one()
    assert row.last_user_sync_total == 3
    assert row.last_user_sync_status == "ok"
    assert row.last_user_sync_at is None
    assert row.last_user_sync_error is None
