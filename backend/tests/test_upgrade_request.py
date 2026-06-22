import pytest
from sqlalchemy import select

from app.models.plan_upgrade import PlanUpgradeRequest


@pytest.mark.asyncio
async def test_upgrade_request_is_recorded(client, db_session, test_tenant, test_user):
    resp = await client.post(
        "/api/plan/upgrade-request",
        json={"note": "We want matters + billing", "target_plan": "full-platform"},
    )
    assert resp.status_code == 202

    rows = (
        (
            await db_session.execute(
                select(PlanUpgradeRequest).where(
                    PlanUpgradeRequest.tenant_id == test_tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].target_plan == "full-platform"
    assert rows[0].note == "We want matters + billing"
    assert rows[0].requested_by_user_id == test_user.id
