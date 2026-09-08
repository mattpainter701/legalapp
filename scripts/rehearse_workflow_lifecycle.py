"""Exercise lifecycle outbox capture on a migrated disposable database."""

import json
import os
import uuid
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from psycopg2.extras import Json

from rehearse_configurable_workflows import connect, seed_fixture, set_tenant
from sqlalchemy.engine import make_url


EVENTS = (
    "task_completed",
    "document_received",
    "intake_submitted",
    "esign_completed",
    "deadline_approaching",
    "invoice_overdue",
    "inbound_email_matched_to_matter",
    "payment_received",
)
TABLES = (
    "tasks",
    "matter_documents",
    "matter_intakes",
    "intake_submissions",
    "leads",
    "signature_requests",
    "inbound_emails",
    "payments",
)


def main():
    if "rehearsal" not in (
        make_url(os.environ["MIGRATOR_DATABASE_URL"]).database or ""
    ):
        raise SystemExit("Only a disposable rehearsal database is supported")
    owner = connect(os.environ["MIGRATOR_DATABASE_URL"])
    runtime = connect(os.environ["RLS_TEST_DATABASE_URL"])
    fixture = seed_fixture(owner)
    tenant, other = fixture["tenants"]
    user, matter, task = fixture["users"][0], fixture["matters"][0], fixture["tasks"][0]
    with owner.cursor() as c:
        role = uuid.uuid4()
        c.execute(
            "UPDATE users SET is_active=true,license_active=true WHERE id=%s", (user,)
        )
        c.execute(
            "INSERT INTO roles(id,tenant_id,name,capabilities) VALUES (%s,%s,'Lifecycle approver',%s)",
            (
                role,
                tenant,
                Json(["manage_matters", "manage_workflows", "approve_legal_work"]),
            ),
        )
        c.execute(
            "INSERT INTO user_roles(id,tenant_id,user_id,role_id) VALUES (%s,%s,%s,%s)",
            (uuid.uuid4(), tenant, user, role),
        )
        for table in TABLES:
            c.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid=%s::regclass AND tgname='workflow_lifecycle_capture' AND tgenabled='O' AND tgdeferrable AND tginitdeferred",
                (table,),
            )
            assert c.fetchone()[0] == 1, f"Missing transactional capture on {table}"
        for event in EVENTS:
            c.execute(
                """INSERT INTO matter_workflow_automation_rules
              (id,tenant_id,name,trigger_event,template_id,status,definition_sha256,
               created_by_user_id,activated_by_user_id,activated_at)
              VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s,now())""",
                (
                    uuid.uuid4(),
                    tenant,
                    event,
                    event,
                    fixture["records"][0]["template"],
                    "a" * 64,
                    user,
                    user,
                ),
            )
    owner.commit()
    set_tenant(runtime, tenant)
    with runtime.cursor() as c:
        c.execute(
            "UPDATE tasks SET status='completed',completed_at=now() WHERE id=%s",
            (task,),
        )
    runtime.rollback()
    with runtime.cursor() as c:
        c.execute(
            "SELECT count(*) FROM durable_jobs WHERE tenant_id=%s AND kind='workflow_lifecycle_plan'",
            (tenant,),
        )
        assert c.fetchone()[0] == 0, "Rolled-back source must not leave a job"
        c.execute(
            "UPDATE tasks SET status='completed',completed_at=now() WHERE id=%s",
            (task,),
        )
    runtime.commit()
    with runtime.cursor() as c:
        c.execute("UPDATE tasks SET title='Unrelated edit' WHERE id=%s", (task,))
        document, signature, invoice, payment, deadline = [
            uuid.uuid4() for _ in range(5)
        ]
        c.execute(
            "INSERT INTO matter_documents(id,tenant_id,matter_id,filename,storage_state,document_sha256) VALUES (%s,%s,%s,'Received.pdf','verified',%s)",
            (document, tenant, matter, "b" * 64),
        )
        c.execute(
            "INSERT INTO signature_requests(id,tenant_id,matter_id,status,completed_at) VALUES (%s,%s,%s,'completed',now())",
            (signature, tenant, matter),
        )
        c.execute(
            "INSERT INTO invoices(id,tenant_id,matter_id,invoice_number,status,issue_date,due_date,subtotal,total,created_by) VALUES (%s,%s,%s,%s,'sent',current_date-10,current_date-1,100,100,%s)",
            (invoice, tenant, matter, str(invoice), user),
        )
        c.execute(
            "INSERT INTO payments(id,tenant_id,invoice_id,amount,payment_date,method) VALUES (%s,%s,%s,10,current_date,'check')",
            (payment, tenant, invoice),
        )
        c.execute(
            "INSERT INTO tasks(id,tenant_id,matter_id,title,task_type,due_date) VALUES (%s,%s,%s,'Due soon','deadline',current_date+2)",
            (deadline, tenant, matter),
        )
        alias, email, form, lead, submission, invite, packet = [
            uuid.uuid4() for _ in range(7)
        ]
        c.execute(
            "INSERT INTO inbound_email_aliases(id,tenant_id,matter_id,token_hash,encrypted_local_part) VALUES (%s,%s,%s,%s,'fixture')",
            (alias, tenant, matter, alias.hex * 2),
        )
        c.execute(
            "INSERT INTO inbound_emails(id,tenant_id,alias_id,matter_id,envelope_sender,recipient,subject,message_sha256,raw_size,occurred_at,status) VALUES (%s,%s,%s,%s,'sender@example.invalid','inbound@example.invalid','Private subject',%s,100,now(),'accepted')",
            (email, tenant, alias, matter, email.hex * 2),
        )
        c.execute(
            "INSERT INTO intake_forms(id,tenant_id,slug,name) VALUES (%s,%s,%s,'Intake')",
            (form, tenant, form.hex),
        )
        c.execute(
            "INSERT INTO leads(id,tenant_id,contact_id) VALUES (%s,%s,%s)",
            (lead, tenant, fixture["contacts"][0]),
        )
        c.execute(
            "INSERT INTO intake_submissions(id,tenant_id,form_id,lead_id,idempotency_key,answers) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                submission,
                tenant,
                form,
                lead,
                submission.hex,
                Json({"private": "answer"}),
            ),
        )
        c.execute(
            "SELECT count(*) FROM durable_jobs WHERE payload->>'source_id'=%s",
            (str(submission),),
        )
        assert c.fetchone()[0] == 0, "Unconverted public intake has no matter workflow"
        c.execute("UPDATE leads SET matter_id=%s WHERE id=%s", (matter, lead))
        c.execute(
            "INSERT INTO client_portal_invites(id,tenant_id,matter_id,token_hash,expires_at) VALUES (%s,%s,%s,%s,now()+interval '1 day')",
            (invite, tenant, matter, invite.hex * 2),
        )
        c.execute(
            "INSERT INTO matter_intakes(id,tenant_id,matter_id,contact_id,owner_id,created_by,signature_id,invite_id,encrypted_invite,status,config,requirements,answers,delivery) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'fixture','awaiting_documents','{}','{}','{}','{}')",
            (
                packet,
                tenant,
                matter,
                fixture["contacts"][0],
                user,
                user,
                signature,
                invite,
            ),
        )
        c.execute(
            "UPDATE matter_intakes SET requirements=%s,answers=%s WHERE id=%s",
            (
                Json({"questionnaire": {"completed": True}}),
                Json({"private": "answer"}),
                packet,
            ),
        )
        for event, kind, source in [
            ("deadline_approaching", "task", deadline),
            ("invoice_overdue", "invoice", invoice),
        ]:
            source_table = "tasks" if kind == "task" else "invoices"
            c.execute(
                f"SELECT due_date::text FROM {source_table} WHERE id=%s", (source,)
            )
            due_date = c.fetchone()[0]
            for _ in range(2):
                c.execute(
                    "SELECT capture_workflow_lifecycle_event(%s,%s,%s,%s,%s,%s)",
                    (tenant, matter, event, kind, source, due_date),
                )
        # Deferred capture sees final transaction facts, including later writes
        # that finish a signature's evidence or a document's provider binding.
        c.execute(
            "UPDATE signature_requests SET evidence_sha256=%s WHERE id=%s",
            ("e" * 64, signature),
        )
        runtime.commit()
        c.execute(
            "SELECT payload FROM durable_jobs WHERE tenant_id=%s AND kind='workflow_lifecycle_plan'",
            (tenant,),
        )
        payloads = [row[0] for row in c.fetchall()]
        assert sorted(p["trigger_event"] for p in payloads) == sorted(
            [*EVENTS, "intake_submitted"]
        )
        assert "Private subject" not in json.dumps(
            payloads
        ) and "answer" not in json.dumps(payloads)
        for p in payloads:
            assert len(p["context_sha256"]) == 64 and p["template_version_id"]
            assert set(p) == {
                "rule_id",
                "matter_id",
                "actor_user_id",
                "trigger_event",
                "source_kind",
                "source_id",
                "context_sha256",
                "rule_sha256",
                "template_version_id",
                "as_of",
                "dedupe_key",
            }
            c.execute(
                "SELECT workflow_lifecycle_fingerprint(%s,%s,%s,%s,%s)",
                (tenant, matter, p["source_kind"], p["source_id"], p["trigger_event"]),
            )
            assert c.fetchone()[0] == p["context_sha256"]
        c.execute("UPDATE tasks SET status='pending' WHERE id=%s", (task,))
        c.execute(
            "SELECT workflow_lifecycle_fingerprint(%s,%s,'task',%s,'task_completed')",
            (tenant, matter, task),
        )
        assert c.fetchone()[0] is None
    runtime.commit()
    set_tenant(runtime, other)
    with runtime.cursor() as c:
        c.execute("SELECT count(*) FROM durable_jobs WHERE tenant_id=%s", (tenant,))
        assert c.fetchone()[0] == 0
        c.execute(
            "SELECT capture_workflow_lifecycle_event(%s,%s,'document_received','document',%s,'forged')",
            (tenant, matter, document),
        )
        assert c.fetchone()[0] == 0
    runtime.commit()
    asyncio.run(rehearse_worker(tenant, task))
    print(
        json.dumps(
            {
                "lifecycle_capture": "passed",
                "events": 8,
                "sources": 9,
                "rollback": True,
                "dedupe": True,
                "tenant_isolation": True,
                "worker_plans_only": True,
            }
        )
    )


