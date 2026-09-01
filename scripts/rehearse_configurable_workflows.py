#!/usr/bin/env python3
"""PostgreSQL 16 runtime rehearsal for COMP-09.

CI seeds a disposable database at ``148_configurable_workflows`` and then upgrades
it to the current migration head before invoking this script. The rehearsal
then exercises the deployed catalog with a real
CI migrates a disposable database through the current repository head before invoking
this script. The rehearsal then exercises the deployed catalog with a real
NOSUPERUSER/NOBYPASSRLS role; it does not substitute ORM ``create_all`` for the
Alembic path and never targets a persistent environment.

The feature under rehearsal remains migration ``148_configurable_workflows``;
the head assertion advances as later migrations are appended.
CI proves the 147→148 upgrade separately, then advances the disposable database
to the repository's current Alembic head before invoking this script. The
rehearsal requires 148 in every deployed head's ancestry and exercises the
catalog with a real NOSUPERUSER/NOBYPASSRLS role; it never targets persistence.
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import os
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg2 import sql
from psycopg2.extras import register_uuid
from sqlalchemy.engine import make_url


register_uuid()


EXPECTED_HEAD = "153_sms_lifecycle"
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
REQUIRED_REVISION = "148_configurable_workflows"
TABLES = (
    "custom_field_definitions",
    "matter_custom_field_values",
    "contact_custom_field_values",
    "matter_workflow_templates",
    "matter_workflow_template_versions",
    "matter_workflow_stage_definitions",
    "matter_workflow_checklist_definitions",
    "matter_workflow_field_requirements",
    "matter_workflow_runs",
    "matter_workflow_run_events",
    "matter_workflow_run_steps",
)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def validate_revision_contract(
    *,
    deployed_heads: tuple[str, ...],
    repository_heads: tuple[str, ...],
    ancestry_by_head: dict[str, set[str]],
    required_revision: str = REQUIRED_REVISION,
) -> tuple[str, ...]:
    """Require the exact repo heads and COMP-09 ancestry without pinning 148."""
    deployed = tuple(sorted(set(deployed_heads)))
    repository = tuple(sorted(set(repository_heads)))
    if not deployed:
        raise AssertionError("database has no deployed Alembic revision")
    if deployed != repository:
        raise AssertionError(
            f"expected deployed Alembic heads {repository}, got {deployed}"
        )
    missing = [
        head
        for head in deployed
        if required_revision not in ancestry_by_head.get(head, set())
    ]
    if missing:
        raise AssertionError(
            f"required revision {required_revision} is not an ancestor of {missing}"
        )
    return deployed


def validate_deployed_revision_graph(
    deployed_heads: tuple[str, ...],
) -> tuple[str, ...]:
    """Load the absolute repository graph and validate deployed head lineage."""
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    repository_heads = tuple(scripts.get_heads())
    ancestry_by_head = {
        head: {
            revision.revision for revision in scripts.iterate_revisions(head, "base")
        }
        for head in repository_heads
    }
    return validate_revision_contract(
        deployed_heads=deployed_heads,
        repository_heads=repository_heads,
        ancestry_by_head=ancestry_by_head,
    )


def connect(value: str):
    parsed = make_url(value)
    return psycopg2.connect(
        dbname=parsed.database,
        user=parsed.username,
        password=parsed.password,
        host=parsed.host or "localhost",
        port=parsed.port or 5432,
        connect_timeout=15,
    )


def set_tenant(connection, tenant_id: uuid.UUID | str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              set_config('app.current_tenant_id', %s, false),
              set_config('app.tenant_id', %s, false),
              set_config('app.rls_bypass', 'off', false)
            """,
            (str(tenant_id), str(tenant_id)),
        )
    connection.commit()


def expect_database_error(
    connection,
    statement: str,
    params: tuple[Any, ...] = (),
    *,
    sqlstates: set[str],
    tenant_id: uuid.UUID | str | None = None,
) -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
        connection.commit()
    except psycopg2.Error as exc:
        connection.rollback()
        if exc.pgcode not in sqlstates:
            raise AssertionError(
                f"expected SQLSTATE {sorted(sqlstates)}, received {exc.pgcode}: {statement}"
            ) from exc
        if tenant_id is not None:
            set_tenant(connection, tenant_id)
        return str(exc.pgcode)
    raise AssertionError(f"expected database rejection: {statement}")


