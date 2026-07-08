import uuid

import pytest
from sqlalchemy import select, text

from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter, MatterEvent


@pytest.mark.asyncio
async def test_create_matter_rebinds_tenant_context_before_post_commit_refresh(
    client, db_session, test_tenant, test_user, monkeypatch
):
    original_refresh = db_session.refresh
    checked = {}

    async def assert_scoped_matter_refresh(instance, *args, **kwargs):
        if isinstance(instance, Matter):
            current_tenant = (
                await db_session.execute(
                    text("SELECT current_setting('app.current_tenant_id', true)")
                )
            ).scalar_one()
            assert current_tenant == str(test_tenant.id)
            checked["matter_refresh"] = True
        return await original_refresh(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "refresh", assert_scoped_matter_refresh)

    resp = await client.post(
        "/api/matters",
        json={
            "matter_name": "RLS Refresh Matter",
            "description": "Created through the matter API regression path.",
            "practice_area": "family",
        },
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert checked["matter_refresh"] is True
    assert data["matter_name"] == "RLS Refresh Matter"
    assert data["assignments"][0]["user_id"] == str(test_user.id)
    assert data["assignments"][0]["role"] == "lead_attorney"

    matter_id = uuid.UUID(data["id"])
    matter = await db_session.get(Matter, matter_id)
    assert matter.tenant_id == test_tenant.id
    assert matter.user_id == test_user.id

    assignment = (
        await db_session.execute(
            select(MatterAssignment).where(MatterAssignment.matter_id == matter_id)
        )
    ).scalar_one()
    assert assignment.user_id == test_user.id
    assert assignment.is_primary is True

    event = (
        await db_session.execute(
            select(MatterEvent).where(MatterEvent.matter_id == matter_id)
        )
    ).scalar_one()
    assert event.event_type == "intake"