async def rehearse_worker(tenant, task):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import app.models  # noqa: F401
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import set_tenant_context
    from app.services import durable_job_worker as worker
    from app.services.workflow_lifecycle import enqueue_due_events_for_tenant

    url = make_url(os.environ["RLS_TEST_DATABASE_URL"]).set(
        drivername="postgresql+asyncpg"
    )
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await set_tenant_context(db, str(tenant))
        await enqueue_due_events_for_tenant(db, tenant)
        await enqueue_due_events_for_tenant(db, tenant)
        jobs = (
            (
                await db.execute(
                    text(
                        "SELECT id FROM durable_jobs WHERE tenant_id=:tenant AND kind='workflow_lifecycle_plan' ORDER BY id"
                    ),
                    {"tenant": tenant},
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 9, "Scheduler scan must reuse source occurrence keys"
        initial_runs = await db.scalar(
            text("SELECT count(*) FROM matter_workflow_runs WHERE tenant_id=:tenant"),
            {"tenant": tenant},
        )
        initial_tasks = await db.scalar(
            text("SELECT count(*) FROM tasks WHERE tenant_id=:tenant"),
            {"tenant": tenant},
        )
        await db.commit()
    with (
        patch.object(worker, "async_session_maker", factory),
        patch.object(
            worker,
            "finish_job",
            AsyncMock(side_effect=RuntimeError("Private crash detail")),
        ),
    ):
        assert await worker.process_job(jobs[0], tenant)
    async with factory() as db:
        await set_tenant_context(db, str(tenant))
        failed = (
            await db.execute(
                text("SELECT status,last_error FROM durable_jobs WHERE id=:id"),
                {"id": jobs[0]},
            )
        ).one()
        assert (
            failed.status == "pending"
            and "Private crash detail" not in failed.last_error
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM matter_workflow_runs WHERE tenant_id=:tenant"
                ),
                {"tenant": tenant},
            )
            == initial_runs
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM matter_workflow_automation_events WHERE tenant_id=:tenant AND trigger_event NOT IN ('matter_created','matter_stage_changed')"
                ),
                {"tenant": tenant},
            )
            == 0
        )
        await db.execute(
            text("UPDATE durable_jobs SET available_at=now() WHERE id=:id"),
            {"id": jobs[0]},
        )
        await db.commit()
    with patch.object(worker, "async_session_maker", factory):
        for job in jobs:
            assert await worker.process_job(job, tenant)
    async with factory() as db:
        await set_tenant_context(db, str(tenant))
        results = (
            await db.execute(
                text(
                    "SELECT status,result,last_error FROM durable_jobs WHERE tenant_id=:tenant AND kind='workflow_lifecycle_plan'"
                ),
                {"tenant": tenant},
            )
        ).all()
        assert all(status == "completed" for status, _, _ in results), results
        assert (
            sum(result["outcome"] == "planned" for _, result, _ in results) == 8
        ), results
        assert (
            sum(result["outcome"] == "blocked" for _, result, _ in results) == 1
        ), results
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM matter_workflow_runs WHERE tenant_id=:tenant"
                ),
                {"tenant": tenant},
            )
            == initial_runs + 8
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM tasks WHERE tenant_id=:tenant"),
                {"tenant": tenant},
            )
            == initial_tasks
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM matter_workflow_automation_events WHERE tenant_id=:tenant AND trigger_event='task_completed' AND detail_json->>'failure_code'='source_changed'"
                ),
                {"tenant": tenant},
            )
            == 1
        )
        await db.execute(
            text(
                "UPDATE durable_jobs SET status='pending' WHERE tenant_id=:tenant AND kind='workflow_lifecycle_plan'"
            ),
            {"tenant": tenant},
        )
        await db.commit()
    with patch.object(worker, "async_session_maker", factory):
        for job in jobs:
            assert await worker.process_job(job, tenant)
    async with factory() as db:
        await set_tenant_context(db, str(tenant))
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM matter_workflow_runs WHERE tenant_id=:tenant"
                ),
                {"tenant": tenant},
            )
            == initial_runs + 8
        )
    await engine.dispose()


if __name__ == "__main__":
    main()
