import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.scheduler import SchedulerLog
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.services.scheduler import LegalScheduler

# Use the database owner account so DDL and DML operate on the same user.
DB_URL = os.environ.get(
    "TEST_DB_URL", "postgresql+asyncpg://legalapp:legalapp@localhost:5432/legalapp_test"
)


@pytest.mark.asyncio
async def test_user_sync_isolates_tenant_failure():
    """run_user_sync must complete even when one tenant's credentials are bad."""
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
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
            session.add(good)
            session.add(bad)
            for t in (good, bad):
                session.add(
                    TenantCredential(
                        tenant_id=t.id,
                        provider="microsoft",
                        encrypted_access_token="enc",
                        scopes="User.Read.All",
                        is_active=True,
                    )
                )
            await session.commit()

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
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await engine.dispose()
