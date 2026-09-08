"""Extend the migrated cloud fixture with firm audit and runtime egress checks."""

import uuid
from unittest.mock import AsyncMock, patch


async def prove_harness_activity(sessions, fixture, grant, original_run_id, worker):
    from sqlalchemy import select
    from app.database import set_tenant_context
    from app.models.user import User
    from app.models.workflow_run import WorkflowRun, WorkflowRunStep
    from app.models.durable_job import DurableJob
    from app.routers.workspace_mcp_activity import activity
    from app.services.automation_capabilities import CapabilityContext, CapabilityError
    from app.services.workflow_run_contract import WorkflowRunInput, ResumeRunInput
    from app.services.workflow_run_ledger import resume_run
    from app.services import workspace_mcp_budgets, workspace_mcp_read_volume
    from rehearse_workflow_transport import replay_through_mcp

    tenant, actor, matter = fixture["tenants"][0], fixture["users"][0], fixture["matters"][0]
    async with sessions() as db:
        await set_tenant_context(db, str(tenant))
        user = await db.get(User, actor)
        page = await activity(before=None, grant_id=grant, limit=50, db=db, user=user)
        evidence = [item for item in page["items"] if item["run"] and item["run"]["id"] == str(original_run_id)]
        assert evidence and evidence[0]["run"]["status"] == "completed"
        assert len(evidence[0]["reviews"]) == 1
        assert evidence[0]["reviews"][0]["decision"] == "approve"
        assert evidence[0]["artifacts"][0]["status"] == "approved"
        assert "Private review text" not in str(page)
    async with sessions() as db:
        await set_tenant_context(db, str(fixture["tenants"][1]))
        user = await db.get(User, fixture["users"][1])
        page = await activity(before=None, grant_id=grant, limit=50, db=db, user=user)
        assert not page["items"]

    plan = WorkflowRunInput(request_id=uuid.uuid4(), matter_id=matter,
        objective="coordinate_matter_work", steps=[{"step_key":"inspect", "capability":"get_matter_context"}])
    proposed = await replay_through_mcp(sessions, tenant_id=tenant, user_id=actor, grant_id=grant, plan=plan)
    run_id = uuid.UUID(proposed["run_id"])

    async def pending_job():
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            return await db.scalar(select(DurableJob.id).where(
                DurableJob.tenant_id == tenant, DurableJob.kind == "workflow_run",
                DurableJob.idempotency_key.like(f"workflow-run:{run_id}:%"),
                DurableJob.status == "pending"))

    volume = AsyncMock(side_effect=CapabilityError("workflow_rate_limited", "Read volume exhausted"))
    with patch.object(workspace_mcp_budgets, "enforce_workspace_runtime_budget", AsyncMock()), \
         patch.object(workspace_mcp_read_volume, "enforce_runtime_read_volume", volume):
        assert await worker(await pending_job())
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            run = await db.get(WorkflowRun, run_id)
            step = await db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))
            assert run.origin_channel == "workspace_mcp"
            assert run.status == "blocked" and run.failure_code == "workflow_rate_limited"
            assert step.result_ciphertext is None
            assert volume.await_args.args[1] > 0
            user = await db.get(User, actor)
            await resume_run(CapabilityContext(db=db, user=user, channel="workspace_mcp", grant_id=grant,
                client_id="runtime-rehearsal", granted_scopes=frozenset({"matters:read", "tasks:propose"})),
                run_id, ResumeRunInput(expected_version=run.version))
            await db.commit()
        volume.side_effect = None
        assert await worker(await pending_job())
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            run = await db.get(WorkflowRun, run_id)
            assert run.status == "completed"
        assert volume.await_count == 2
    print("Harness activity: exact reviewed artifact, tenant isolation and MCP runtime egress pause/resume passed")
