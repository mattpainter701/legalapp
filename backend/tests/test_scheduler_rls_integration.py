"""Production-like scheduler proof using the least-privilege runtime role."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import set_tenant_context
from app.main import health_readiness
from app.models.scheduler import SchedulerLog
from app.services.scheduler import LegalScheduler


@pytest.mark.asyncio
async def test_scheduler_heartbeat_is_tenant_scoped_under_runtime_rls_role(
    test_tenant, test_user
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")

    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with patch("app.services.scheduler.async_session_maker", maker):
            await LegalScheduler().run_scheduler_heartbeat()
            await LegalScheduler().run_scheduler_heartbeat()

        async with maker() as session:
            await set_tenant_context(session, str(test_tenant.id))
            logs = list(
                (
                    await session.execute(
                        select(SchedulerLog).where(
                            SchedulerLog.agent_name == "scheduler-heartbeat",
                            SchedulerLog.tenant_id == test_tenant.id,
                        )
                    )
                ).scalars()
            )
        assert logs
        assert len(logs) == 1
        assert logs[-1].status == "completed"
        assert "1 user(s) visible" in (logs[-1].summary or "")
        assert all(log.tenant_id is not None for log in logs)

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(redis=AsyncMock()))
        )
        with (
            patch("app.main.async_session_maker", maker),
            patch(
                "app.main.shutil.disk_usage",
                return_value=SimpleNamespace(total=100, used=10),
            ),
        ):
            readiness = await health_readiness(request)
        assert readiness.status_code == 200
        body = json.loads(readiness.body)
        assert body["status"] == "ok"
        assert body["components"]["scheduler"] == "ok"
        assert body["components"]["queue"] == "ok"
    finally:
        await engine.dispose()
