"""Kill a real worker after a fake provider stores bytes, then reconcile once."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from psycopg2.extras import Json
from sqlalchemy.engine import make_url
from rehearse_configurable_workflows import connect, seed_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def provider_patches(directory, *, crash=False):
    from app.services.cloud_artifact_materialization import cloud_artifact_materializer
    from app.services.matter_file_store import StorageResult, MatterFileStore
    directory = Path(directory)

    async def upload(**kwargs):
        marker = directory / "uploads.txt"
        count = int(marker.read_text()) if marker.exists() else 0
        marker.write_text(str(count + 1))
        (directory / "document.docx").write_bytes(kwargs["content"])
        if crash:
            os._exit(73)
        return StorageResult(provider="microsoft", backend="onedrive",
            provider_item_id="runtime-file", parent_id="runtime-folder", drive_id="runtime-drive")

    async def read(*args, **kwargs):
        return (directory / "document.docx").read_bytes()

    stack = ExitStack()
    stack.enter_context(patch.object(cloud_artifact_materializer, "_upload", upload))
    stack.enter_context(patch.object(cloud_artifact_materializer, "_readback", read))
    stack.enter_context(patch.object(cloud_artifact_materializer, "read_current_cloud_bytes", read))
    stack.enter_context(patch.object(MatterFileStore, "read_matter_file_bytes", read))
    return stack


async def worker_once(tenant_id, job_id, directory, *, crash=False):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.services import workflow_runtime, durable_job_worker, task_automation
    engine = create_async_engine(os.environ["RLS_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with provider_patches(directory, crash=crash), patch.object(workflow_runtime, "async_session_maker", sessions), patch.object(durable_job_worker, "async_session_maker", sessions), patch.object(task_automation, "async_session_maker", sessions):
        result = await durable_job_worker.process_job(job_id, tenant_id)
    await engine.dispose()
    return result


def main():
    if "--child" in sys.argv:
        tenant, job, directory = sys.argv[-3:]
        asyncio.run(worker_once(uuid.UUID(tenant), uuid.UUID(job), directory, crash=True))
        raise SystemExit("Worker did not reach the provider crash boundary")
    if "rehearsal" not in (make_url(os.environ["MIGRATOR_DATABASE_URL"]).database or ""):
        raise SystemExit("Use a disposable rehearsal database")
    owner = connect(os.environ["MIGRATOR_DATABASE_URL"])
    fixture = seed_fixture(owner)
    tenant, actor, matter = fixture["tenants"][0], fixture["users"][0], fixture["matters"][0]
    role = uuid.uuid4()
    grant = uuid.uuid4()
    with owner.cursor() as c:
        c.execute("UPDATE users SET is_active=true,license_active=true WHERE id=%s", (actor,))
        c.execute("INSERT INTO roles(id,tenant_id,name,capabilities) VALUES(%s,%s,'Runtime attorney',%s)",
                  (role, tenant, Json(["manage_matters", "manage_documents", "approve_legal_work"])))
        c.execute("INSERT INTO user_roles(id,tenant_id,user_id,role_id) VALUES(%s,%s,%s,%s)", (uuid.uuid4(),tenant,actor,role))
        c.execute("UPDATE matters SET attorney_of_record_id=%s,cloud_folder=%s WHERE id=%s", (actor,
            Json({"onedrive": {"drive_id": "runtime-drive", "subfolders": {"documents": "runtime-folder"}}}), matter))
        c.execute("INSERT INTO tenant_settings(id,tenant_id,artifact_review_policy,primary_cloud_provider,enable_chat_actions) VALUES(%s,%s,'attorney_only','onedrive',true)", (uuid.uuid4(),tenant))
        c.execute("""INSERT INTO workspace_mcp_grants(id,tenant_id,user_id,client_id,client_name,scopes,status,consent_version,consent_sha256,expires_at)
            VALUES(%s,%s,%s,'runtime-rehearsal','Runtime rehearsal',%s,'active','rehearsal',%s,now()+interval '1 day')""",
            (grant,tenant,actor,Json(["matters:read","tasks:read","tasks:propose","documents:read","documents:propose"]),"a"*64))
    owner.commit()

    async def setup():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.database import set_tenant_context
        from app.models.user import User
        from app.models.durable_job import DurableJob
        from app.services.automation_capabilities import CapabilityContext
        from app.services.workflow_run_contract import WorkflowRunInput
        from app.services.chat_tools.registry import resolve_tool
        engine = create_async_engine(os.environ["RLS_TEST_DATABASE_URL"])
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with sessions() as db:
            await set_tenant_context(db,str(tenant))
            user = await db.get(User,actor)
            plan = WorkflowRunInput(
                request_id=uuid.uuid4(),matter_id=matter,objective="prepare_document",
                steps=[{"step_key":"draft","capability":"propose_matter_document",
                        "arguments":{"title":"Recovery draft","body":"Private review text"}},
                       {"step_key":"follow_up","capability":"propose_task"}])
            result = await resolve_tool("propose_workflow_run").execute(CapabilityContext(db=db,user=user),plan)
            job = await db.scalar(select(DurableJob).where(DurableJob.tenant_id==tenant,DurableJob.kind=="workflow_run"))
            job_id=job.id
            await db.commit()
        await engine.dispose()
        return uuid.UUID(result["run_id"]),job_id,plan

    run_id, job_id, plan = asyncio.run(setup())
    with tempfile.TemporaryDirectory(prefix="lawhand-runtime-provider-") as directory:
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(tenant), str(job_id), directory], timeout=60, capture_output=True, text=True)
        assert child.returncode==73, child.stdout[-2000:]+child.stderr[-2000:]
        with owner.cursor() as c:
            c.execute("SELECT artifact_id,artifact_revision_id,storage_operation_id FROM workflow_run_steps WHERE run_id=%s AND position=0",(run_id,))
            artifact, revision, operation=c.fetchone()
            assert artifact and revision and operation
            c.execute("SELECT status FROM document_storage_operations WHERE id=%s",(operation,))
            assert c.fetchone()[0]=="writing"
            c.execute("UPDATE durable_jobs SET leased_at=now()-interval '16 minutes',available_at=now() WHERE id=%s",(job_id,))
        owner.commit()
        assert asyncio.run(worker_once(tenant,job_id,directory))
        with owner.cursor() as c:
            c.execute("SELECT status FROM workflow_runs WHERE id=%s",(run_id,))
            assert c.fetchone()[0]=="reconciliation_required"
        owner.rollback()
        assert (Path(directory)/"uploads.txt").read_text()=="1"

        async def reconcile_and_review():
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
            from app.database import set_tenant_context
            from app.models.user import User
            from app.models.task import Task
            from app.models.workflow_run import WorkflowRun,WorkflowRunStep
            from app.models.durable_job import DurableJob
            from app.services.automation_capabilities import CapabilityContext
            from app.services.workflow_run_contract import ResumeRunInput
            from app.services.workflow_run_ledger import describe_run,resume_run
            from app.services import workflow_run_reconciliation as reconciliation
            from app.routers.workflow_runs import CloudReconciliationRequest, reconcile_cloud
            from app.routers.tasks import review_task_as_attorney
            from app.schemas.task import TaskReviewDecisionRequest
            engine=create_async_engine(os.environ["RLS_TEST_DATABASE_URL"])
            sessions=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
            async with sessions() as db:
                await set_tenant_context(db,str(tenant))
                user=await db.get(User,actor)
                run=await db.get(WorkflowRun,run_id)
                with patch.object(reconciliation,"verify_provider_object",AsyncMock(return_value={"provider_etag":"verified","provider_version_id":"1"})):
                    await reconcile_cloud(run_id,
                        CloudReconciliationRequest(expected_version=run.version,provider_object_id="runtime-file",reason="Located original upload"), db, user)
                await db.commit()
                await set_tenant_context(db,str(tenant))
                next_job=await db.scalar(select(DurableJob.id).where(DurableJob.tenant_id==tenant,DurableJob.kind=="workflow_run",DurableJob.status=="pending"))
            assert await worker_once(tenant,next_job,directory)
            from rehearse_workflow_transport import replay_through_mcp
            mcp_result=await replay_through_mcp(sessions,tenant_id=tenant,user_id=actor,grant_id=grant,plan=plan)
            assert mcp_result["run_id"]==str(run_id)
            assert mcp_result["steps"][0]["artifact_id"]==str(artifact)
            async with sessions() as db:
                await set_tenant_context(db,str(tenant))
                run=await db.get(WorkflowRun,run_id)
                result=await describe_run(db,run)
                assert result["status"]=="awaiting_review",result
                step=await db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id==run_id,WorkflowRunStep.position==0))
                assert step.artifact_id==artifact and step.artifact_revision_id==revision
                user=await db.get(User,actor)
                task=await db.get(Task,step.task_id)
                with provider_patches(directory):
                    await review_task_as_attorney(task.id,TaskReviewDecisionRequest(expected_version=task.version,decision="approve"),user,db)
                await set_tenant_context(db,str(tenant))
                approval_job=await db.scalar(select(DurableJob.id).where(DurableJob.tenant_id==tenant,DurableJob.kind=="task_automation",DurableJob.status=="pending"))
            assert await worker_once(tenant,approval_job,directory)
            async with sessions() as db:
                await set_tenant_context(db,str(tenant))
                user=await db.get(User,actor)
                run=await db.get(WorkflowRun,run_id)
                await resume_run(CapabilityContext(db=db,user=user),run_id,ResumeRunInput(expected_version=run.version))
                await db.commit()
                await set_tenant_context(db,str(tenant))
                next_job=await db.scalar(select(DurableJob.id).where(DurableJob.tenant_id==tenant,DurableJob.kind=="workflow_run",DurableJob.status=="pending"))
            assert await worker_once(tenant,next_job,directory)
            async with sessions() as db:
                await set_tenant_context(db,str(tenant))
                run=await db.get(WorkflowRun,run_id)
                assert run.status=="awaiting_input",await describe_run(db,run)
                first_step=await db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id==run_id,WorkflowRunStep.position==0))
                assert first_step.artifact_id==artifact and first_step.approval_id
                approval_id=first_step.approval_id
                from app.services import workflow_run_ledger
                from app.services.chat_tools.registry import resolve_tool
                from app.services.workflow_run_contract import ResumeWorkflowRunInput
                class ThreeDaysLater(datetime):
                    @classmethod
                    def now(cls,tz=None): return datetime.now(tz)+timedelta(days=3)
                user=await db.get(User,actor)
                with patch.object(workflow_run_ledger,"datetime",ThreeDaysLater):
                    await resolve_tool("resume_workflow_run").execute(CapabilityContext(db=db,user=user),
                        ResumeWorkflowRunInput(run_id=run_id,expected_version=run.version,missing_arguments={"title":"Review completed draft"}))
                assert run.updated_at>datetime.now(timezone.utc)+timedelta(days=2)
                await db.commit()
                await set_tenant_context(db,str(tenant))
                next_job=await db.scalar(select(DurableJob.id).where(DurableJob.tenant_id==tenant,DurableJob.kind=="workflow_run",DurableJob.status=="pending"))
            assert await worker_once(tenant,next_job,directory)
            async with sessions() as db:
                await set_tenant_context(db,str(tenant))
                run=await db.get(WorkflowRun,run_id)
                assert run.status=="awaiting_review"
                follow_up=await db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id==run_id,WorkflowRunStep.position==1))
                task=await db.get(Task,follow_up.task_id)
                from app.services.task_workflow import transition_task
                transition_task(db,task,to_status="in_progress",actor_user_id=actor,expected_version=task.version)
                await db.commit()
                await set_tenant_context(db,str(tenant))
                user=await db.get(User,actor)
                await resume_run(CapabilityContext(db=db,user=user),run_id,ResumeRunInput(expected_version=run.version))
                await db.commit()
                await set_tenant_context(db,str(tenant))
                next_job=await db.scalar(select(DurableJob.id).where(DurableJob.tenant_id==tenant,DurableJob.kind=="workflow_run",DurableJob.status=="pending"))
            assert await worker_once(tenant,next_job,directory)
            async with sessions() as db:
                await set_tenant_context(db,str(tenant))
                run=await db.get(WorkflowRun,run_id)
                first_step=await db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id==run_id,WorkflowRunStep.position==0))
                assert run.status=="completed",await describe_run(db,run)
                assert first_step.approval_id==approval_id
            from rehearse_harness_activity import prove_harness_activity
            await prove_harness_activity(sessions, fixture, grant, run_id,
                lambda job_id: worker_once(tenant, job_id, directory))
            await engine.dispose()
        asyncio.run(reconcile_and_review())
        assert (Path(directory)/"uploads.txt").read_text()=="1"
    with owner.cursor() as c:
        c.execute("SELECT count(*) FROM work_artifact_approval WHERE artifact_id=%s AND decision='approve'",(artifact,))
        assert c.fetchone()[0]==1
    owner.close()
    print("Killed worker recovery: one cloud upload, same artifact/revision, verified reconciliation and one approval passed")


if __name__=="__main__":
    main()
