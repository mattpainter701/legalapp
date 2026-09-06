"""Postgres outbox/worker integration: real transactions, no external providers."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.durable_job import DurableJob
from app.models.plugin import Matter
from app.models.configurable_workflow import MatterWorkflowRun
from app.models.task import Task
from app.services import durable_workflow_automations as outbox
from app.services import durable_job_worker as worker
from app.services import workflow_automations as planning


async def drain(db):
    ids = (
        await db.execute(
            select(DurableJob.id, DurableJob.tenant_id).where(
                DurableJob.kind == outbox.JOB_KIND,
                DurableJob.status == "pending",
            )
        )
    ).all()
    await db.commit()
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    with patch.object(worker, "async_session_maker", factory):
        for job_id, tenant_id in ids:
            await worker.process_job(job_id, tenant_id)


async def setup_rule(client, db, tenant, user):
    from tests.test_workflow_automation_dispatch import (
        active_rule,
        both_capabilities,
        restore,
    )
    from tests.test_workflow_automation_rules import approved_template

    role = await both_capabilities(db, tenant, user)
    template = await approved_template(client, role, db)
    await restore(role, db)
    rule = await active_rule(client, db, role, template)
    return role, rule


@pytest.mark.asyncio
async def test_save_queues_and_duplicate_delivery_does_not_apply(
    client, db_session, test_tenant, test_user
):
    await setup_rule(client, db_session, test_tenant, test_user)
    response = await client.post(
        "/api/matters", json={"matter_name": "Private content never in payload"}
    )
    assert response.status_code == 201
    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.kind == outbox.JOB_KIND)
    )
    job_id, tenant_id = job.id, job.tenant_id
    assert "Private content" not in str(job.payload)
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0
    activity = await client.get(
        f"/api/matters/{response.json()['id']}/workflow-automation-events"
    )
    assert activity.json()["items"][0]["outcome"] == "pending"
    await drain(db_session)
    await db_session.refresh(job)
    assert job.status == "completed"
    assert job.result["outcome"] == "planned"
    # Simulate a delivered-again receipt after the original commit.
    job.status = "pending"
    await db_session.commit()
    await drain(db_session)
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1
    assert await db_session.scalar(select(func.count(Task.id))) == 0
    assert job_id and tenant_id


@pytest.mark.asyncio
async def test_enqueue_and_matter_rollback_together(
    client, db_session, test_tenant, test_user
):
    await setup_rule(client, db_session, test_tenant, test_user)
    tenant_id, user_id = test_tenant.id, test_user.id
    matter = Matter(
        tenant_id=tenant_id, user_id=user_id, slug="rollback", matter_name="Rollback"
    )
    db_session.add(matter)
    await outbox.enqueue_matter_event(
        db_session, matter=matter, trigger_event="matter_created", actor_user_id=user_id
    )
    await db_session.rollback()
    assert await db_session.scalar(select(func.count(Matter.id))) == 0
    assert await db_session.scalar(select(func.count(DurableJob.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        ("stage", "matter_changed"),
        ("archive", "matter_archived"),
        ("actor", "actor_unavailable"),
        ("permission", "actor_permission_changed"),
        ("rule", "rule_changed"),
    ],
)
async def test_queued_context_changes_require_review(
    client, db_session, test_tenant, test_user, change, code
):
    role, rule = await setup_rule(client, db_session, test_tenant, test_user)
    response = await client.post("/api/matters", json={"matter_name": "Waiting"})
    matter = await db_session.get(Matter, uuid.UUID(response.json()["id"]))
    if change == "stage":
        matter.stage = "Different"
    elif change == "archive":
        matter.archived_at = datetime.now(timezone.utc)
    elif change == "actor":
        test_user.is_active = False
    elif change == "permission":
        role.capabilities = []
    else:
        result = await client.post(
            f"/api/workflow-config/automations/{rule['id']}/archive"
        )
        assert result.status_code == 200
    await db_session.commit()
    await drain(db_session)
    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.kind == outbox.JOB_KIND)
    )
    assert job.result["outcome"] == "blocked"
    event = await db_session.get(
        planning.MatterWorkflowAutomationEvent, uuid.UUID(job.result["event_id"])
    )
    assert event.detail_json["failure_code"] == code
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0


@pytest.mark.asyncio
async def test_crash_after_plan_rolls_back_then_retries(
    client, db_session, test_tenant, test_user, monkeypatch
):
    await setup_rule(client, db_session, test_tenant, test_user)
    await client.post("/api/matters", json={"matter_name": "Retry"})
    original = worker.finish_job

    async def crash(*args, **kwargs):
        raise RuntimeError("PRIVATE SQL PARAMETER MUST NOT BE LOGGED")

    monkeypatch.setattr(worker, "finish_job", crash)
    await drain(db_session)
    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.kind == outbox.JOB_KIND)
    )
    assert job.status == "pending" and job.attempts == 1
    assert "PRIVATE" not in job.last_error
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0
    monkeypatch.setattr(worker, "finish_job", original)
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    await drain(db_session)
    await db_session.refresh(job)
    assert job.status == "completed" and job.attempts == 2
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1


@pytest.mark.asyncio
async def test_wrong_tenant_payload_cannot_plan(
    client, db_session, test_tenant, test_user
):
    await setup_rule(client, db_session, test_tenant, test_user)
    await client.post("/api/matters", json={"matter_name": "Tenant boundary"})
    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.kind == outbox.JOB_KIND)
    )
    # The requested source is not in this tenant (unknown and foreign are identical).
    job.payload = {**job.payload, "matter_id": str(uuid.uuid4())}
    await db_session.commit()
    await drain(db_session)
    await db_session.refresh(job)
    assert job.result == {"outcome": "blocked", "failure_code": "source_unavailable"}
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0
