import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.scheduler import SchedulerLog
from app.models.tenant import Tenant


@pytest.mark.asyncio
async def test_scheduler_reads_hide_other_tenant_logs(client, db_session, test_tenant):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other-scheduler-firm.example",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    now = datetime.now(timezone.utc)
    own_log = SchedulerLog(
        agent_name="renewal-watcher",
        tenant_id=test_tenant.id,
        run_at=now - timedelta(minutes=1),
        status="completed",
        summary="Own tenant summary",
    )
    foreign_log = SchedulerLog(
        agent_name="renewal-watcher",
        tenant_id=other_tenant.id,
        run_at=now,
        status="failed",
        summary="Foreign tenant summary",
        error_message="Foreign tenant secret",
    )
    db_session.add_all([own_log, foreign_log])
    await db_session.commit()

    logs_response = await client.get("/api/scheduler/logs")
    assert logs_response.status_code == 200
    logs = logs_response.json()
    assert [row["id"] for row in logs] == [str(own_log.id)]
    assert "Foreign tenant secret" not in logs_response.text

    agents_response = await client.get("/api/scheduler/agents")
    assert agents_response.status_code == 200
    renewal = next(
        row for row in agents_response.json() if row["name"] == "renewal-watcher"
    )
    assert renewal["last_run_status"] == "completed"
    assert renewal["last_run_summary"] == "Own tenant summary"
    assert "Foreign tenant summary" not in agents_response.text
