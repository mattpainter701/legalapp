"""Task Work Board API, concurrency, history, and privacy behavior."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.task import Task, TaskEvent
from app.models.tenant import TenantSettings
from app.models.user import User


@pytest.mark.asyncio
async def test_board_transition_history_and_stale_conflict(
    client, db_session, test_tenant, test_user
):
    task = Task(
        tenant_id=test_tenant.id,
        title="Prepare summary judgment exhibits",
        description="Privileged strategy notes must not appear on a collapsed card.",
        task_type="filing",
        priority="urgent",
        due_date=date.today() + timedelta(days=2),
        assigned_to_user_id=test_user.id,
        created_by_user_id=test_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    board = await client.get("/api/tasks/board", params={"scope": "mine"})
    assert board.status_code == 200, board.text
    body = board.json()
    assert [column["status"] for column in body["columns"]] == [
        "pending",
        "in_progress",
        "waiting",
        "review",
        "completed",
    ]
    card = body["columns"][0]["items"][0]
    assert card["title"] == task.title
    assert "description" not in card
    initial_version = card["version"]

    no_reason = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "waiting", "expected_version": initial_version},
    )
    assert no_reason.status_code == 422

    moved = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "waiting",
            "expected_version": initial_version,
            "reason": "Waiting for certified medical records",
            "waiting_follow_up_date": (date.today() + timedelta(days=4)).isoformat(),
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "waiting"
    assert moved.json()["version"] == initial_version + 1

    stale = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "review", "expected_version": initial_version},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_task"]["status"] == "waiting"

    history = await client.get(f"/api/tasks/{task.id}/events")
    assert history.status_code == 200
    assert history.json()["items"][0]["to_status"] == "waiting"
    assert history.json()["items"][0]["note"] == "Waiting for certified medical records"


@pytest.mark.asyncio
async def test_firm_board_risk_counts_and_reviewer_labels(
    client, db_session, test_tenant, test_user
):
    reviewer = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="reviewer@testfirm.com",
        full_name="Riley Reviewer",
        role="attorney",
        is_active=True,
    )
    overdue = Task(
        tenant_id=test_tenant.id,
        title="Serve overdue responses",
        status="review",
        reviewer_user_id=reviewer.id,
        assigned_to_user_id=test_user.id,
        due_date=date.today() - timedelta(days=1),
        priority="high",
    )
    unassigned = Task(
        tenant_id=test_tenant.id,
        title="Assign new intake",
        status="pending",
        due_date=date.today(),
    )
    waiting = Task(
        tenant_id=test_tenant.id,
        title="Check court order",
        status="waiting",
        waiting_reason="Court ruling",
        waiting_follow_up_date=date.today(),
    )
    db_session.add_all([reviewer, overdue, unassigned, waiting])
    await db_session.commit()

    response = await client.get("/api/tasks/board", params={"scope": "firm"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["risk_counts"] == {
        "overdue": 1,
        "due_today": 1,
        "unassigned": 2,
        "waiting_follow_up_due": 1,
    }
    review_column = next(c for c in body["columns"] if c["status"] == "review")
    assert review_column["items"][0]["reviewer"]["label"] == "Riley Reviewer"


@pytest.mark.asyncio
async def test_cross_tenant_reviewer_is_rejected_without_disclosure(
    client, db_session, test_tenant, test_user
):
    from app.models.tenant import Tenant

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other-task-board.test",
        billing_tier="payg",
        is_active=True,
    )
    foreign_reviewer = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="reviewer@other-task-board.test",
        role="attorney",
        is_active=True,
    )
    task = Task(
        tenant_id=test_tenant.id,
        title="Internal review",
        assigned_to_user_id=test_user.id,
    )
    db_session.add_all([other_tenant, foreign_reviewer, task])
    await db_session.commit()
    await db_session.refresh(task)

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "review",
            "expected_version": task.version,
            "reviewer_user_id": str(foreign_reviewer.id),
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Reviewer not found"
    assert (
        await db_session.scalar(select(TaskEvent).where(TaskEvent.task_id == task.id))
        is None
    )


@pytest.mark.asyncio
async def test_tenant_can_disable_board_without_disabling_task_list(
    client, db_session, test_tenant
):
    settings = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
    )
    if settings is None:
        settings = TenantSettings(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            enable_task_board=False,
        )
        db_session.add(settings)
    else:
        settings.enable_task_board = False
    await db_session.commit()

    config = await client.get("/api/tasks/board/config")
    assert config.status_code == 200
    assert config.json()["enabled"] is False

    board = await client.get("/api/tasks/board")
    assert board.status_code == 404
    assert board.json()["detail"] == "The Work Board is disabled"

    task_list = await client.get("/api/tasks")
    assert task_list.status_code == 200
