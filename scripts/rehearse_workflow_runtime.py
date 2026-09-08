"""Real migrated PostgreSQL rehearsal for bounded runtime checkpoints and review."""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

from psycopg2.extras import Json
from sqlalchemy.engine import make_url
from rehearse_configurable_workflows import connect, seed_fixture, set_tenant, expect_database_error


def main():
    if "rehearsal" not in (make_url(os.environ["MIGRATOR_DATABASE_URL"]).database or ""):
        raise SystemExit("Use a disposable rehearsal database")
    owner = connect(os.environ["MIGRATOR_DATABASE_URL"])
    runtime = connect(os.environ["RLS_TEST_DATABASE_URL"])
    with runtime.cursor() as cursor:
        cursor.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert cursor.fetchone() == (False, False)
        for table in ("workflow_runs", "workflow_run_steps", "workflow_run_events"):
            cursor.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", (table,))
            assert cursor.fetchone() == (True, True)
    runtime.rollback()
    fixture = seed_fixture(owner)
    tenant, other = fixture["tenants"]
    actor_id = fixture["users"][0]
    matter_id = fixture["matters"][0]
    role_id = uuid.uuid4()
    with owner.cursor() as cursor:
        cursor.execute("UPDATE users SET is_active=true,license_active=true WHERE id=%s", (actor_id,))
        cursor.execute("INSERT INTO roles(id,tenant_id,name,capabilities) VALUES(%s,%s,'Runtime operator',%s)",
                       (role_id, tenant, Json(["manage_matters", "manage_documents", "approve_legal_work", "admin_settings"])))
        cursor.execute("INSERT INTO user_roles(id,tenant_id,user_id,role_id) VALUES(%s,%s,%s,%s)",
                       (uuid.uuid4(), tenant, actor_id, role_id))
    owner.commit()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

    async def exercise():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.database import set_tenant_context
        from app.models.user import User
        from app.models.workflow_run import WorkflowRun
        from app.models.durable_job import DurableJob
        from app.services.automation_capabilities import CapabilityContext
        from app.services.workflow_run_contract import WorkflowRunInput, ResumeRunInput
        from app.services.workflow_run_ledger import submit_run, describe_run, resume_run
        from app.services import workflow_runtime as executor, durable_job_worker as worker
        from app.routers import workflow_runs as api
        from fastapi import HTTPException

        engine = create_async_engine(os.environ["RLS_TEST_DATABASE_URL"])
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        request = WorkflowRunInput(request_id=uuid.uuid4(), matter_id=matter_id,
            objective="coordinate_matter_work", steps=[
                {"step_key": "read", "capability": "list_matter_tasks"},
                {"step_key": "task", "capability": "propose_task"},
            ])
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            context = CapabilityContext(db=db, user=actor)
            result = await api.create_run(request, db, actor)
            await set_tenant_context(db, str(tenant))
            run_id = uuid.UUID(result["run_id"])
            repeat = await submit_run(context, request)
            assert repeat["run_id"] == str(run_id)
            listed = await api.list_runs(matter_id, 0, db, actor)
            assert any(item["run_id"] == str(run_id) for item in listed["items"])
            await db.commit()

        async def work():
            async with sessions() as db:
                await set_tenant_context(db, str(tenant))
                job = await db.scalar(select(DurableJob).where(
                    DurableJob.tenant_id == tenant, DurableJob.kind == "workflow_run",
                    DurableJob.status == "pending",
                ).order_by(DurableJob.created_at).limit(1))
                assert job
                job_id = job.id
            with patch.object(worker, "async_session_maker", sessions), patch.object(executor, "async_session_maker", sessions):
                assert await worker.process_job(job_id, tenant)
            async with sessions() as db:
                await set_tenant_context(db, str(tenant))
                job = await db.get(DurableJob, job_id)
                assert job.status == "completed", job.last_error
                run = await db.get(WorkflowRun, run_id)
                return await describe_run(db, run)

        result = await work()
        assert result["status"] == "awaiting_input", result
        assert result["steps"][0]["status"] == "completed"
        assert result["steps"][1]["required_inputs"] == ["title"]
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            try:
                await api.continue_run(run_id, ResumeRunInput(expected_version=999), db, actor)
                raise AssertionError("Stale resume accepted")
            except HTTPException as error:
                assert error.status_code == 409
            await api.continue_run(run_id,
                ResumeRunInput(expected_version=result["version"], missing_arguments={"title": "Review intake checklist"}), db, actor)
            await db.commit()
        result = await work()
        assert result["status"] == "awaiting_review", result
        assert result["steps"][0]["attempts"] == 1
        assert result["steps"][1]["task_id"]
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            cancelled = await api.stop_run(run_id, api.VersionRequest(expected_version=result["version"]), db, actor)
            assert cancelled["status"] == "cancelled"
        await engine.dispose()
        return run_id

    run_id = asyncio.run(exercise())
    set_tenant(runtime, tenant)
    expect_database_error(runtime, "UPDATE workflow_runs SET plan_sha256=%s WHERE id=%s",
                          ("0"*64, run_id), sqlstates={"P0001"}, tenant_id=tenant)
    expect_database_error(runtime, "DELETE FROM workflow_run_events WHERE run_id=%s",
                          (run_id,), sqlstates={"P0001"}, tenant_id=tenant)
    runtime.rollback()
    set_tenant(runtime, other)
    with runtime.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM workflow_runs WHERE id=%s", (run_id,))
        assert cursor.fetchone()[0] == 0
    runtime.rollback()
    runtime.close()
    owner.close()
    print("Durable workflow runtime: missing-input resume, review pause, immutable evidence and RLS passed")


if __name__ == "__main__":
    main()
