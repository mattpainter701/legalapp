"""Exercise revision-165 guards on CI's migrated disposable PostgreSQL database."""

import json
import os
import uuid

from psycopg2.extras import Json
from sqlalchemy.engine import make_url

from rehearse_configurable_workflows import (
    connect,
    set_tenant,
    expect_database_error,
    seed_fixture,
)

TABLES = (
    "work_artifact_review_requirement",
    "work_artifact_approval",
    "work_artifact_delivery",
)


def execute(connection, statement, params=()):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
    connection.commit()


def main():
    owner_url = os.environ["MIGRATOR_DATABASE_URL"]
    runtime_url = os.environ["RLS_TEST_DATABASE_URL"]
    if "rehearsal" not in (make_url(owner_url).database or ""):
        raise SystemExit("Only a disposable rehearsal database is supported")
    owner, runtime = connect(owner_url), connect(runtime_url)
    fixtures = seed_fixture(owner)
    tenant, other_tenant = fixtures["tenants"]
    attorney, outsider = fixtures["users"]
    task, matter = fixtures["tasks"][0], fixtures["matters"][0]
    artifact, revision, document, staff, staff_req, attorney_req = [
        uuid.uuid4() for _ in range(6)
    ]
    text_hash, byte_hash = "a" * 64, "b" * 64
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users(id,tenant_id,email,full_name) VALUES (%s,%s,%s,'Staff reviewer')",
            (staff, tenant, f"{staff}@example.invalid"),
        )
        cursor.execute(
            "INSERT INTO generated_artifacts(id,tenant_id,matter_id,task_id,title,kind,format,status,source_channel,client_request_id,request_sha256,provenance) VALUES (%s,%s,%s,%s,'Reviewed document','letter','docx','review','workspace_mcp',%s,%s,'{}')",
            (artifact, tenant, matter, task, uuid.uuid4(), text_hash),
        )
        cursor.execute(
            "INSERT INTO generated_artifact_revisions(id,tenant_id,artifact_id,revision_no,content_text,content_sha256,variable_snapshot,unresolved_variables,source_snapshot,renderer_version) VALUES (%s,%s,%s,1,'Reviewed text',%s,'{}','[]','[]','rehearsal')",
            (revision, tenant, artifact, text_hash),
        )
        cursor.execute(
            "INSERT INTO matter_documents(id,tenant_id,matter_id,filename,generated_artifact_id,generated_artifact_revision_id,document_sha256,storage_state) VALUES (%s,%s,%s,'Reviewed.docx',%s,%s,%s,'verified')",
            (document, tenant, matter, artifact, revision, byte_hash),
        )
        cursor.execute(
            "UPDATE generated_artifacts SET output_document_id=%s WHERE id=%s",
            (document, artifact),
        )
        for seq, role, reviewer, requirement in (
            (1, "staff", staff, staff_req),
            (2, "attorney", attorney, attorney_req),
        ):
            cursor.execute(
                "INSERT INTO work_artifact_review_requirement(id,tenant_id,artifact_id,revision_id,review_round,sequence,reviewer_role,reviewer_user_id,required,status) VALUES (%s,%s,%s,%s,1,%s,%s,%s,true,'pending')",
                (requirement, tenant, artifact, revision, seq, role, reviewer),
            )
    owner.commit()
    set_tenant(runtime, tenant)
    with runtime.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert cursor.fetchone() == (False, False)
        cursor.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=ANY(%s)",
            (list(TABLES),),
        )
        assert len(rows := cursor.fetchall()) == 3 and all(
            row[1:] == (True, True) for row in rows
        )
    runtime.commit()

    insert = "INSERT INTO work_artifact_approval(id,tenant_id,artifact_id,revision_id,requirement_id,reviewer_user_id,document_id,decision,reason,content_sha256,document_sha256,purpose,stamp_metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'approve_document','{}')"

    def approval_params(
        req, reviewer, decision="approve", reason=None, digest=byte_hash
    ):
        return (
            uuid.uuid4(),
            tenant,
            artifact,
            revision,
            req,
            reviewer,
            document,
            decision,
            reason,
            text_hash,
            digest,
        )

    def reject(statement, params, states={"23514", "23503", "P0001", "23505"}):
        expect_database_error(
            runtime, statement, params, sqlstates=states, tenant_id=tenant
        )

    reject(insert, approval_params(attorney_req, attorney))  # staff-first
    reject(insert, approval_params(staff_req, outsider))  # foreign tenant/actor
    reject(
        insert, approval_params(staff_req, staff, digest="c" * 64)
    )  # substituted bytes
    reject(
        insert, approval_params(staff_req, attorney, decision="override")
    )  # no reason
    reject(
        "UPDATE work_artifact_review_requirement SET status='approved' WHERE id=%s",
        (staff_req,),
    )
    staff_stamp = approval_params(staff_req, staff)
    execute(runtime, insert, staff_stamp)
    execute(
        runtime,
        "UPDATE work_artifact_review_requirement SET status='approved' WHERE id=%s",
        (staff_req,),
    )
    attorney_stamp = approval_params(attorney_req, attorney)
    execute(runtime, insert, attorney_stamp)
    execute(
        runtime,
        "UPDATE work_artifact_review_requirement SET status='approved' WHERE id=%s",
        (attorney_req,),
    )
    reject(insert, approval_params(attorney_req, attorney))  # duplicate decision
    reject(
        "UPDATE work_artifact_approval SET document_sha256=%s WHERE id=%s",
        ("c" * 64, attorney_stamp[0]),
    )
    reject("DELETE FROM work_artifact_approval WHERE id=%s", (attorney_stamp[0],))
    reject("DELETE FROM work_artifact_review_requirement WHERE id=%s", (staff_req,))

    attempt = uuid.uuid4()
    recipients = [
        dict(
            party_id=str(uuid.uuid4()),
            contact_id=str(fixtures["contacts"][0]),
            address="client@example.invalid",
        )
    ]
    snapshot = dict(
        type="email_client",
        recipient_bindings=recipients,
        artifact_attachment=dict(
            artifact_id=str(artifact),
            revision_id=str(revision),
            approval_id=str(attorney_stamp[0]),
            document_sha256=byte_hash,
        ),
    )
    execute(
        owner,
        "INSERT INTO task_automation_runs(id,tenant_id,task_id,action_type,idempotency_key,status,triggered_by_user_id,action_snapshot,action_sha256) VALUES (%s,%s,%s,'email_client',%s,'queued',%s,%s,%s)",
        (attempt, tenant, task, str(attempt), attorney, Json(snapshot), text_hash),
    )
    delivery = "INSERT INTO work_artifact_delivery(tenant_id,artifact_id,revision_id,approval_id,actor_user_id,attempt_id,channel,status,document_sha256,recipient_bindings,detail) VALUES (%s,%s,%s,%s,%s,%s,'email',%s,%s,%s,'{}')"
    delivery_params = (tenant, artifact, revision, attorney_stamp[0], attorney, attempt)
    reject(
        delivery, (*delivery_params, "sent", byte_hash, Json(recipients))
    )  # no queued receipt
    execute(
        runtime, delivery, (*delivery_params, "queued", byte_hash, Json(recipients))
    )

    # Change the current revision: approvals remain immutable historical evidence.
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO generated_artifact_revisions(id,tenant_id,artifact_id,revision_no,content_text,content_sha256,variable_snapshot,unresolved_variables,source_snapshot,renderer_version) VALUES (%s,%s,%s,2,'Edited text',%s,'{}','[]','[]','rehearsal')",
            (uuid.uuid4(), tenant, artifact, "c" * 64),
        )
        cursor.execute(
            "UPDATE generated_artifacts SET current_revision_no=2 WHERE id=%s",
            (artifact,),
        )
    owner.commit()
    with runtime.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM work_artifact_review_requirement WHERE artifact_id=%s AND status='superseded'",
            (artifact,),
        )
        assert cursor.fetchone()[0] == 2
    runtime.commit()
    reject(insert, approval_params(attorney_req, attorney))
    # Late uncertainty must still be recordable against the original attempt.
    execute(
        owner,
        "UPDATE task_automation_runs SET status='failed',delivery_certainty='outcome_unknown',delivery_certainty_v2='outcome_unknown' WHERE id=%s",
        (attempt,),
    )
    execute(
        runtime,
        delivery,
        (*delivery_params, "outcome_unknown", byte_hash, Json(recipients)),
    )
    reject(
        "UPDATE work_artifact_delivery SET status='sent' WHERE attempt_id=%s",
        (attempt,),
    )
    set_tenant(runtime, other_tenant)
    for table in TABLES:
        with runtime.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {table} WHERE artifact_id=%s", (artifact,)
            )
            assert cursor.fetchone()[0] == 0
    runtime.commit()
    owner.close()
    runtime.close()
    print(
        json.dumps(
            {
                "artifact_review_rehearsal": "passed",
                "checks": [
                    "staged order",
                    "exact hashes",
                    "assigned actors",
                    "immutable decisions",
                    "edit supersession",
                    "delivery attempt binding",
                    "late uncertainty",
                    "tenant RLS",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
