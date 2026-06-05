import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.scheduler import SchedulerLog
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.services.scheduler import LegalScheduler


@pytest.mark.asyncio
async def test_user_sync_isolates_tenant_failure(db_session: AsyncSession, test_engine):
    """run_user_sync must complete even when one tenant's credentials are bad."""
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    good = Tenant(
        id=uuid.uuid4(),
        name="Good Firm",
        domain="goodfirm.com",
        billing_tier="payg",
        is_active=True,
    )
    bad = Tenant(
        id=uuid.uuid4(),
        name="Bad Firm",
        domain="badsyncfirm.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(good)
    db_session.add(bad)
    await db_session.flush()

    for t in (good, bad):
        db_session.add(
            TenantCredential(
                tenant_id=t.id,
                provider="microsoft",
                encrypted_access_token="enc",
                scopes="User.Read.All",
                is_active=True,
            )
        )
    await db_session.commit()

    async def fake_ms(db, tenant_id):
        if tenant_id == str(bad.id):
            raise RuntimeError("expired token")
        return {"created": 0, "updated": 1, "skipped": 0, "total": 1}

    with (
        patch("app.services.scheduler.async_session_maker", maker),
        patch(
            "app.services.user_sync.user_sync.sync_microsoft_users",
            new=AsyncMock(side_effect=fake_ms),
        ),
    ):
        await LegalScheduler().run_user_sync()

    # Run completed despite one tenant failing
    async with maker() as session:
        log = (
            (
                await session.execute(
                    select(SchedulerLog)
                    .where(SchedulerLog.agent_name == "user-sync")
                    .order_by(SchedulerLog.run_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert log is not None
        assert log.status == "completed"

        # Failing tenant's credential recorded a failure
        bad_cred = (
            await session.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == bad.id,
                    TenantCredential.provider == "microsoft",
                )
            )
        ).scalar_one()
        assert bad_cred.last_user_sync_status == "failed"
        assert "expired token" in (bad_cred.last_user_sync_error or "")