def seed_fixture(owner) -> dict[str, Any]:
    tenants = [uuid.uuid4(), uuid.uuid4()]
    users = [uuid.uuid4(), uuid.uuid4()]
    matters = [uuid.uuid4(), uuid.uuid4()]
    contacts = [uuid.uuid4(), uuid.uuid4()]
    tasks = [uuid.uuid4(), uuid.uuid4()]
    records: list[dict[str, Any]] = []

    with owner.cursor() as cursor:
        for index, tenant_id in enumerate(tenants):
            user_id = users[index]
            matter_id = matters[index]
            contact_id = contacts[index]
            task_id = tasks[index]
            record = {
                "field": uuid.uuid4(),
                "contact_field": uuid.uuid4(),
                "matter_value": uuid.uuid4(),
                "contact_value": uuid.uuid4(),
                "template": uuid.uuid4(),
                "version": uuid.uuid4(),
                "stage": uuid.uuid4(),
                "checklist_preflight": uuid.uuid4(),
                "checklist": uuid.uuid4(),
                "requirement": uuid.uuid4(),
                "run": uuid.uuid4(),
                "event": uuid.uuid4(),
                "step": uuid.uuid4(),
            }
            records.append(record)
            definition_sha256 = hashlib.sha256(
                json.dumps(
                    {
                        "initial_stage_key": "start",
                        "stages": [{"stage_key": "start", "label": "Started"}],
                        "checklist": [
                            {
                                "item_key": "preflight",
                                "stage_key": "start",
                                "title": "Prepare file",
                                "description": None,
                                "task_type": "general",
                                "priority": "medium",
                                "due_offset_days": 0,
                                "assignee_role": "unassigned",
                            },
                            {
                                "item_key": "review",
                                "stage_key": "start",
                                "title": "Review file",
                                "description": None,
                                "task_type": "review",
                                "priority": "medium",
                                "due_offset_days": 2,
                                "assignee_role": "matter_owner",
                            },
                        ],
                        "required_field_definition_ids": [str(record["field"])],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            record["definition_sha256"] = definition_sha256
            token = uuid.uuid4().hex
            cursor.execute(
                "INSERT INTO tenants (id,name,domain) VALUES (%s,%s,%s)",
                (tenant_id, f"COMP-09 tenant {index}", f"comp09-{token}.invalid"),
            )
            cursor.execute(
                "INSERT INTO users (id,tenant_id,email,full_name) VALUES (%s,%s,%s,%s)",
                (user_id, tenant_id, f"comp09-{token}@example.invalid", "COMP-09 User"),
            )
            cursor.execute(
                "INSERT INTO matters (id,tenant_id,user_id,slug,matter_name,stage) VALUES (%s,%s,%s,%s,%s,'New')",
                (matter_id, tenant_id, user_id, f"comp09-{token}", "COMP-09 Matter"),
            )
            cursor.execute(
                "INSERT INTO contacts (id,tenant_id,first_name,last_name,created_by_user_id) VALUES (%s,%s,'COMP','09',%s)",
                (contact_id, tenant_id, user_id),
            )
            cursor.execute(
                "INSERT INTO tasks (id,tenant_id,title,matter_id,created_by_user_id) VALUES (%s,%s,'Existing task',%s,%s)",
                (task_id, tenant_id, matter_id, user_id),
            )
            cursor.execute(
                """
                INSERT INTO custom_field_definitions
                  (id,tenant_id,entity_type,field_key,label,field_type,created_by_user_id)
                VALUES (%s,%s,'matter','priority_code','Priority code','text',%s)
                """,
                (record["field"], tenant_id, user_id),
            )
            cursor.execute(
                """
                INSERT INTO custom_field_definitions
                  (id,tenant_id,entity_type,field_key,label,field_type,options_json,created_by_user_id)
                VALUES (%s,%s,'contact','relationship','Relationship','single_select','["Client","Opponent"]'::jsonb,%s)
                """,
                (record["contact_field"], tenant_id, user_id),
            )
            cursor.execute(
                """
                INSERT INTO matter_custom_field_values
                  (id,tenant_id,matter_id,field_definition_id,value_json,value_hmac,updated_by_user_id)
                VALUES (%s,%s,%s,%s,'"P1"'::jsonb,%s,%s)
                """,
                (
                    record["matter_value"],
                    tenant_id,
                    matter_id,
                    record["field"],
                    HASH_A,
                    user_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO contact_custom_field_values
                  (id,tenant_id,contact_id,field_definition_id,value_json,value_hmac,updated_by_user_id)
                VALUES (%s,%s,%s,%s,'"Client"'::jsonb,%s,%s)
                """,
                (
                    record["contact_value"],
                    tenant_id,
                    contact_id,
                    record["contact_field"],
                    HASH_B,
                    user_id,
                ),
            )
            cursor.execute(
                "INSERT INTO matter_workflow_templates (id,tenant_id,name,created_by_user_id) VALUES (%s,%s,%s,%s)",
                (record["template"], tenant_id, f"Opening {index}", user_id),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_template_versions
                  (id,tenant_id,template_id,version,initial_stage_key,definition_sha256,created_by_user_id)
                VALUES (%s,%s,%s,1,'start',%s,%s)
                """,
                (
                    record["version"],
                    tenant_id,
                    record["template"],
                    definition_sha256,
                    user_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_stage_definitions
                  (id,tenant_id,template_version_id,stage_key,label,position)
                VALUES (%s,%s,%s,'start','Started',0)
                """,
                (record["stage"], tenant_id, record["version"]),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_checklist_definitions
                  (id,tenant_id,template_version_id,stage_key,item_key,title,position,task_type,priority,due_offset_days,assignee_role)
                VALUES (%s,%s,%s,'start','preflight','Prepare file',0,'general','medium',0,'unassigned')
                """,
                (record["checklist_preflight"], tenant_id, record["version"]),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_checklist_definitions
                  (id,tenant_id,template_version_id,stage_key,item_key,title,position,task_type,priority,due_offset_days,assignee_role)
                VALUES (%s,%s,%s,'start','review','Review file',1,'review','medium',2,'matter_owner')
                """,
                (record["checklist"], tenant_id, record["version"]),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_field_requirements
                  (id,tenant_id,template_version_id,field_definition_id)
                VALUES (%s,%s,%s,%s)
                """,
                (record["requirement"], tenant_id, record["version"], record["field"]),
            )
            cursor.execute(
                """
                UPDATE matter_workflow_template_versions
                   SET status='approved', approved_by_user_id=%s, approved_at=now()
                 WHERE id=%s
                """,
                (user_id, record["version"]),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_runs
                  (id,tenant_id,matter_id,template_version_id,idempotency_key,
                   request_sha256,template_sha256,matter_sha256,preview_sha256,
                   preview_json,status,planned_by_user_id,approved_by_user_id,approved_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        '{"initial_stage":{"stage_key":"start","label":"Started"},"tasks":[]}'::jsonb,
                        'applied',%s,%s,now())
                """,
                (
                    record["run"],
                    tenant_id,
                    matter_id,
                    record["version"],
                    f"seed-{index}",
                    HASH_A,
                    HASH_B,
                    HASH_C,
                    HASH_D,
                    user_id,
                    user_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_run_events
                  (id,tenant_id,run_id,sequence,event_type,actor_user_id,evidence_sha256)
                VALUES (%s,%s,%s,1,'applied',%s,%s)
                """,
                (record["event"], tenant_id, record["run"], user_id, HASH_A),
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_run_steps
                  (id,tenant_id,run_id,sequence,step_type,action_key,status,task_id,evidence_sha256)
                VALUES (%s,%s,%s,1,'task_create','review','succeeded',%s,%s)
                """,
                (record["step"], tenant_id, record["run"], task_id, HASH_B),
            )
    owner.commit()
    return {
        "tenants": tenants,
        "users": users,
        "matters": matters,
        "contacts": contacts,
        "tasks": tasks,
        "records": records,
    }


def seed_demo_purge_fixture(owner, fixture_tenant_id: uuid.UUID) -> dict[str, Any]:
    """Seed every COMP-09 table for an expired disposable demo tenant."""

    tenant_id, session_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    matter_id, contact_id = uuid.uuid4(), uuid.uuid4()
    matter_field_id, contact_field_id = uuid.uuid4(), uuid.uuid4()
    template_id, version_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = {name: uuid.uuid4() for name in ("stage", "checklist", "requirement")}
    with owner.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tenants
              (id,name,domain,billing_tier,is_active,expires_at)
            VALUES (%s,'COMP-09 purge demo',%s,'demo',true,now() - interval '1 minute')
            """,
            (tenant_id, f"comp09-{uuid.uuid4().hex}.demo.invalid"),
        )
        cursor.execute(
            """
            INSERT INTO demo_sessions
              (id,tenant_id,fixture_tenant_id,fixture_version,prospect_name,
               prospect_email,status,quota,expires_at)
            VALUES (%s,%s,%s,'comp09-rehearsal','Synthetic Prospect',
                    'comp09-purge@example.invalid','active',20,now() - interval '1 minute')
            """,
            (session_id, tenant_id, fixture_tenant_id),
        )
        cursor.execute(
            "INSERT INTO users (id,tenant_id,email,full_name) VALUES (%s,%s,%s,'Purge User')",
            (user_id, tenant_id, f"comp09-purge-{uuid.uuid4().hex}@example.invalid"),
        )
        cursor.execute(
            """
            INSERT INTO matters (id,tenant_id,user_id,slug,matter_name,stage)
            VALUES (%s,%s,%s,%s,'Disposable workflow matter','New')
            """,
            (matter_id, tenant_id, user_id, f"comp09-purge-{uuid.uuid4().hex}"),
        )
        cursor.execute(
            """
            INSERT INTO contacts (id,tenant_id,first_name,last_name,created_by_user_id)
            VALUES (%s,%s,'Disposable','Workflow Contact',%s)
            """,
            (contact_id, tenant_id, user_id),
        )
        cursor.execute(
            """
            INSERT INTO custom_field_definitions
              (id,tenant_id,entity_type,field_key,label,field_type,created_by_user_id)
            VALUES
              (%s,%s,'matter','purge_matter_field','Purge matter field','text',%s),
              (%s,%s,'contact','purge_contact_field','Purge contact field','text',%s)
            """,
            (
                matter_field_id,
                tenant_id,
                user_id,
                contact_field_id,
                tenant_id,
                user_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO matter_custom_field_values
              (tenant_id,matter_id,field_definition_id,value_json,value_hmac,updated_by_user_id)
            VALUES (%s,%s,%s,'"purge"'::jsonb,%s,%s)
            """,
            (tenant_id, matter_id, matter_field_id, HASH_A, user_id),
        )
        cursor.execute(
            """
            INSERT INTO contact_custom_field_values
              (tenant_id,contact_id,field_definition_id,value_json,value_hmac,updated_by_user_id)
            VALUES (%s,%s,%s,'"purge"'::jsonb,%s,%s)
            """,
            (tenant_id, contact_id, contact_field_id, HASH_B, user_id),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_templates (id,tenant_id,name,created_by_user_id)
            VALUES (%s,%s,'Disposable approved workflow',%s)
            """,
            (template_id, tenant_id, user_id),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_template_versions
              (id,tenant_id,template_id,version,initial_stage_key,definition_sha256,created_by_user_id)
            VALUES (%s,%s,%s,1,'initial',%s,%s)
            """,
            (version_id, tenant_id, template_id, HASH_C, user_id),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_stage_definitions
              (id,tenant_id,template_version_id,stage_key,label,position)
            VALUES (%s,%s,%s,'initial','Initial',0)
            """,
            (ids["stage"], tenant_id, version_id),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_checklist_definitions
              (id,tenant_id,template_version_id,stage_key,item_key,title,position,
               task_type,priority,due_offset_days,assignee_role)
            VALUES (%s,%s,%s,'initial','review','Review demo',0,
                    'review','medium',1,'matter_owner')
            """,
            (ids["checklist"], tenant_id, version_id),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_field_requirements
              (id,tenant_id,template_version_id,field_definition_id)
            VALUES (%s,%s,%s,%s)
            """,
            (ids["requirement"], tenant_id, version_id, matter_field_id),
        )
        cursor.execute(
            """
            UPDATE matter_workflow_template_versions
               SET status='approved',approved_by_user_id=%s,approved_at=now()
             WHERE id=%s
            """,
            (user_id, version_id),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_runs
              (id,tenant_id,matter_id,template_version_id,idempotency_key,
               request_sha256,template_sha256,matter_sha256,preview_sha256,
               preview_json,status,prior_stage,planned_by_user_id,
               approved_by_user_id,approved_at)
            VALUES (%s,%s,%s,%s,'demo-purge',%s,%s,%s,%s,
                    '{"initial_stage":{"stage_key":"initial","label":"Initial"},"tasks":[]}'::jsonb,
                    'applied','New',%s,%s,now())
            """,
            (
                run_id,
                tenant_id,
                matter_id,
                version_id,
                HASH_A,
                HASH_B,
                HASH_C,
                HASH_D,
                user_id,
                user_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_run_events
              (tenant_id,run_id,sequence,event_type,actor_user_id,evidence_sha256)
            VALUES (%s,%s,1,'applied',%s,%s)
            """,
            (tenant_id, run_id, user_id, HASH_A),
        )
        cursor.execute(
            """
            INSERT INTO matter_workflow_run_steps
              (tenant_id,run_id,sequence,step_type,action_key,status,evidence_sha256)
            VALUES (%s,%s,1,'matter_stage','initial','succeeded',%s)
            """,
            (tenant_id, run_id, HASH_B),
        )
    owner.commit()
    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "run_id": run_id,
        "stage_id": ids["stage"],
    }


def assert_seeded_draft_approval_transitions(owner, fixture: dict[str, Any]) -> int:
    """Prove the trigger permits the exact draft-to-approved transition."""

    version_ids = [record["version"] for record in fixture["records"]]
    with owner.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
              FROM public.matter_workflow_template_versions
             WHERE id = ANY(%s)
               AND status = 'approved'
               AND approved_by_user_id IS NOT NULL
               AND approved_at IS NOT NULL
            """,
            (version_ids,),
        )
        approved = cursor.fetchone()[0]
    if approved != len(version_ids):
        raise AssertionError(
            "the exact draft-to-approved version transition was not permitted"
        )
    return approved


def assert_catalog(owner, runtime_role: str) -> None:
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s",
            (runtime_role,),
        )
        if cursor.fetchone() != (False, False):
            raise AssertionError("runtime role must be NOSUPERUSER and NOBYPASSRLS")
        for table in TABLES:
            cursor.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            )
            if cursor.fetchone() != (True, True):
                raise AssertionError(f"{table} does not ENABLE and FORCE RLS")
            cursor.execute(
                """
                SELECT pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid)
                  FROM pg_policy
                 WHERE polrelid=%s::regclass
                """,
                (table,),
            )
            policies = cursor.fetchall()
            if not any(
                "tenant_id" in f"{using} {check}"
                and "current_setting" in f"{using} {check}"
                and check is not None
                for using, check in policies
            ):
                raise AssertionError(
                    f"{table} lacks a fail-closed tenant WITH CHECK policy"
                )

        for table, constraint in (
            ("contacts", "uq_contacts_tenant_id"),
            ("tasks", "uq_tasks_tenant_id"),
        ):
            cursor.execute(
                """
                SELECT array_agg(a.attname::text ORDER BY keys.ordinality)
                  FROM pg_constraint c
                  CROSS JOIN unnest(c.conkey) WITH ORDINALITY AS keys(attnum, ordinality)
                  JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=keys.attnum
                 WHERE c.conrelid=%s::regclass AND c.conname=%s
                """,
                (table, constraint),
            )
            if cursor.fetchone()[0] != ["tenant_id", "id"]:
                raise AssertionError(
                    f"{table}.{constraint} is not the expected composite target"
                )
        cursor.execute(
            """
            SELECT count(*)
              FROM roles
             WHERE name='Administrator' AND is_system IS TRUE
               AND NOT (capabilities @> '["manage_workflows"]'::jsonb)
            """
        )
        if cursor.fetchone()[0] != 0:
            raise AssertionError(
                "existing system Administrator roles lack manage_workflows"
            )


def assert_effective_rls(runtime_url: str, fixture: dict[str, Any]) -> dict[str, Any]:
    tenants = fixture["tenants"]
    visible: dict[str, dict[str, int]] = {}
    with connect(runtime_url) as runtime:
        for index, tenant_id in enumerate(tenants):
            set_tenant(runtime, tenant_id)
            visible[str(index)] = {}
            with runtime.cursor() as cursor:
                for table in TABLES:
                    cursor.execute(
                        sql.SQL("SELECT count(*) FROM {} WHERE tenant_id=%s").format(
                            sql.Identifier(table)
                        ),
                        (tenant_id,),
                    )
                    own_count = cursor.fetchone()[0]
                    if own_count < 1:
                        raise AssertionError(
                            f"tenant {index} cannot read its own {table} row"
                        )
                    cursor.execute(
                        sql.SQL("SELECT count(*) FROM {} WHERE tenant_id=%s").format(
                            sql.Identifier(table)
                        ),
                        (tenants[1 - index],),
                    )
                    if cursor.fetchone()[0] != 0:
                        raise AssertionError(
                            f"tenant {index} read foreign rows in {table}"
                        )
                    visible[str(index)][table] = own_count

        set_tenant(runtime, tenants[0])
        expect_database_error(
            runtime,
            """
            INSERT INTO custom_field_definitions
              (tenant_id,entity_type,field_key,label,field_type,created_by_user_id)
            VALUES (%s,'matter','cross_write','Cross write','text',%s)
            """,
            (tenants[1], fixture["users"][1]),
            sqlstates={"42501"},
            tenant_id=tenants[0],
        )

    with connect(runtime_url) as no_context:
        with no_context.cursor() as cursor:
            for table in TABLES:
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                )
                if cursor.fetchone()[0] != 0:
                    raise AssertionError(f"{table} is visible without a tenant GUC")
        expect_database_error(
            no_context,
            """
            INSERT INTO custom_field_definitions
              (tenant_id,entity_type,field_key,label,field_type,created_by_user_id)
            VALUES (%s,'matter','no_context','No context','text',%s)
            """,
            (tenants[0], fixture["users"][0]),
            sqlstates={"42501"},
        )
    return visible


def assert_integrity_and_immutability(
    owner, fixture: dict[str, Any]
) -> tuple[list[str], list[str]]:
    tenant_a = fixture["tenants"][0]
    user_a, user_b = fixture["users"]
    matter_a, matter_b = fixture["matters"]
    contact_b = fixture["contacts"][1]
    task_b = fixture["tasks"][1]
    record_a, record_b = fixture["records"]
    rejected: list[str] = []

    draft_template, draft_version, draft_stage = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    relationship_field = uuid.uuid4()
    typed_field = uuid.uuid4()
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO matter_workflow_templates (id,tenant_id,name,created_by_user_id) VALUES (%s,%s,'Draft mutation proof',%s)",
            (draft_template, tenant_a, user_a),
        )
        cursor.execute(
            "INSERT INTO matter_workflow_template_versions (id,tenant_id,template_id,version,initial_stage_key,definition_sha256,created_by_user_id) VALUES (%s,%s,%s,1,'draft',%s,%s)",
            (draft_version, tenant_a, draft_template, HASH_A, user_a),
        )
        cursor.execute(
            "INSERT INTO matter_workflow_stage_definitions (id,tenant_id,template_version_id,stage_key,label,position) VALUES (%s,%s,%s,'draft','Draft',0)",
            (draft_stage, tenant_a, draft_version),
        )
        cursor.execute(
            "INSERT INTO custom_field_definitions (id,tenant_id,entity_type,field_key,label,field_type,created_by_user_id) VALUES (%s,%s,'matter','related_contact','Related contact','contact',%s)",
            (relationship_field, tenant_a, user_a),
        )
        cursor.execute(
            "INSERT INTO custom_field_definitions (id,tenant_id,entity_type,field_key,label,field_type,sensitive,created_by_user_id) VALUES (%s,%s,'matter','typed_text','Typed text','text',true,%s)",
            (typed_field, tenant_a, user_a),
        )
    owner.commit()

    cases = (
        (
            "bad_key",
            "INSERT INTO custom_field_definitions (tenant_id,entity_type,field_key,label,field_type,created_by_user_id) VALUES (%s,'matter','bad-key','Bad','text',%s)",
            (tenant_a, user_a),
            {"23514"},
        ),
        (
            "empty_select_options",
            "INSERT INTO custom_field_definitions (tenant_id,entity_type,field_key,label,field_type,created_by_user_id) VALUES (%s,'matter','empty_select','Bad','single_select',%s)",
            (tenant_a, user_a),
            {"23514"},
        ),
        (
            "non_string_select_option",
            "INSERT INTO custom_field_definitions (tenant_id,entity_type,field_key,label,field_type,options_json,created_by_user_id) VALUES (%s,'matter','bad_option','Bad','single_select','[1]'::jsonb,%s)",
            (tenant_a, user_a),
            {"23514"},
        ),
        (
            "duplicate_select_option",
            "INSERT INTO custom_field_definitions (tenant_id,entity_type,field_key,label,field_type,options_json,created_by_user_id) VALUES (%s,'matter','duplicate_option','Bad','single_select','[\"Client\",\"client\"]'::jsonb,%s)",
            (tenant_a, user_a),
            {"23514"},
        ),
        (
            "typed_value_mismatch",
            "INSERT INTO matter_custom_field_values (tenant_id,matter_id,field_definition_id,value_json,value_hmac,updated_by_user_id) VALUES (%s,%s,%s,'true'::jsonb,%s,%s)",
            (tenant_a, matter_a, typed_field, HASH_A, user_a),
            {"P0001"},
        ),
        (
            "stable_field_key_rewrite",
            "UPDATE custom_field_definitions SET field_key='renamed', schema_version=schema_version+1 WHERE id=%s",
            (typed_field,),
            {"P0001"},
        ),
        (
            "sensitive_field_downgrade",
            "UPDATE custom_field_definitions SET sensitive=false, schema_version=schema_version+1 WHERE id=%s",
            (typed_field,),
            {"P0001"},
        ),
        (
            "stored_select_option_rewrite",
            'UPDATE custom_field_definitions SET options_json=\'["Client","Other"]\'::jsonb, schema_version=schema_version+1 WHERE id=%s',
            (record_a["contact_field"],),
            {"P0001"},
        ),
        (
            "cross_linked_contact_fk",
            "INSERT INTO matter_custom_field_values (tenant_id,matter_id,field_definition_id,linked_contact_id,value_json,value_hmac,updated_by_user_id) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)",
            (
                tenant_a,
                matter_a,
                relationship_field,
                contact_b,
                json.dumps(str(contact_b)),
                HASH_A,
                user_a,
            ),
            {"23503"},
        ),
        (
            "cross_matter_fk",
            "INSERT INTO matter_custom_field_values (tenant_id,matter_id,field_definition_id,value_json,value_hmac,updated_by_user_id) VALUES (%s,%s,%s,'\"x\"',%s,%s)",
            (tenant_a, matter_b, record_a["field"], HASH_A, user_a),
            {"23503"},
        ),
        (
            "cross_contact_fk",
            "INSERT INTO contact_custom_field_values (tenant_id,contact_id,field_definition_id,value_json,value_hmac,updated_by_user_id) VALUES (%s,%s,%s,'\"Client\"',%s,%s)",
            (tenant_a, contact_b, record_a["contact_field"], HASH_A, user_a),
            {"23503"},
        ),
        (
            "cross_required_field_fk",
            "INSERT INTO matter_workflow_field_requirements (tenant_id,template_version_id,field_definition_id) VALUES (%s,%s,%s)",
            (tenant_a, draft_version, record_b["field"]),
            {"23503"},
        ),
        (
            "bad_due_offset",
            """
            INSERT INTO matter_workflow_checklist_definitions
              (tenant_id,template_version_id,stage_key,item_key,title,position,
               task_type,priority,due_offset_days,assignee_role)
            VALUES (%s,%s,'draft','bad_offset','Bad offset',0,
                    'review','medium',-1,'unassigned')
            """,
            (tenant_a, draft_version),
            {"23514"},
        ),
        (
            "blank_stage_label",
            "INSERT INTO matter_workflow_stage_definitions (tenant_id,template_version_id,stage_key,label,position) VALUES (%s,%s,'blank_stage','   ',1)",
            (tenant_a, draft_version),
            {"23514"},
        ),
        (
            "blank_checklist_title",
            "INSERT INTO matter_workflow_checklist_definitions (tenant_id,template_version_id,stage_key,item_key,title,position,task_type,priority,due_offset_days,assignee_role) VALUES (%s,%s,'draft','blank_title','   ',1,'review','medium',0,'unassigned')",
            (tenant_a, draft_version),
            {"23514"},
        ),
        (
            "cross_run_matter_fk",
            """
            INSERT INTO matter_workflow_runs
              (tenant_id,matter_id,template_version_id,idempotency_key,request_sha256,template_sha256,matter_sha256,preview_sha256,preview_json,planned_by_user_id)
            VALUES (%s,%s,%s,'cross-matter',%s,%s,%s,%s,'{}',%s)
            """,
            (
                tenant_a,
                matter_b,
                record_a["version"],
                HASH_A,
                HASH_B,
                HASH_C,
                HASH_D,
                user_a,
            ),
            {"23503"},
        ),
        (
            "cross_event_actor_fk",
            "INSERT INTO matter_workflow_run_events (tenant_id,run_id,sequence,event_type,actor_user_id,evidence_sha256) VALUES (%s,%s,9,'failed',%s,%s)",
            (tenant_a, record_a["run"], user_b, HASH_A),
            {"23503"},
        ),
        (
            "cross_step_task_fk",
            "INSERT INTO matter_workflow_run_steps (tenant_id,run_id,sequence,step_type,action_key,status,task_id,evidence_sha256) VALUES (%s,%s,9,'task_create','foreign','succeeded',%s,%s)",
            (tenant_a, record_a["run"], task_b, HASH_A),
            {"23503"},
        ),
        (
            "bad_run_status",
            """
            INSERT INTO matter_workflow_runs
              (tenant_id,matter_id,template_version_id,idempotency_key,request_sha256,template_sha256,matter_sha256,preview_sha256,preview_json,status,planned_by_user_id)
            VALUES (%s,%s,%s,'bad-status',%s,%s,%s,%s,'{}','invented',%s)
            """,
            (
                tenant_a,
                matter_a,
                record_a["version"],
                HASH_A,
                HASH_B,
                HASH_C,
                HASH_D,
                user_a,
            ),
            {"23514"},
        ),
        (
            "history_event_update",
            "UPDATE matter_workflow_run_events SET event_type='failed' WHERE id=%s",
            (record_a["event"],),
            {"P0001"},
        ),
        (
            "history_step_delete",
            "DELETE FROM matter_workflow_run_steps WHERE id=%s",
            (record_a["step"],),
            {"P0001"},
        ),
        (
            "run_snapshot_update",
            "UPDATE matter_workflow_runs SET preview_sha256=%s WHERE id=%s",
            (HASH_A, record_a["run"]),
            {"P0001"},
        ),
        (
            "run_delete",
            "DELETE FROM matter_workflow_runs WHERE id=%s",
            (record_a["run"],),
            {"P0001"},
        ),
        (
            "approved_version_update",
            "UPDATE matter_workflow_template_versions SET initial_stage_key='changed' WHERE id=%s",
            (record_a["version"],),
            {"P0001"},
        ),
        (
            "preapproved_version_insert",
            """
            INSERT INTO matter_workflow_template_versions
              (tenant_id,template_id,version,status,initial_stage_key,definition_sha256,
               created_by_user_id,approved_by_user_id,approved_at)
            VALUES (%s,%s,2,'approved','start',%s,%s,%s,now())
            """,
            (tenant_a, record_a["template"], HASH_A, user_a, user_a),
            {"P0001"},
        ),
        (
            "approved_stage_update",
            "UPDATE matter_workflow_stage_definitions SET label='Changed' WHERE id=%s",
            (record_a["stage"],),
            {"P0001"},
        ),
        (
            "approved_stage_delete",
            "DELETE FROM matter_workflow_stage_definitions WHERE id=%s",
            (record_a["stage"],),
            {"P0001"},
        ),
        (
            "approved_stage_insert",
            "INSERT INTO matter_workflow_stage_definitions (tenant_id,template_version_id,stage_key,label,position) VALUES (%s,%s,'late','Late',2)",
            (tenant_a, record_a["version"]),
            {"P0001"},
        ),
        (
            "approved_checklist_update",
            "UPDATE matter_workflow_checklist_definitions SET title='Changed' WHERE id=%s",
            (record_a["checklist"],),
            {"P0001"},
        ),
        (
            "approved_requirement_update",
            "UPDATE matter_workflow_field_requirements SET field_definition_id=%s WHERE id=%s",
            (typed_field, record_a["requirement"]),
            {"P0001"},
        ),
        (
            "approved_checklist_delete",
            "DELETE FROM matter_workflow_checklist_definitions WHERE id=%s",
            (record_a["checklist"],),
            {"P0001"},
        ),
        (
            "approved_checklist_insert",
            "INSERT INTO matter_workflow_checklist_definitions (tenant_id,template_version_id,stage_key,item_key,title,position,task_type,priority,due_offset_days,assignee_role) VALUES (%s,%s,'start','late_check','Late check',9,'review','medium',0,'unassigned')",
            (tenant_a, record_a["version"]),
            {"P0001"},
        ),
        (
            "approved_requirement_delete",
            "DELETE FROM matter_workflow_field_requirements WHERE id=%s",
            (record_a["requirement"],),
            {"P0001"},
        ),
        (
            "approved_requirement_insert",
            "INSERT INTO matter_workflow_field_requirements (tenant_id,template_version_id,field_definition_id) VALUES (%s,%s,%s)",
            (tenant_a, record_a["version"], typed_field),
            {"P0001"},
        ),
        (
            "mutated_draft_approval_transition",
            "UPDATE matter_workflow_template_versions SET status='approved', approved_by_user_id=%s, approved_at=now(), definition_sha256=%s WHERE id=%s",
            (user_a, HASH_B, draft_version),
            {"P0001"},
        ),
    )
    for name, statement, params, states in cases:
        expect_database_error(owner, statement, params, sqlstates=states)
        rejected.append(name)

    with owner.cursor() as cursor:
        own_contact = fixture["contacts"][0]
        cursor.execute(
            "INSERT INTO matter_custom_field_values (tenant_id,matter_id,field_definition_id,linked_contact_id,value_json,value_hmac,updated_by_user_id) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)",
            (
                tenant_a,
                matter_a,
                relationship_field,
                own_contact,
                json.dumps(str(own_contact)),
                HASH_A,
                user_a,
            ),
        )
        cursor.execute(
            "UPDATE custom_field_definitions SET label='Typed text updated', schema_version=schema_version+1 WHERE id=%s",
            (typed_field,),
        )
        cursor.execute(
            "UPDATE matter_workflow_stage_definitions SET label='Draft changed' WHERE id=%s",
            (draft_stage,),
        )
        draft_insert_stage = uuid.uuid4()
        cursor.execute(
            "INSERT INTO matter_workflow_stage_definitions (id,tenant_id,template_version_id,stage_key,label,position) VALUES (%s,%s,%s,'draft_insert','Draft insert',1)",
            (draft_insert_stage, tenant_a, draft_version),
        )
        cursor.execute(
            "UPDATE matter_workflow_stage_definitions SET label='Draft updated' WHERE id=%s",
            (draft_insert_stage,),
        )
        cursor.execute(
            "DELETE FROM matter_workflow_stage_definitions WHERE id=%s",
            (draft_insert_stage,),
        )
        draft_checklist = uuid.uuid4()
        cursor.execute(
            "INSERT INTO matter_workflow_checklist_definitions (id,tenant_id,template_version_id,stage_key,item_key,title,position,task_type,priority,due_offset_days,assignee_role) VALUES (%s,%s,%s,'draft','draft_check','Draft check',0,'review','medium',0,'unassigned')",
            (draft_checklist, tenant_a, draft_version),
        )
        cursor.execute(
            "UPDATE matter_workflow_checklist_definitions SET title='Draft check updated' WHERE id=%s",
            (draft_checklist,),
        )
        cursor.execute(
            "DELETE FROM matter_workflow_checklist_definitions WHERE id=%s",
            (draft_checklist,),
        )
        cursor.execute(
            "DELETE FROM matter_workflow_stage_definitions WHERE id=%s", (draft_stage,)
        )
        draft_requirement = uuid.uuid4()
        cursor.execute(
            "INSERT INTO matter_workflow_field_requirements (id,tenant_id,template_version_id,field_definition_id) VALUES (%s,%s,%s,%s)",
            (draft_requirement, tenant_a, draft_version, typed_field),
        )
        cursor.execute(
            "UPDATE matter_workflow_field_requirements SET field_definition_id=%s WHERE id=%s",
            (relationship_field, draft_requirement),
        )
        cursor.execute(
            "DELETE FROM matter_workflow_field_requirements WHERE id=%s",
            (draft_requirement,),
        )
    owner.commit()
    return rejected, [
        "draft_stage_insert_update_delete",
        "draft_checklist_insert_update_delete",
        "draft_requirement_insert_update_delete",
    ]


def assert_temp_shadow_resistance(
    runtime_url: str, fixture: dict[str, Any]
) -> list[str]:
    """Prove runtime TEMP privileges cannot redirect trigger relation reads."""

    tenant_id = fixture["tenants"][0]
    record = fixture["records"][0]
    inactive_field = uuid.uuid4()
    rejected: list[str] = []
    with connect(runtime_url) as runtime:
        set_tenant(runtime, tenant_id)
        with runtime.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE custom_field_definitions (
                  id uuid, tenant_id uuid, entity_type text,
                  field_type text, options_json jsonb, active boolean
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO custom_field_definitions
                  (id,tenant_id,entity_type,field_type,options_json,active)
                VALUES (%s,%s,'matter','boolean','[]'::jsonb,true)
                """,
                (record["field"], tenant_id),
            )
            cursor.execute(
                """
                INSERT INTO public.custom_field_definitions
                  (id,tenant_id,entity_type,field_key,label,field_type,active,created_by_user_id)
                VALUES (%s,%s,'matter','temp_shadow_inactive','Inactive','text',false,%s)
                """,
                (inactive_field, tenant_id, fixture["users"][0]),
            )
            cursor.execute(
                """
                INSERT INTO custom_field_definitions
                  (id,tenant_id,entity_type,field_type,options_json,active)
                VALUES (%s,%s,'matter','text','[]'::jsonb,true)
                """,
                (inactive_field, tenant_id),
            )
            cursor.execute(
                """
                CREATE TEMP TABLE matter_workflow_template_versions (
                  id uuid, tenant_id uuid, template_version_id uuid, status text
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO matter_workflow_template_versions
                  (id,tenant_id,template_version_id,status)
                VALUES (%s,%s,%s,'draft')
                """,
                (record["version"], tenant_id, record["version"]),
            )
        runtime.commit()
        set_tenant(runtime, tenant_id)
        expect_database_error(
            runtime,
            "UPDATE public.matter_custom_field_values SET value_json='true'::jsonb WHERE id=%s",
            (record["matter_value"],),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        rejected.append("temp_shadow_typed_value")
        expect_database_error(
            runtime,
            """
            INSERT INTO public.matter_custom_field_values
              (tenant_id,matter_id,field_definition_id,value_json,value_hmac,updated_by_user_id)
            VALUES (%s,%s,%s,'\"bypass\"'::jsonb,%s,%s)
            """,
            (
                tenant_id,
                fixture["matters"][0],
                inactive_field,
                HASH_A,
                fixture["users"][0],
            ),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        rejected.append("temp_shadow_inactive_field")
        expect_database_error(
            runtime,
            "UPDATE public.matter_workflow_stage_definitions SET label='Shadowed' WHERE id=%s",
            (record["stage"],),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        rejected.append("temp_shadow_approved_definition")
        expect_database_error(
            runtime,
            "DELETE FROM public.matter_workflow_checklist_definitions WHERE id=%s",
            (record["checklist"],),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        rejected.append("temp_shadow_approved_delete")
    return rejected


def assert_demo_purge_lifecycle(
    owner, runtime_url: str, purge_fixture: dict[str, Any]
) -> dict[str, Any]:
    """Exercise the deployed immutable-history carve-out through real purge code."""

    tenant_id = purge_fixture["tenant_id"]
    session_id = purge_fixture["session_id"]
    with connect(runtime_url) as runtime:
        set_tenant(runtime, tenant_id)
        with runtime.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  set_config('app.config_workflow_demo_purge_tenant_id', %s, false),
                  set_config('app.config_workflow_demo_purge_session_id', %s, false)
                """,
                (str(tenant_id), str(uuid.uuid4())),
            )
        runtime.commit()
        expect_database_error(
            runtime,
            "DELETE FROM public.matter_workflow_run_events WHERE run_id=%s",
            (purge_fixture["run_id"],),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        with runtime.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  set_config('app.config_workflow_demo_purge_tenant_id', %s, false),
                  set_config('app.config_workflow_demo_purge_session_id', %s, false)
                """,
                (str(tenant_id), str(session_id)),
            )
        runtime.commit()
        expect_database_error(
            runtime,
            "DELETE FROM public.matter_workflow_stage_definitions WHERE id=%s",
            (purge_fixture["stage_id"],),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        with owner.cursor() as cursor:
            cursor.execute(
                "UPDATE public.tenants SET is_active=false WHERE id=%s",
                (tenant_id,),
            )
            cursor.execute(
                """
                UPDATE public.demo_sessions
                   SET status='purging', purge_started_at=now(),
                       expires_at=now() + interval '1 hour'
                 WHERE id=%s
                """,
                (session_id,),
            )
        owner.commit()
        expect_database_error(
            runtime,
            "DELETE FROM public.matter_workflow_run_events WHERE run_id=%s",
            (purge_fixture["run_id"],),
            sqlstates={"P0001"},
            tenant_id=tenant_id,
        )
        with owner.cursor() as cursor:
            cursor.execute(
                "UPDATE public.tenants SET is_active=true WHERE id=%s",
                (tenant_id,),
            )
            cursor.execute(
                """
                UPDATE public.demo_sessions
                   SET status='active', purge_started_at=NULL,
                       expires_at=now() - interval '1 minute'
                 WHERE id=%s
                """,
                (session_id,),
            )
        owner.commit()

    async def run_verified_purge() -> dict[str, int]:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.demo_purge import purge_demo_tenant

        engine = create_async_engine(runtime_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        try:
            async with sessions() as session:
                return await purge_demo_tenant(session, tenant_id)
        finally:
            await engine.dispose()

    deleted = asyncio.run(run_verified_purge())
    expected = {
        "custom_field_definitions": 2,
        "matter_custom_field_values": 1,
        "contact_custom_field_values": 1,
        "matter_workflow_templates": 1,
        "matter_workflow_template_versions": 1,
        "matter_workflow_stage_definitions": 1,
        "matter_workflow_checklist_definitions": 1,
        "matter_workflow_field_requirements": 1,
        "matter_workflow_runs": 1,
        "matter_workflow_run_events": 1,
        "matter_workflow_run_steps": 1,
    }
    actual = {table: deleted.get(table, 0) for table in TABLES}
    if actual != expected:
        raise AssertionError(f"workflow demo purge counts were incomplete: {actual}")
    with owner.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM tenants WHERE id=%s", (tenant_id,))
        tenant_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT count(*) FROM operator_audit_logs
             WHERE action='demo.session.purged' AND resource_id=%s
            """,
            (str(session_id),),
        )
        audit_count = cursor.fetchone()[0]
    if tenant_count != 0 or audit_count != 1:
        raise AssertionError(
            "verified demo purge lacked tenant removal or terminal audit: "
            f"tenant_count={tenant_count}, audit_count={audit_count}"
        )
    return {
        "invalid_context_rejected": True,
        "mismatched_session_rejected": True,
        "future_session_expiry_rejected": True,
        "deleted_rows": actual,
        "terminal_audit": True,
    }


def assert_concurrent_idempotency(
    runtime_url: str, fixture: dict[str, Any]
) -> list[int]:
    tenant_id = fixture["tenants"][0]
    matter_id = fixture["matters"][0]
    user_id = fixture["users"][0]
    version_id = fixture["records"][0]["version"]
    key = f"concurrent-{uuid.uuid4()}"
    barrier = threading.Barrier(2)
    outcomes: list[int] = []
    failures: list[str] = []
    mutex = threading.Lock()

    def claim() -> None:
        connection = connect(runtime_url)
        try:
            set_tenant(connection, tenant_id)
            with connection.cursor() as cursor:
                barrier.wait(timeout=10)
                cursor.execute(
                    """
                    INSERT INTO matter_workflow_runs
                      (tenant_id,matter_id,template_version_id,idempotency_key,
                       request_sha256,template_sha256,matter_sha256,preview_sha256,
                       preview_json,planned_by_user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'{}',%s)
                    ON CONFLICT (tenant_id,matter_id,idempotency_key)
                    DO NOTHING RETURNING id
                    """,
                    (
                        tenant_id,
                        matter_id,
                        version_id,
                        key,
                        HASH_A,
                        HASH_B,
                        HASH_C,
                        HASH_D,
                        user_id,
                    ),
                )
                rowcount = cursor.rowcount
            connection.commit()
            with mutex:
                outcomes.append(rowcount)
        except Exception as exc:  # pragma: no cover - surfaced by aggregate assertion
            connection.rollback()
            with mutex:
                failures.append(repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=claim, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    if any(thread.is_alive() for thread in threads):
        raise AssertionError("concurrent idempotency rehearsal deadlocked")
    if failures or sorted(outcomes) != [0, 1]:
        raise AssertionError(
            f"expected one durable claim, got {outcomes}; errors={failures}"
        )
    return sorted(outcomes)


def assert_custom_field_contract_serialization(
    owner, runtime_url: str, fixture: dict[str, Any]
) -> dict[str, bool]:
    """Prove first-value writes serialize with field contract changes."""

    tenant_id = fixture["tenants"][0]
    matter_id = fixture["matters"][0]
    actor_user_id = fixture["users"][0]
    value_first_field_id = uuid.uuid4()
    rewrite_first_field_id = uuid.uuid4()
    with owner.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.custom_field_definitions
              (id,tenant_id,entity_type,field_key,label,field_type,
               options_json,created_by_user_id)
            VALUES (%s,%s,'matter','value_first_race','Value first race',
                    'single_select','["Alpha"]'::jsonb,%s),
                   (%s,%s,'matter','rewrite_first_race','Rewrite first race',
                    'single_select','["Alpha"]'::jsonb,%s)
            """,
            (
                value_first_field_id,
                tenant_id,
                actor_user_id,
                rewrite_first_field_id,
                tenant_id,
                actor_user_id,
            ),
        )
    owner.commit()

    def wait_for_lock(
        connection_pid: int, thread: threading.Thread, label: str
    ) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with owner.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT wait_event_type
                      FROM pg_catalog.pg_stat_activity
                     WHERE pid=%s
                    """,
                    (connection_pid,),
                )
                row = cursor.fetchone()
            # PostgreSQL can cache statistics snapshots for a transaction.
            # End this read-only monitor transaction so the next poll observes
            # the worker's current wait state rather than the first sample.
            owner.commit()
            if row is not None and row[0] == "Lock":
                return
            if not thread.is_alive():
                break
            time.sleep(0.05)
        raise AssertionError(f"{label} did not block on the field definition lock")

    # The value transaction holds FOR SHARE on its definition. A concurrent
    # contract rewrite must wait, then observe the committed value and reject.
    value_first = connect(runtime_url)
    blocked_rewrite = connect(runtime_url)
    set_tenant(value_first, tenant_id)
    set_tenant(blocked_rewrite, tenant_id)
    rewrite_pid = blocked_rewrite.get_backend_pid()
    rewrite_started = threading.Event()
    rewrite_outcomes: list[str] = []

    def rewrite_after_value() -> None:
        try:
            with blocked_rewrite.cursor() as cursor:
                rewrite_started.set()
                cursor.execute(
                    """
                    UPDATE public.custom_field_definitions
                       SET options_json='["Beta"]'::jsonb,
                           schema_version=schema_version+1
                     WHERE tenant_id=%s AND id=%s
                    """,
                    (tenant_id, value_first_field_id),
                )
            blocked_rewrite.commit()
            rewrite_outcomes.append("committed")
        except psycopg2.Error as exc:
            blocked_rewrite.rollback()
            rewrite_outcomes.append(exc.pgcode or type(exc).__name__)

    rewrite_thread = threading.Thread(target=rewrite_after_value, daemon=True)
    try:
        with value_first.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.matter_custom_field_values
                  (tenant_id,matter_id,field_definition_id,value_json,
                   value_hmac,updated_by_user_id)
                VALUES (%s,%s,%s,'"Alpha"'::jsonb,%s,%s)
                """,
                (
                    tenant_id,
                    matter_id,
                    value_first_field_id,
                    HASH_A,
                    actor_user_id,
                ),
            )
        rewrite_thread.start()
        if not rewrite_started.wait(timeout=5):
            raise AssertionError("value-first contract rewrite did not start")
        wait_for_lock(rewrite_pid, rewrite_thread, "value-first contract rewrite")
        value_first.commit()
        rewrite_thread.join(timeout=10)
        if rewrite_thread.is_alive():
            raise AssertionError("value-first contract rewrite deadlocked")
    finally:
        if value_first.closed == 0:
            value_first.rollback()
            value_first.close()
        if rewrite_thread.is_alive() and blocked_rewrite.closed == 0:
            blocked_rewrite.cancel()
            rewrite_thread.join(timeout=5)
        rewrite_cleanup_failed = rewrite_thread.is_alive()
        if not rewrite_cleanup_failed and blocked_rewrite.closed == 0:
            blocked_rewrite.rollback()
            blocked_rewrite.close()
        if rewrite_cleanup_failed:
            raise AssertionError("value-first contract rewrite cleanup did not stop")

    if rewrite_outcomes != ["P0001"]:
        raise AssertionError(
            "a contract rewrite crossed the first stored value: " f"{rewrite_outcomes}"
        )
    with owner.cursor() as cursor:
        cursor.execute(
            """
            SELECT options_json, schema_version
              FROM public.custom_field_definitions
             WHERE id=%s
            """,
            (value_first_field_id,),
        )
        value_first_contract = cursor.fetchone()
        cursor.execute(
            """
            SELECT value_json
              FROM public.matter_custom_field_values
             WHERE tenant_id=%s AND field_definition_id=%s
            """,
            (tenant_id, value_first_field_id),
        )
        stored_value = cursor.fetchone()
    if value_first_contract != (["Alpha"], 1) or stored_value != ("Alpha",):
        raise AssertionError(
            "value-first serialization changed the validated contract or value: "
            f"contract={value_first_contract}, value={stored_value}"
        )

    # The inverse order must also serialize: the definition rewrite owns the
    # row first, so an old-option value waits and revalidates against the newly
    # committed contract before it can be stored.
    rewrite_first = connect(runtime_url)
    blocked_value = connect(runtime_url)
    set_tenant(rewrite_first, tenant_id)
    set_tenant(blocked_value, tenant_id)
    value_pid = blocked_value.get_backend_pid()
    value_started = threading.Event()
    value_outcomes: list[str] = []

    def insert_after_rewrite() -> None:
        try:
            with blocked_value.cursor() as cursor:
                value_started.set()
                cursor.execute(
                    """
                    INSERT INTO public.matter_custom_field_values
                      (tenant_id,matter_id,field_definition_id,value_json,
                       value_hmac,updated_by_user_id)
                    VALUES (%s,%s,%s,'"Alpha"'::jsonb,%s,%s)
                    """,
                    (
                        tenant_id,
                        matter_id,
                        rewrite_first_field_id,
                        HASH_A,
                        actor_user_id,
                    ),
                )
            blocked_value.commit()
            value_outcomes.append("committed")
        except psycopg2.Error as exc:
            blocked_value.rollback()
            value_outcomes.append(exc.pgcode or type(exc).__name__)

    value_thread = threading.Thread(target=insert_after_rewrite, daemon=True)
    try:
        with rewrite_first.cursor() as cursor:
            cursor.execute(
                """
                UPDATE public.custom_field_definitions
                   SET options_json='["Beta"]'::jsonb,
                       schema_version=schema_version+1
                 WHERE tenant_id=%s AND id=%s
                """,
                (tenant_id, rewrite_first_field_id),
            )
        value_thread.start()
        if not value_started.wait(timeout=5):
            raise AssertionError("rewrite-first value insert did not start")
        wait_for_lock(value_pid, value_thread, "rewrite-first value insert")
        rewrite_first.commit()
        value_thread.join(timeout=10)
        if value_thread.is_alive():
            raise AssertionError("rewrite-first value insert deadlocked")
    finally:
        if rewrite_first.closed == 0:
            rewrite_first.rollback()
            rewrite_first.close()
        if value_thread.is_alive() and blocked_value.closed == 0:
            blocked_value.cancel()
            value_thread.join(timeout=5)
        value_cleanup_failed = value_thread.is_alive()
        if not value_cleanup_failed and blocked_value.closed == 0:
            blocked_value.rollback()
            blocked_value.close()
        if value_cleanup_failed:
            raise AssertionError("rewrite-first value cleanup did not stop")

    if value_outcomes != ["P0001"]:
        raise AssertionError(
            "an old-option value crossed the contract rewrite: " f"{value_outcomes}"
        )
    with owner.cursor() as cursor:
        cursor.execute(
            """
            SELECT options_json, schema_version
              FROM public.custom_field_definitions
             WHERE id=%s
            """,
            (rewrite_first_field_id,),
        )
        rewrite_first_contract = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*)
              FROM public.matter_custom_field_values
             WHERE tenant_id=%s AND field_definition_id=%s
            """,
            (tenant_id, rewrite_first_field_id),
        )
        stale_value_count = cursor.fetchone()[0]
    if rewrite_first_contract != (["Beta"], 2) or stale_value_count != 0:
        raise AssertionError(
            "rewrite-first serialization stored stale field data: "
            f"contract={rewrite_first_contract}, values={stale_value_count}"
        )

    return {
        "value_blocked_contract_rewrite": True,
        "contract_rewrite_blocked_value": True,
    }


def assert_approval_child_mutation_serialization(
    owner, runtime_url: str, fixture: dict[str, Any]
) -> dict[str, Any]:
    """Prove approval and child mutation cannot cross an immutable snapshot."""

    tenant_id = fixture["tenants"][0]
    actor_user_id = fixture["users"][0]
    template_id = uuid.uuid4()
    version_id = uuid.uuid4()
    stage_id = uuid.uuid4()
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO matter_workflow_templates (id,tenant_id,name,created_by_user_id) VALUES (%s,%s,'Approval race',%s)",
            (template_id, tenant_id, actor_user_id),
        )
        cursor.execute(
            "INSERT INTO matter_workflow_template_versions (id,tenant_id,template_id,version,initial_stage_key,definition_sha256,created_by_user_id) VALUES (%s,%s,%s,1,'initial',%s,%s)",
            (version_id, tenant_id, template_id, HASH_A, actor_user_id),
        )
        cursor.execute(
            "INSERT INTO matter_workflow_stage_definitions (id,tenant_id,template_version_id,stage_key,label,position) VALUES (%s,%s,%s,'initial','Initial',0)",
            (stage_id, tenant_id, version_id),
        )
    owner.commit()

    approval = connect(runtime_url)
    mutation = connect(runtime_url)
    mutation_pid = mutation.get_backend_pid()
    set_tenant(approval, tenant_id)
    set_tenant(mutation, tenant_id)
    started = threading.Event()
    outcomes: list[str] = []

    def mutate_child() -> None:
        try:
            with mutation.cursor() as cursor:
                started.set()
                cursor.execute(
                    "UPDATE public.matter_workflow_stage_definitions SET label='Raced mutation' WHERE id=%s",
                    (stage_id,),
                )
            mutation.commit()
            outcomes.append("committed")
        except psycopg2.Error as exc:
            mutation.rollback()
            outcomes.append(exc.pgcode or type(exc).__name__)

    thread = threading.Thread(target=mutate_child, daemon=True)
    try:
        with approval.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM public.matter_workflow_template_versions WHERE id=%s FOR UPDATE",
                (version_id,),
            )
            cursor.execute(
                "SELECT label FROM public.matter_workflow_stage_definitions WHERE id=%s",
                (stage_id,),
            )
            if cursor.fetchone()[0] != "Initial":
                raise AssertionError("approval race seed was unexpectedly mutated")
            cursor.execute(
                "UPDATE public.matter_workflow_template_versions SET status='approved', approved_by_user_id=%s, approved_at=now() WHERE id=%s",
                (actor_user_id, version_id),
            )
        thread.start()
        if not started.wait(timeout=5):
            raise AssertionError("approval race mutation did not start")
        deadline = time.monotonic() + 5
        blocked_before_approval_commit = False
        while time.monotonic() < deadline:
            with owner.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_catalog.pg_stat_activity WHERE pid=%s",
                    (mutation_pid,),
                )
                row = cursor.fetchone()
            # Keep lock-state polling independent of a cached statistics
            # snapshot in this long-lived owner connection.
            owner.commit()
            if row is not None and row[0] == "Lock":
                blocked_before_approval_commit = True
                break
            if not thread.is_alive():
                break
            time.sleep(0.05)
        approval.commit()
        thread.join(timeout=10)
        if thread.is_alive():
            raise AssertionError("approval race mutation deadlocked")
    finally:
        if approval.closed == 0:
            approval.rollback()
        if thread.is_alive():
            thread.join(timeout=10)
        if thread.is_alive() and mutation.closed == 0:
            mutation.cancel()
            thread.join(timeout=5)
        if approval.closed == 0:
            approval.close()
        mutation_cleanup_failed = thread.is_alive()
        if not mutation_cleanup_failed and mutation.closed == 0:
            mutation.rollback()
            mutation.close()
        if mutation_cleanup_failed:
            raise AssertionError("approval race mutation cleanup did not stop")

    if not blocked_before_approval_commit or outcomes != ["P0001"]:
        raise AssertionError(
            "approval/child serialization failed: "
            f"blocked={blocked_before_approval_commit}, outcomes={outcomes}"
        )
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM public.matter_workflow_template_versions WHERE id=%s",
            (version_id,),
        )
        status = cursor.fetchone()[0]
        cursor.execute(
            "SELECT label FROM public.matter_workflow_stage_definitions WHERE id=%s",
            (stage_id,),
        )
        label = cursor.fetchone()[0]
    if status != "approved" or label != "Initial":
        raise AssertionError(
            f"approval race crossed its snapshot: status={status}, label={label}"
        )
    return {
        "mutation_blocked_until_approval_commit": True,
        "mutation_rejected_after_approval": True,
        "approved_snapshot_unchanged": True,
    }


async def assert_concurrent_apply(
    runtime_url: str, fixture: dict[str, Any]
) -> dict[str, Any]:
    """Exercise the real service lock path against the migrated runtime schema."""

    from fastapi import HTTPException
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import set_tenant_context
    from app.models.configurable_workflow import (
        MatterWorkflowRun,
        MatterWorkflowRunEvent,
        MatterWorkflowRunStep,
    )
    from app.models.plugin import Matter
    from app.models.task import Task
    from app.models.user import User
    from app.routers.configurable_workflows import _matter_or_404, _run_or_404
    from app.services.configurable_workflows import (
        append_run_event,
        apply_run,
        build_preview,
        digest_payload,
        rollback_run,
    )

    tenant_id = fixture["tenants"][0]
    matter_id = fixture["matters"][0]
    actor_user_id = fixture["users"][0]
    version_id = fixture["records"][0]["version"]
    idempotency_key = f"service-apply-{uuid.uuid4()}"
    engine = create_async_engine(runtime_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    try:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            matter = await session.scalar(
                select(Matter).where(
                    Matter.tenant_id == tenant_id, Matter.id == matter_id
                )
            )
            if matter is None:
                raise AssertionError("service rehearsal matter is not tenant-visible")
            (
                preview,
                preview_sha256,
                template_sha256,
                matter_sha256,
            ) = await build_preview(
                session,
                matter=matter,
                version_id=version_id,
                as_of=date.today(),
            )
            run = MatterWorkflowRun(
                tenant_id=tenant_id,
                matter_id=matter_id,
                template_version_id=version_id,
                idempotency_key=idempotency_key,
                request_sha256=digest_payload(
                    {
                        "matter_id": str(matter_id),
                        "template_version_id": str(version_id),
                    }
                ),
                template_sha256=template_sha256,
                matter_sha256=matter_sha256,
                preview_sha256=preview_sha256,
                preview_json=preview,
                prior_stage=matter.stage,
                planned_by_user_id=actor_user_id,
            )
            session.add(run)
            await session.flush()
            await append_run_event(
                session,
                run,
                event_type="previewed",
                actor_user_id=actor_user_id,
                detail={"preview_sha256": preview_sha256},
            )
            await session.commit()
            run_id = run.id

        barrier = asyncio.Barrier(2)

        async def apply_once() -> str:
            async with sessions() as session:
                await set_tenant_context(session, str(tenant_id))
                await barrier.wait()
                locked_run = await _run_or_404(
                    session,
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    run_id=run_id,
                    lock=True,
                )
                locked_matter = await _matter_or_404(
                    session, tenant_id, matter_id, lock=True
                )
                await apply_run(
                    session,
                    run=locked_run,
                    matter=locked_matter,
                    actor_user_id=actor_user_id,
                    preview_sha256=preview_sha256,
                )
                await session.commit()
                return locked_run.status

        statuses = await asyncio.gather(apply_once(), apply_once())

        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            task_count = await session.scalar(
                select(func.count(Task.id)).where(
                    Task.tenant_id == tenant_id,
                    Task.external_ref.like(f"workflow:{run_id}:%"),
                )
            )
            applied_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == run_id,
                    MatterWorkflowRunEvent.event_type == "applied",
                )
            )
            created_steps = await session.scalar(
                select(func.count(MatterWorkflowRunStep.id)).where(
                    MatterWorkflowRunStep.tenant_id == tenant_id,
                    MatterWorkflowRunStep.run_id == run_id,
                    MatterWorkflowRunStep.step_type == "task_create",
                )
            )
        evidence = {
            "statuses": sorted(statuses),
            "task_count": task_count,
            "applied_event_count": applied_events,
            "task_create_step_count": created_steps,
        }
        if evidence != {
            "statuses": ["applied", "applied"],
            "task_count": 2,
            "applied_event_count": 1,
            "task_create_step_count": 2,
        }:
            raise AssertionError(f"concurrent apply was not exactly-once: {evidence}")

        # Make the created task ineligible for automatic cancellation, then
        # prove a same-key compensation replay returns the immutable blockers
        # without appending another request/event/step set.
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            created_task = await session.scalar(
                select(Task)
                .where(
                    Task.tenant_id == tenant_id,
                    Task.external_ref == f"workflow:{run_id}:review",
                )
                .with_for_update()
            )
            if created_task is None:
                raise AssertionError("concurrent apply task is missing")
            created_task.title = f"{created_task.title} (reviewed)"
            created_task.version += 1
            await session.commit()

        rollback_key = f"service-rollback-{uuid.uuid4()}"
        rollback_reason = "Rehearse compensating boundary"
        rollback_sha256 = digest_payload(
            {"run_id": str(run_id), "reason": rollback_reason}
        )

        async def rollback_once() -> tuple[str, list[str]]:
            async with sessions() as session:
                await set_tenant_context(session, str(tenant_id))
                locked_run = await _run_or_404(
                    session,
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    run_id=run_id,
                    lock=True,
                )
                locked_matter = await _matter_or_404(
                    session, tenant_id, matter_id, lock=True
                )
                result, blockers = await rollback_run(
                    session,
                    run=locked_run,
                    matter=locked_matter,
                    actor_user_id=actor_user_id,
                    idempotency_key=rollback_key,
                    request_sha256=rollback_sha256,
                    reason=rollback_reason,
                )
                await session.commit()
                return result.status, blockers

        first_rollback = await rollback_once()
        replayed_rollback = await rollback_once()
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            rollback_requested_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == run_id,
                    MatterWorkflowRunEvent.event_type == "rollback_requested",
                )
            )
            rollback_blocked_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == run_id,
                    MatterWorkflowRunEvent.event_type == "rollback_blocked",
                )
            )
            blocked_steps = await session.scalar(
                select(func.count(MatterWorkflowRunStep.id)).where(
                    MatterWorkflowRunStep.tenant_id == tenant_id,
                    MatterWorkflowRunStep.run_id == run_id,
                    MatterWorkflowRunStep.status == "blocked",
                )
            )
        compensation = {
            "first_status": first_rollback[0],
            "replayed_status": replayed_rollback[0],
            "same_blockers": first_rollback[1] == replayed_rollback[1],
            "blocker_count": len(first_rollback[1]),
            "rollback_requested_event_count": rollback_requested_events,
            "rollback_blocked_event_count": rollback_blocked_events,
            "blocked_step_count": blocked_steps,
        }
        expected_compensation = {
            "first_status": "compensation_required",
            "replayed_status": "compensation_required",
            "same_blockers": True,
            "blocker_count": 1,
            "rollback_requested_event_count": 1,
            "rollback_blocked_event_count": 1,
            "blocked_step_count": 1,
        }
        if compensation != expected_compensation:
            raise AssertionError(
                f"compensating rollback replay was not idempotent: {compensation}"
            )
        evidence["compensating_rollback"] = compensation

        # Production sessions use autoflush=False. Exercise apply plus a
        # successful multi-step rollback in one transaction so every event and
        # step allocator must make its prior append visible before MAX+1.
        success_key = f"service-successful-rollback-{uuid.uuid4()}"
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            matter = await session.scalar(
                select(Matter).where(
                    Matter.tenant_id == tenant_id, Matter.id == matter_id
                )
            )
            if matter is None:
                raise AssertionError("successful rollback matter is not visible")
            (
                success_preview,
                success_sha,
                template_sha,
                matter_sha,
            ) = await build_preview(
                session,
                matter=matter,
                version_id=version_id,
                as_of=date.today(),
            )
            success_run = MatterWorkflowRun(
                tenant_id=tenant_id,
                matter_id=matter_id,
                template_version_id=version_id,
                idempotency_key=success_key,
                request_sha256=digest_payload(
                    {
                        "matter_id": str(matter_id),
                        "template_version_id": str(version_id),
                    }
                ),
                template_sha256=template_sha,
                matter_sha256=matter_sha,
                preview_sha256=success_sha,
                preview_json=success_preview,
                prior_stage=matter.stage,
                planned_by_user_id=actor_user_id,
            )
            session.add(success_run)
            await session.flush()
            await append_run_event(
                session,
                success_run,
                event_type="previewed",
                actor_user_id=actor_user_id,
                detail={"preview_sha256": success_sha},
            )
            await session.commit()
            success_run_id = success_run.id

        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            locked_run = await _run_or_404(
                session,
                tenant_id=tenant_id,
                matter_id=matter_id,
                run_id=success_run_id,
                lock=True,
            )
            locked_matter = await _matter_or_404(
                session, tenant_id, matter_id, lock=True
            )
            await apply_run(
                session,
                run=locked_run,
                matter=locked_matter,
                actor_user_id=actor_user_id,
                preview_sha256=success_sha,
            )
            reason = "Rehearse successful multi-step rollback"
            result, blockers = await rollback_run(
                session,
                run=locked_run,
                matter=locked_matter,
                actor_user_id=actor_user_id,
                idempotency_key=f"rollback-{uuid.uuid4()}",
                request_sha256=digest_payload(
                    {"run_id": str(success_run_id), "reason": reason}
                ),
                reason=reason,
            )
            if blockers or result.status != "rolled_back":
                raise AssertionError(
                    f"successful rollback required compensation: {blockers}"
                )
            await session.commit()

        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            event_sequences = (
                (
                    await session.execute(
                        select(MatterWorkflowRunEvent.sequence)
                        .where(
                            MatterWorkflowRunEvent.tenant_id == tenant_id,
                            MatterWorkflowRunEvent.run_id == success_run_id,
                        )
                        .order_by(MatterWorkflowRunEvent.sequence)
                    )
                )
                .scalars()
                .all()
            )
            step_sequences = (
                (
                    await session.execute(
                        select(MatterWorkflowRunStep.sequence)
                        .where(
                            MatterWorkflowRunStep.tenant_id == tenant_id,
                            MatterWorkflowRunStep.run_id == success_run_id,
                        )
                        .order_by(MatterWorkflowRunStep.sequence)
                    )
                )
                .scalars()
                .all()
            )
        successful_rollback = {
            "status": "rolled_back",
            "event_sequences": event_sequences,
            "step_sequences": step_sequences,
        }
        if event_sequences != list(range(1, 6)) or step_sequences != list(range(1, 7)):
            raise AssertionError(
                "autoflush-disabled rollback evidence sequences were not contiguous: "
                f"{successful_rollback}"
            )
        evidence["successful_rollback"] = successful_rollback

        # Rehearse a later-step failure after the first task has flushed. The
        # enclosing transaction must leave only the original preview evidence:
        # no created task, apply step/event, status change, or stage mutation.
        failed_key = f"service-failed-apply-{uuid.uuid4()}"
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            matter = await session.scalar(
                select(Matter).where(
                    Matter.tenant_id == tenant_id, Matter.id == matter_id
                )
            )
            if matter is None:
                raise AssertionError("failed-apply matter is not tenant-visible")
            prior_failed_stage = matter.stage
            (
                failed_preview,
                failed_preview_sha256,
                failed_template_sha256,
                failed_matter_sha256,
            ) = await build_preview(
                session,
                matter=matter,
                version_id=version_id,
                as_of=date.today(),
            )
            failed_run = MatterWorkflowRun(
                tenant_id=tenant_id,
                matter_id=matter_id,
                template_version_id=version_id,
                idempotency_key=failed_key,
                request_sha256=digest_payload(
                    {
                        "matter_id": str(matter_id),
                        "template_version_id": str(version_id),
                    }
                ),
                template_sha256=failed_template_sha256,
                matter_sha256=failed_matter_sha256,
                preview_sha256=failed_preview_sha256,
                preview_json=failed_preview,
                prior_stage=prior_failed_stage,
                planned_by_user_id=actor_user_id,
            )
            session.add(failed_run)
            await session.flush()
            await append_run_event(
                session,
                failed_run,
                event_type="previewed",
                actor_user_id=actor_user_id,
                detail={"preview_sha256": failed_preview_sha256},
            )
            await session.commit()
            failed_run_id = failed_run.id

        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            actor = await session.scalar(
                select(User)
                .where(User.tenant_id == tenant_id, User.id == actor_user_id)
                .with_for_update()
            )
            if actor is None:
                raise AssertionError("failed-apply actor is not tenant-visible")
            actor.is_active = False
            await session.commit()

        failure_status = None
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            locked_run = await _run_or_404(
                session,
                tenant_id=tenant_id,
                matter_id=matter_id,
                run_id=failed_run_id,
                lock=True,
            )
            locked_matter = await _matter_or_404(
                session, tenant_id, matter_id, lock=True
            )
            try:
                await apply_run(
                    session,
                    run=locked_run,
                    matter=locked_matter,
                    actor_user_id=actor_user_id,
                    preview_sha256=failed_preview_sha256,
                )
                await session.commit()
            except HTTPException as exc:
                failure_status = exc.status_code
                await session.rollback()
            else:
                raise AssertionError("inactive later assignee did not fail apply")

        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            persisted_failed_run = await _run_or_404(
                session,
                tenant_id=tenant_id,
                matter_id=matter_id,
                run_id=failed_run_id,
            )
            persisted_matter = await _matter_or_404(session, tenant_id, matter_id)
            failed_task_count = await session.scalar(
                select(func.count(Task.id)).where(
                    Task.tenant_id == tenant_id,
                    Task.external_ref.like(f"workflow:{failed_run_id}:%"),
                )
            )
            failed_apply_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == failed_run_id,
                    MatterWorkflowRunEvent.event_type.in_(("approved", "applied")),
                )
            )
            failed_steps = await session.scalar(
                select(func.count(MatterWorkflowRunStep.id)).where(
                    MatterWorkflowRunStep.tenant_id == tenant_id,
                    MatterWorkflowRunStep.run_id == failed_run_id,
                )
            )
            actor = await session.scalar(
                select(User)
                .where(User.tenant_id == tenant_id, User.id == actor_user_id)
                .with_for_update()
            )
            if actor is None:
                raise AssertionError("failed-apply actor disappeared")
            actor.is_active = True
            await session.commit()
        failed_apply = {
            "http_status": failure_status,
            "run_status": persisted_failed_run.status,
            "matter_stage_unchanged": persisted_matter.stage == prior_failed_stage,
            "task_count": failed_task_count,
            "apply_event_count": failed_apply_events,
            "step_count": failed_steps,
        }
        expected_failed_apply = {
            "http_status": 409,
            "run_status": "planned",
            "matter_stage_unchanged": True,
            "task_count": 0,
            "apply_event_count": 0,
            "step_count": 0,
        }
        if failed_apply != expected_failed_apply:
            raise AssertionError(f"failed apply left partial effects: {failed_apply}")
        evidence["failed_apply"] = failed_apply
        return evidence
    finally:
        await engine.dispose()


async def assert_workflow_dependency_serialization(
    owner_url: str,
    runtime_url: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Prove apply sees one coherent config/data/assignee snapshot.

    Every case uses two READ COMMITTED runtime-role sessions with production
    ``autoflush=False``. We exercise both commit orders and verify the second
    transaction really waits in PostgreSQL before the first is released.
    """

    from fastapi import HTTPException
    from sqlalchemy import delete, func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import set_tenant_context
    from app.models.configurable_workflow import (
        CustomFieldDefinition,
        MatterCustomFieldValue,
        MatterWorkflowRun,
        MatterWorkflowRunEvent,
        MatterWorkflowRunStep,
        MatterWorkflowTemplate,
    )
    from app.models.task import Task
    from app.models.user import User
    from app.routers.configurable_workflows import _matter_or_404, _run_or_404
    from app.services.configurable_workflows import (
        acquire_workflow_config_lock,
        append_run_event,
        apply_run,
        build_preview,
        digest_payload,
    )

    tenant_id = fixture["tenants"][0]
    matter_id = fixture["matters"][0]
    actor_user_id = fixture["users"][0]
    record = fixture["records"][0]
    version_id = record["version"]
    template_id = record["template"]
    field_id = record["field"]
    value_id = record["matter_value"]
    engine = create_async_engine(runtime_url, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    def wait_for_backend_lock(pid: int, label: str) -> None:
        monitor = connect(owner_url)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with monitor.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT wait_event_type
                          FROM pg_catalog.pg_stat_activity
                         WHERE pid=%s
                        """,
                        (pid,),
                    )
                    row = cursor.fetchone()
                monitor.commit()
                if row is not None and row[0] == "Lock":
                    return
                time.sleep(0.05)
        finally:
            monitor.close()
        raise AssertionError(f"{label} did not enter a PostgreSQL lock wait")

    async def backend_pid(session) -> int:
        return int(await session.scalar(text("SELECT pg_backend_pid()")))

    async def create_planned_run(label: str) -> tuple[uuid.UUID, str, str]:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            matter = await _matter_or_404(session, tenant_id, matter_id, lock=True)
            await acquire_workflow_config_lock(session, tenant_id, shared=True)
            preview, preview_sha, template_sha, matter_sha = await build_preview(
                session,
                matter=matter,
                version_id=version_id,
                as_of=date.today(),
                lock_dependencies=True,
            )
            run = MatterWorkflowRun(
                tenant_id=tenant_id,
                matter_id=matter_id,
                template_version_id=version_id,
                idempotency_key=f"dependency-{label}-{uuid.uuid4()}",
                request_sha256=digest_payload(
                    {
                        "matter_id": str(matter_id),
                        "template_version_id": str(version_id),
                    }
                ),
                template_sha256=template_sha,
                matter_sha256=matter_sha,
                preview_sha256=preview_sha,
                preview_json=preview,
                prior_stage=matter.stage,
                planned_by_user_id=actor_user_id,
            )
            session.add(run)
            await session.flush()
            await append_run_event(
                session,
                run,
                event_type="previewed",
                actor_user_id=actor_user_id,
                detail={"preview_sha256": preview_sha},
            )
            await session.commit()
            return run.id, preview_sha, run.prior_stage

    async def apply_attempt(
        run_id: uuid.UUID,
        preview_sha: str,
        pid_ready: asyncio.Future[int],
        applied_ready: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> int:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            pid_ready.set_result(await backend_pid(session))
            try:
                run = await _run_or_404(
                    session,
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    run_id=run_id,
                    lock=True,
                )
                matter = await _matter_or_404(session, tenant_id, matter_id, lock=True)
                await apply_run(
                    session,
                    run=run,
                    matter=matter,
                    actor_user_id=actor_user_id,
                    preview_sha256=preview_sha,
                )
                if applied_ready is not None:
                    applied_ready.set()
                if release is not None:
                    await release.wait()
                await session.commit()
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    async def preview_attempt(
        pid_ready: asyncio.Future[int],
        preview_ready: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            pid_ready.set_result(await backend_pid(session))
            matter = await _matter_or_404(session, tenant_id, matter_id, lock=True)
            await acquire_workflow_config_lock(session, tenant_id, shared=True)
            preview, _preview_sha, _template_sha, matter_sha = await build_preview(
                session,
                matter=matter,
                version_id=version_id,
                as_of=date.today(),
                lock_dependencies=True,
            )
            if preview_ready is not None:
                preview_ready.set()
            if release is not None:
                await release.wait()
            await session.commit()
            return {
                "matter_sha256": matter_sha,
                "can_apply": preview["can_apply"],
            }

    async def assert_applied_once(run_id: uuid.UUID, preview_sha: str) -> None:
        # An applied replay must not re-run stale checks or duplicate evidence.
        replay_pid = asyncio.get_running_loop().create_future()
        if await apply_attempt(run_id, preview_sha, replay_pid) != 200:
            raise AssertionError("applied run did not replay idempotently")
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            run = await _run_or_404(
                session,
                tenant_id=tenant_id,
                matter_id=matter_id,
                run_id=run_id,
            )
            task_count = await session.scalar(
                select(func.count(Task.id)).where(
                    Task.tenant_id == tenant_id,
                    Task.external_ref.like(f"workflow:{run_id}:%"),
                )
            )
            approved_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == run_id,
                    MatterWorkflowRunEvent.event_type == "approved",
                )
            )
            applied_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == run_id,
                    MatterWorkflowRunEvent.event_type == "applied",
                )
            )
            task_steps = await session.scalar(
                select(func.count(MatterWorkflowRunStep.id)).where(
                    MatterWorkflowRunStep.tenant_id == tenant_id,
                    MatterWorkflowRunStep.run_id == run_id,
                    MatterWorkflowRunStep.step_type == "task_create",
                )
            )
        evidence = (
            run.status,
            task_count,
            approved_events,
            applied_events,
            task_steps,
        )
        if evidence != ("applied", 2, 1, 1, 2):
            raise AssertionError(f"apply/replay was not exactly once: {evidence}")

    async def assert_stale_zero_effects(
        run_id: uuid.UUID, prior_stage: str, status: int
    ) -> None:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            run = await _run_or_404(
                session,
                tenant_id=tenant_id,
                matter_id=matter_id,
                run_id=run_id,
            )
            matter = await _matter_or_404(session, tenant_id, matter_id)
            task_count = await session.scalar(
                select(func.count(Task.id)).where(
                    Task.tenant_id == tenant_id,
                    Task.external_ref.like(f"workflow:{run_id}:%"),
                )
            )
            apply_events = await session.scalar(
                select(func.count(MatterWorkflowRunEvent.id)).where(
                    MatterWorkflowRunEvent.tenant_id == tenant_id,
                    MatterWorkflowRunEvent.run_id == run_id,
                    MatterWorkflowRunEvent.event_type.in_(("approved", "applied")),
                )
            )
            steps = await session.scalar(
                select(func.count(MatterWorkflowRunStep.id)).where(
                    MatterWorkflowRunStep.tenant_id == tenant_id,
                    MatterWorkflowRunStep.run_id == run_id,
                )
            )
        evidence = (
            status,
            run.status,
            matter.stage,
            task_count,
            apply_events,
            steps,
        )
        if evidence != (409, "planned", prior_stage, 0, 0, 0):
            raise AssertionError(f"stale apply left partial effects: {evidence}")

    async def mutate_archive(session, active: bool) -> None:
        await acquire_workflow_config_lock(session, tenant_id, shared=False)
        template = await session.scalar(
            select(MatterWorkflowTemplate)
            .where(
                MatterWorkflowTemplate.tenant_id == tenant_id,
                MatterWorkflowTemplate.id == template_id,
            )
            .with_for_update(of=MatterWorkflowTemplate)
        )
        template.active = active

    async def mutate_field_active(session, active: bool) -> None:
        await acquire_workflow_config_lock(session, tenant_id, shared=False)
        field = await session.scalar(
            select(CustomFieldDefinition)
            .where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.id == field_id,
            )
            .with_for_update(of=CustomFieldDefinition)
        )
        field.active = active
        field.schema_version += 1

    phantom_id = uuid.uuid4()

    async def insert_phantom(session) -> None:
        await acquire_workflow_config_lock(session, tenant_id, shared=False)
        session.add(
            CustomFieldDefinition(
                id=phantom_id,
                tenant_id=tenant_id,
                entity_type="matter",
                field_key=f"race_phantom_{phantom_id.hex}",
                label="Race phantom",
                field_type="text",
                created_by_user_id=actor_user_id,
            )
        )
        await session.flush()

    async def delete_phantom(session) -> None:
        await acquire_workflow_config_lock(session, tenant_id, shared=False)
        await session.execute(
            delete(CustomFieldDefinition).where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.id == phantom_id,
            )
        )

    async def mutate_value(session, value: str, value_digest: str) -> None:
        await _matter_or_404(session, tenant_id, matter_id, lock=True)
        stored = await session.scalar(
            select(MatterCustomFieldValue)
            .where(
                MatterCustomFieldValue.tenant_id == tenant_id,
                MatterCustomFieldValue.id == value_id,
            )
            .with_for_update(of=MatterCustomFieldValue)
        )
        stored.value_json = value
        stored.value_hmac = value_digest
        stored.updated_by_user_id = actor_user_id

    async def mutate_user_active(session, active: bool) -> None:
        user = await session.scalar(
            select(User)
            .where(User.tenant_id == tenant_id, User.id == actor_user_id)
            .with_for_update(of=User)
        )
        user.is_active = active

    async def run_writer(
        mutation,
        pid_ready: asyncio.Future[int],
        mutated_ready: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            pid_ready.set_result(await backend_pid(session))
            await mutation(session)
            if mutated_ready is not None:
                mutated_ready.set()
            if release is not None:
                await release.wait()
            await session.commit()

    async def reset(mutation) -> None:
        async with sessions() as session:
            await set_tenant_context(session, str(tenant_id))
            await mutation(session)
            await session.commit()

    scenarios = [
        (
            "archive",
            lambda session: mutate_archive(session, False),
            lambda session: mutate_archive(session, True),
        ),
        (
            "field_deactivation",
            lambda session: mutate_field_active(session, False),
            lambda session: mutate_field_active(session, True),
        ),
        ("active_field_phantom", insert_phantom, delete_phantom),
        (
            "matter_value",
            lambda session: mutate_value(session, "P2", HASH_B),
            lambda session: mutate_value(session, "P1", HASH_A),
        ),
        (
            "assignee_deactivation",
            lambda session: mutate_user_active(session, False),
            lambda session: mutate_user_active(session, True),
        ),
    ]
    evidence: dict[str, Any] = {}
    try:
        for label, mutation, restore in scenarios:
            # Apply owns its dependency locks first; the writer must wait, then
            # apply commits exactly once and replay remains idempotent.
            run_id, preview_sha, _prior_stage = await create_planned_run(
                f"{label}-apply-first"
            )
            apply_pid = asyncio.get_running_loop().create_future()
            applied_ready = asyncio.Event()
            release_apply = asyncio.Event()
            apply_task = asyncio.create_task(
                apply_attempt(
                    run_id,
                    preview_sha,
                    apply_pid,
                    applied_ready,
                    release_apply,
                )
            )
            await applied_ready.wait()
            writer_pid = asyncio.get_running_loop().create_future()
            writer_task = asyncio.create_task(run_writer(mutation, writer_pid))
            try:
                await asyncio.to_thread(
                    wait_for_backend_lock,
                    await writer_pid,
                    f"{label} apply-first writer",
                )
            finally:
                release_apply.set()
            apply_status, _ = await asyncio.gather(apply_task, writer_task)
            if apply_status != 200:
                raise AssertionError(f"{label} apply-first returned {apply_status}")
            await assert_applied_once(run_id, preview_sha)
            await reset(restore)

            # The writer owns its lock first; apply must wait and then reject
            # the stale preview without any stage/task/run-evidence effects.
            run_id, preview_sha, prior_stage = await create_planned_run(
                f"{label}-writer-first"
            )
            writer_pid = asyncio.get_running_loop().create_future()
            mutated_ready = asyncio.Event()
            release_writer = asyncio.Event()
            writer_task = asyncio.create_task(
                run_writer(
                    mutation,
                    writer_pid,
                    mutated_ready,
                    release_writer,
                )
            )
            await mutated_ready.wait()
            apply_pid = asyncio.get_running_loop().create_future()
            apply_task = asyncio.create_task(
                apply_attempt(run_id, preview_sha, apply_pid)
            )
            try:
                await asyncio.to_thread(
                    wait_for_backend_lock,
                    await apply_pid,
                    f"{label} writer-first apply",
                )
            finally:
                release_writer.set()
            _, apply_status = await asyncio.gather(writer_task, apply_task)
            await assert_stale_zero_effects(run_id, prior_stage, apply_status)
            await reset(restore)
            evidence[label] = {
                "apply_first_writer_blocked": True,
                "apply_first_exactly_once_and_replayed": True,
                "writer_first_apply_blocked": True,
                "writer_first_stale_409_zero_effects": True,
            }

        preview_scenarios = [
            (
                "matter_value",
                lambda session: mutate_value(session, "P2", HASH_B),
                lambda session: mutate_value(session, "P1", HASH_A),
                True,
            ),
            (
                "field_deactivation",
                lambda session: mutate_field_active(session, False),
                lambda session: mutate_field_active(session, True),
                False,
            ),
        ]
        preview_evidence: dict[str, Any] = {}
        for label, mutation, restore, writer_first_can_apply in preview_scenarios:
            baseline_pid = asyncio.get_running_loop().create_future()
            baseline = await preview_attempt(baseline_pid)

            # Preview owns matter/config/dependency locks; the writer waits and
            # the returned preview remains the pre-mutation snapshot.
            preview_pid = asyncio.get_running_loop().create_future()
            preview_ready = asyncio.Event()
            release_preview = asyncio.Event()
            preview_task = asyncio.create_task(
                preview_attempt(preview_pid, preview_ready, release_preview)
            )
            await preview_ready.wait()
            writer_pid = asyncio.get_running_loop().create_future()
            writer_task = asyncio.create_task(run_writer(mutation, writer_pid))
            try:
                await asyncio.to_thread(
                    wait_for_backend_lock,
                    await writer_pid,
                    f"{label} preview-first writer",
                )
            finally:
                release_preview.set()
            preview_result, _ = await asyncio.gather(preview_task, writer_task)
            if preview_result != baseline:
                raise AssertionError(
                    f"{label} preview crossed a blocked writer: "
                    f"baseline={baseline}, result={preview_result}"
                )
            await reset(restore)

            # The writer owns matter/config first; preview waits and then must
            # include the committed value or active-field membership change.
            writer_pid = asyncio.get_running_loop().create_future()
            mutated_ready = asyncio.Event()
            release_writer = asyncio.Event()
            writer_task = asyncio.create_task(
                run_writer(
                    mutation,
                    writer_pid,
                    mutated_ready,
                    release_writer,
                )
            )
            await mutated_ready.wait()
            preview_pid = asyncio.get_running_loop().create_future()
            preview_task = asyncio.create_task(preview_attempt(preview_pid))
            try:
                await asyncio.to_thread(
                    wait_for_backend_lock,
                    await preview_pid,
                    f"{label} writer-first preview",
                )
            finally:
                release_writer.set()
            _, preview_result = await asyncio.gather(writer_task, preview_task)
            if (
                preview_result["matter_sha256"] == baseline["matter_sha256"]
                or preview_result["can_apply"] is not writer_first_can_apply
            ):
                raise AssertionError(
                    f"{label} writer-first preview missed committed state: "
                    f"baseline={baseline}, result={preview_result}"
                )
            await reset(restore)
            preview_evidence[label] = {
                "preview_first_writer_blocked": True,
                "preview_first_snapshot_coherent": True,
                "writer_first_preview_blocked": True,
                "writer_first_snapshot_includes_mutation": True,
            }
        evidence["preview_snapshot_races"] = preview_evidence
        return evidence
    finally:
        await engine.dispose()


def main() -> int:
    owner_url = os.environ.get("MIGRATOR_DATABASE_URL") or os.environ.get(
        "DATABASE_URL"
    )
    runtime_url = os.environ.get("RLS_TEST_DATABASE_URL")
    if not owner_url or not runtime_url:
        raise SystemExit(
            "MIGRATOR_DATABASE_URL (or DATABASE_URL) and RLS_TEST_DATABASE_URL are required"
        )

    runtime_role = make_url(runtime_url).username
    if not runtime_role:
        raise SystemExit("RLS_TEST_DATABASE_URL must include a runtime role")

    with connect(owner_url) as owner:
        with owner.cursor() as cursor:
            cursor.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            )
            deployed_heads = validate_deployed_revision_graph(
                tuple(row[0] for row in cursor.fetchall())
            )
        assert_catalog(owner, runtime_role)
        fixture = seed_fixture(owner)
        approved_transitions = assert_seeded_draft_approval_transitions(owner, fixture)
        purge_fixture = seed_demo_purge_fixture(owner, fixture["tenants"][0])
        visible = assert_effective_rls(runtime_url, fixture)
        rejected, draft_child_mutations = assert_integrity_and_immutability(
            owner, fixture
        )
        shadow_rejections = assert_temp_shadow_resistance(runtime_url, fixture)
        demo_purge = assert_demo_purge_lifecycle(owner, runtime_url, purge_fixture)
        field_contract_serialization = assert_custom_field_contract_serialization(
            owner, runtime_url, fixture
        )
        approval_serialization = assert_approval_child_mutation_serialization(
            owner, runtime_url, fixture
        )
        concurrent = assert_concurrent_idempotency(runtime_url, fixture)
        concurrent_apply = asyncio.run(assert_concurrent_apply(runtime_url, fixture))
        dependency_serialization = asyncio.run(
            assert_workflow_dependency_serialization(owner_url, runtime_url, fixture)
        )

    print(
        json.dumps(
            {
                "alembic_version": (
                    deployed_heads[0] if len(deployed_heads) == 1 else None
                ),
                "alembic_heads": list(deployed_heads),
                "required_revision": REQUIRED_REVISION,
                "catalog_tables": list(TABLES),
                "runtime_role": {"superuser": False, "bypassrls": False},
                "visible_rows_by_tenant": visible,
                "database_rejections": rejected,
                "draft_child_mutations": draft_child_mutations,
                "draft_to_approved_transitions": approved_transitions,
                "temp_shadow_rejections": shadow_rejections,
                "verified_demo_purge": demo_purge,
                "custom_field_contract_serialization": field_contract_serialization,
                "approval_child_serialization": approval_serialization,
                "concurrent_claim_rowcounts": concurrent,
                "concurrent_apply": concurrent_apply,
                "workflow_dependency_serialization": dependency_serialization,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
