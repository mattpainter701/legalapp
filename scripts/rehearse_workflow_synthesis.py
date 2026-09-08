"""Exercise history synthesis, exact approval and import isolation on migrated PG."""

import asyncio
import io
import json
import os
import sys
import uuid
import zipfile
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

from psycopg2.extras import Json
from sqlalchemy.engine import make_url

from rehearse_configurable_workflows import (
    connect,
    seed_fixture,
    set_tenant,
    expect_database_error as expect_rejected,
)


def main():
    if "rehearsal" not in (
        make_url(os.environ["MIGRATOR_DATABASE_URL"]).database or ""
    ):
        raise SystemExit("Use a disposable rehearsal database")
    owner = connect(os.environ["MIGRATOR_DATABASE_URL"])
    runtime = connect(os.environ["RLS_TEST_DATABASE_URL"])
    with runtime.cursor() as cursor:
        cursor.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert cursor.fetchone() == (False, False), "Use a production-shaped runtime role"
        cursor.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='workflow_configuration_proposals'::regclass")
        assert cursor.fetchone() == (True, True)
    runtime.rollback()
    fixture = seed_fixture(owner)
    tenant, other = fixture["tenants"]
    actor_id = fixture["users"][0]
    task_ids = []
    with owner.cursor() as c:
        c.execute(
            "UPDATE users SET is_active=true,license_active=true WHERE id=%s",
            (actor_id,),
        )
        role = uuid.uuid4()
        c.execute(
            "INSERT INTO roles(id,tenant_id,name,capabilities) VALUES(%s,%s,'Synthesis reviewer',%s)",
            (
                role,
                tenant,
                Json(
                    [
                        "manage_matters",
                        "manage_workflows",
                        "approve_legal_work",
                        "admin_settings",
                    ]
                ),
            ),
        )
        c.execute(
            "INSERT INTO user_roles(id,tenant_id,user_id,role_id) VALUES(%s,%s,%s,%s)",
            (uuid.uuid4(), tenant, actor_id, role),
        )
        for index in range(3):
            matter, task = uuid.uuid4(), uuid.uuid4()
            task_ids.append(task)
            c.execute(
                """INSERT INTO matters(id,tenant_id,user_id,slug,matter_name,matter_type,created_at)
                       VALUES(%s,%s,%s,%s,'Private matter','probate',now()-interval '20 days')""",
                (matter, tenant, actor_id, str(matter)),
            )
            c.execute(
                """INSERT INTO tasks(id,tenant_id,matter_id,title,task_type,due_date,created_at,
                       assigned_to_user_id,created_by_user_id) VALUES(%s,%s,%s,'Request inventory','review',
                       current_date-10,now()-interval '15 days',%s,%s)""",
                (task, tenant, matter, actor_id, actor_id),
            )
            c.execute(
                """INSERT INTO communication_logs(id,tenant_id,matter_id,channel,direction,status,subject,summary,occurred_at)
                       VALUES(%s,%s,%s,'email','outbound','sent','Private subject','Private body excluded',now()-interval '12 days')""",
                (uuid.uuid4(), tenant, matter),
            )
    owner.commit()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

    async def exercise():
        from fastapi import HTTPException, UploadFile
        from sqlalchemy import select, text
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.database import set_tenant_context
        from app.models.user import User
        from app.models.durable_job import DurableJob
        from app.models.workflow_configuration_proposal import (
            WorkflowConfigurationProposal,
        )
        from app.models.workflow_automation import MatterWorkflowAutomationRule
        from app.routers import workflow_synthesis as api
        from app.routers.external_imports import upload_tabs3_bundle
        from app.routers.configurable_workflows import approve_workflow_template_version
        from app.routers.workflow_automations import activate_automation_rule
        from app.schemas.workflow_automation import WorkflowAutomationActivateRequest
        from app.services import durable_job_worker as worker

        engine = create_async_engine(
            os.environ["RLS_TEST_DATABASE_URL"], pool_pre_ping=True
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        async def analyze(*, crash=False):
            async with sessions() as db:
                await set_tenant_context(db, str(tenant))
                actor = await db.get(User, actor_id)
                result = await api.analyze(
                    api.AnalyzeRequest(request_id=uuid.uuid4()), db, actor
                )
                job_id = uuid.UUID(result["id"])
                repeat = await api.analyze(
                    api.AnalyzeRequest(request_id=uuid.uuid4()), db, actor
                )
                assert repeat["id"] == str(
                    job_id
                ), "Repeated clicks must share outstanding analysis"
            with patch.object(worker, "async_session_maker", sessions):
                if crash:
                    with patch.object(
                        worker,
                        "finish_job",
                        AsyncMock(side_effect=RuntimeError("private source")),
                    ):
                        await worker.process_job(job_id, tenant)
                    with owner.cursor() as c:
                        c.execute(
                            "SELECT count(*) FROM workflow_configuration_proposals WHERE tenant_id=%s",
                            (tenant,),
                        )
                        assert (
                            c.fetchone()[0] == 0
                        ), "Crash cannot leave partial drafts/evidence"
                        c.execute(
                            "SELECT last_error FROM durable_jobs WHERE id=%s", (job_id,)
                        )
                        assert "private source" not in c.fetchone()[0]
                        c.execute(
                            "UPDATE durable_jobs SET available_at=now() WHERE id=%s",
                            (job_id,),
                        )
                    owner.commit()
                assert await worker.process_job(job_id, tenant)
                assert not await worker.process_job(
                    job_id, tenant
                ), "Completed replay must be inert"
            async with sessions() as db:
                await set_tenant_context(db, str(tenant))
                job = await db.get(DurableJob, job_id)
                assert job.status == "completed", job.last_error
                return job.result

        first = await analyze(crash=True)
        assert first["outcome"] == "drafts_prepared", first
        assert len(first["proposal_ids"]) == 1
        proposal_id = uuid.UUID(first["proposal_ids"][0])
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            proposal = await db.get(WorkflowConfigurationProposal, proposal_id)
            assert "Private body" not in json.dumps(proposal.evidence_json)
            listing = await api.list_proposals(0, db, actor)
            assert listing["items"][0]["version_status"] == "draft"
            rule = await db.get(MatterWorkflowAutomationRule, proposal.rule_id)
            assert rule.status == "draft"
            template_id, version_id, rule_id = (
                proposal.template_id,
                proposal.template_version_id,
                proposal.rule_id,
            )
            rule_sha = rule.definition_sha256
            await approve_workflow_template_version(template_id, version_id, db, actor)
            await activate_automation_rule(
                rule_id,
                WorkflowAutomationActivateRequest(
                    definition_sha256=rule_sha, confirm_activate=True
                ),
                db,
                actor,
            )
        unchanged = await analyze()
        assert unchanged["outcome"] == "no_new_patterns", unchanged
        # Existing approved workflow stays intact until an exact amendment is approved.
        with owner.cursor() as c:
            c.execute(
                "UPDATE tasks SET due_date=current_date-5 WHERE id=ANY(%s)", (task_ids,)
            )
        owner.commit()
        drift = await analyze()
        assert len(drift["proposal_ids"]) == 1, drift
        amendment_id = uuid.UUID(drift["proposal_ids"][0])
        assert not (await analyze())[
            "proposal_ids"
        ], "Only one pending amendment per pattern"
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            amendment = await db.get(WorkflowConfigurationProposal, amendment_id)
            assert amendment.baseline_json["version_id"] == str(version_id)
            assert amendment.rule_id == rule_id
            await api.decline(
                amendment_id,
                api.DeclineRequest(
                    expected_proposal_sha256=amendment.proposal_sha256,
                    reason="Keep the existing ten-day standard",
                ),
                db,
                actor,
            )
            declined_version = amendment.template_version_id
            try:
                await approve_workflow_template_version(template_id, declined_version, db, actor)
            except HTTPException as error:
                assert error.status_code == 409
            else:
                raise AssertionError("Human API must report a declined amendment as a conflict")
        suppressed = await analyze()
        assert suppressed["suppressed_patterns"] == 1 and not suppressed["proposal_ids"]
        set_tenant(runtime, tenant)
        expect_rejected(
            runtime,
            "UPDATE workflow_configuration_proposals SET evidence_json='{}' WHERE id=%s",
            (proposal_id,),
            tenant_id=tenant,
            sqlstates={"P0001"},
        )
        expect_rejected(
            runtime,
            "UPDATE matter_workflow_template_versions SET status='approved',approved_by_user_id=%s,approved_at=now() WHERE id=%s",
            (actor_id, declined_version),
            tenant_id=tenant,
            sqlstates={"P0001"},
        )
        expect_rejected(
            runtime,
            "DELETE FROM workflow_configuration_proposals WHERE id=%s",
            (proposal_id,),
            tenant_id=tenant,
            sqlstates={"P0001"},
        )
        set_tenant(runtime, other)
        with runtime.cursor() as c:
            c.execute(
                "SELECT count(*) FROM workflow_configuration_proposals WHERE tenant_id=%s",
                (tenant,),
            )
            assert c.fetchone()[0] == 0
        runtime.rollback()

        task_csv = b"Matter,Task,Due,Notes\nA,Prepare closing,08/12/2026,private\nB,Prepare closing,08/12/2026,private\nC,Prepare closing,08/12/2026,private\n"
        matter_csv = b"Key,Opened,Type\nA,08/01/2026,transaction\nB,08/01/2026,transaction\nC,08/01/2026,transaction\n"
        mapping = json.dumps(
            {
                "tasks": {"matter_key": "Matter", "title": "Task", "due_date": "Due"},
                "matters": {
                    "matter_key": "Key",
                    "opened_at": "Opened",
                    "matter_type": "Type",
                },
            }
        )

        def uploads():
            return UploadFile(io.BytesIO(task_csv), filename="tasks.csv"), UploadFile(
                io.BytesIO(matter_csv), filename="matters.csv"
            )

        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            preview = await api.preview_history(*uploads(), actor)
            assert preview["task_rows"] == 3 and preview["matter_rows"] == 3
            imported = await api.upload_history(
                *uploads(), "clio", mapping, preview["fingerprint"], db, actor
            )
            repeat = await api.upload_history(
                *uploads(), "clio", mapping, preview["fingerprint"], db, actor
            )
            assert (
                repeat["reused"]
                and repeat["import_run_id"] == imported["import_run_id"]
            )
            try:
                await api.upload_history(
                    *uploads(), "clio", mapping, "0" * 64, db, actor
                )
            except HTTPException as error:
                assert error.status_code == 409
            else:
                raise AssertionError("Changed file fingerprint must fail closed")
        with patch.object(worker, "async_session_maker", sessions):
            assert await worker.process_job(uuid.UUID(imported["job_id"]), tenant)
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            job = await db.get(DurableJob, uuid.UUID(imported["job_id"]))
            assert job.status == "completed", job.last_error
            assert len(job.result["proposal_ids"]) == 1, job.result
            saved = (
                (
                    await db.execute(
                        text(
                            "SELECT row_data FROM external_raw_rows WHERE import_run_id=:run"
                        ),
                        {"run": uuid.UUID(imported["import_run_id"])},
                    )
                )
                .scalars()
                .all()
            )
            assert "private" not in json.dumps(saved) and "Notes" not in json.dumps(
                saved
            )
            proposal = await db.get(
                WorkflowConfigurationProposal, uuid.UUID(job.result["proposal_ids"][0])
            )
            actor = await db.get(User, actor_id)
            await api.decline(
                proposal.id,
                api.DeclineRequest(
                    expected_proposal_sha256=proposal.proposal_sha256,
                    reason="Use our reviewed transaction workflow",
                ),
                db,
                actor,
            )
            await set_tenant_context(db, str(tenant))
            archived = await db.get(MatterWorkflowAutomationRule, proposal.rule_id)
            assert archived.status == "archived"
        # The existing encrypted/ZIP Tabs3 migration entry point automatically
        # queues the same analyzer, using documented PracticeMaster fields.
        tables = {
            "CMCLIENT": [
                {
                    "Client_ID": str(n),
                    "Date_Open": "2026-08-01",
                    "Category": 42,
                    "Name": f"Client {n}",
                }
                for n in range(3)
            ],
            "CMCAT": [{"Category_Number": 42, "Description": "litigation"}],
            "CMCAL": [
                {
                    "Client_ID": str(n),
                    "Desc": "Prepare disclosure",
                    "Due_Date": "2026-08-09",
                    "Private": 0,
                    "_SEQUENCE_NO": n,
                }
                for n in range(3)
            ],
        }
        bundle = io.BytesIO()
        manifest = dict(
            export_version="tabs3-export-v1",
            provider="tabs3",
            source_system="tabs3_odbc",
            export_id=str(uuid.uuid4()),
            dsn="SynthesisRehearsal",
            tables=[],
        )
        with zipfile.ZipFile(bundle, "w") as archive:
            for name, records in tables.items():
                content = "".join(json.dumps(row) + "\n" for row in records).encode()
                path = f"tables/{name}.ndjson"
                archive.writestr(path, content)
                manifest["tables"].append(
                    dict(
                        name=name,
                        format="ndjson",
                        path=path,
                        row_count=len(records),
                        sha256=hashlib.sha256(content).hexdigest(),
                    )
                )
            archive.writestr("manifest.json", json.dumps(manifest))
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            actor = await db.get(User, actor_id)
            run = await upload_tabs3_bundle(
                UploadFile(io.BytesIO(bundle.getvalue()), filename="tabs3.zip"),
                None,
                "tabs3_reference",
                actor,
                db,
            )
            await set_tenant_context(db, str(tenant))
            job = await db.scalar(
                select(DurableJob).where(
                    DurableJob.tenant_id == tenant,
                    DurableJob.kind == "workflow_configuration_synthesis",
                    DurableJob.idempotency_key == f"synthesis:{run.id}",
                )
            )
            tabs3_job_id = job.id
        with patch.object(worker, "async_session_maker", sessions):
            assert await worker.process_job(tabs3_job_id, tenant)
        async with sessions() as db:
            await set_tenant_context(db, str(tenant))
            job = await db.get(DurableJob, tabs3_job_id)
            assert job.status == "completed", job.last_error
            assert len(job.result["proposal_ids"]) == 1, job.result
            proposal = await db.get(
                WorkflowConfigurationProposal, uuid.UUID(job.result["proposal_ids"][0])
            )
            assert (
                proposal.configuration_json["rule"]["match_matter_type"] == "litigation"
            )
            assert (
                len(proposal.evidence_json["items"][0]["evidence_refs"][0]["context"])
                == 2
            )
        await engine.dispose()
        return {
            "native_draft": True,
            "human_approval": True,
            "drift_amendment": True,
            "decline_suppression": True,
            "crash_recovery": True,
            "mapped_clio_import": True,
            "tabs3_onboarding": True,
            "tenant_isolation": True,
        }

    result = asyncio.run(exercise())
    print(json.dumps(result, sort_keys=True))
    runtime.close()
    owner.close()


if __name__ == "__main__":
    main()
