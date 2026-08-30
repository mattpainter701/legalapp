#!/usr/bin/env python3
"""Mandatory disposable-PostgreSQL integrity rehearsal for COMP-08."""

import os
import threading
import uuid

import psycopg2
from psycopg2.extras import register_uuid
from sqlalchemy.engine import make_url

register_uuid()

PRE146_MATTER = uuid.UUID("23c079f1-73c4-4ce9-b872-7ff9e4d0ec12")


def connect(user=None, password=None):
    parsed = make_url(os.environ["RESEARCH_WORKSPACE_REHEARSAL_DATABASE_URL"])
    return psycopg2.connect(dbname=parsed.database, user=user or parsed.username, password=password or parsed.password, host=parsed.host, port=parsed.port)


def must_fail(cur, statement, params=()):
    try:
        cur.execute(statement, params)
    except psycopg2.Error:
        cur.connection.rollback()
        return
    raise AssertionError(f"expected constraint failure: {statement}")


def must_fail_with_code(cur, statement, params, expected_code):
    try:
        cur.execute(statement, params)
    except psycopg2.Error as exc:
        cur.connection.rollback()
        assert exc.pgcode == expected_code, f"expected SQLSTATE {expected_code}, received {exc.pgcode}"
        return
    raise AssertionError(f"expected SQLSTATE {expected_code}: {statement}")


def concurrently(*operations):
    """Run independent transactions together and return commit/error outcomes."""
    gate, results = threading.Barrier(len(operations)), []
    mutex = threading.Lock()

    def run(operation):
        conn = connect()
        try:
            with conn.cursor() as cur:
                gate.wait(timeout=10)
                operation(cur)
                conn.commit()
            outcome = "committed"
        except Exception as exc:
            conn.rollback()
            outcome = f"rejected:{getattr(exc, 'pgcode', type(exc).__name__)}"
        finally:
            conn.close()
        with mutex:
            results.append(outcome)

    threads = [threading.Thread(target=run, args=(operation,), daemon=True) for operation in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads), "concurrent rehearsal deadlocked"
    return results


def main():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_active_working FROM matter_assignments WHERE matter_id=%s", (PRE146_MATTER,))
            assert cur.fetchone() == (True,), "pre-146 assigned-matter baseline was not retained"
            cur.execute("SELECT 1 FROM pg_constraint WHERE conname='uq_matters_tenant_id'")
            assert cur.fetchone() == (1,), "online upgrade did not add the composite matter key"
    token = uuid.uuid4().hex
    runtime_role, runtime_password = "research_workspace_runtime", "research-workspace-rehearsal"
    ta, tb = uuid.uuid4(), uuid.uuid4()
    owner, assigned, other, foreign = (uuid.uuid4() for _ in range(4))
    matter_a, matter_b, workspace_a, workspace_b, folder, record = (uuid.uuid4() for _ in range(6))
    with connect() as conn:
        with conn.cursor() as cur:
            for tenant, label in ((ta, "a"), (tb, "b")):
                cur.execute("INSERT INTO tenants (id,name,domain) VALUES (%s,%s,%s)", (tenant, f"research {label}", f"research-{label}-{token}.invalid"))
            for user, tenant in ((owner, ta), (assigned, ta), (other, ta), (foreign, tb)):
                cur.execute("INSERT INTO users (id,tenant_id,email,full_name) VALUES (%s,%s,%s,%s)", (user, tenant, f"{user}@{token}.invalid", "Research rehearsal"))
            cur.execute("INSERT INTO matters (id,tenant_id,user_id,slug,matter_name) VALUES (%s,%s,%s,%s,%s)", (matter_a, ta, owner, f"research-a-{token}", "Research A"))
            cur.execute("INSERT INTO matters (id,tenant_id,user_id,slug,matter_name) VALUES (%s,%s,%s,%s,%s)", (matter_b, tb, foreign, f"research-b-{token}", "Research B"))
            cur.execute("INSERT INTO matter_assignments (tenant_id,matter_id,user_id,role) VALUES (%s,%s,%s,'associate')", (ta, matter_a, assigned))
            for workspace, title in ((workspace_a, "Rehearsal A"), (workspace_b, "Rehearsal B")):
                cur.execute("INSERT INTO research_workspaces (id,tenant_id,matter_id,title,created_by_user_id) VALUES (%s,%s,%s,%s,%s)", (workspace, ta, matter_a, title, owner))
                cur.execute("INSERT INTO research_workspace_members (tenant_id,workspace_id,user_id,role) VALUES (%s,%s,%s,'owner')", (ta, workspace, owner))
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,%s,'folder','Authorities','model')", (folder, ta, workspace_a))
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class,folder_id) VALUES (%s,%s,%s,'memo','Memo','model',%s)", (record, ta, workspace_a, folder))
            cur.execute(f"CREATE ROLE {runtime_role} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS", (runtime_password,))
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {runtime_role}")
            cur.execute(f"GRANT SELECT, INSERT ON research_workspaces TO {runtime_role}")
            cur.execute(f"GRANT UPDATE (id) ON research_workspaces TO {runtime_role}")
            cur.execute(f"GRANT INSERT ON research_workspace_members TO {runtime_role}")
            cur.execute(f"GRANT SELECT ON research_records TO {runtime_role}")
            # These column grants and the parent row-lock permission are the
            # smallest trigger capabilities needed by the runtime workspace +
            # owner write exercised below. FK enforcement itself does not
            # require client-side REFERENCES privileges.
            cur.execute(f"GRANT SELECT (id, tenant_id) ON users TO {runtime_role}")
            cur.execute(f"GRANT SELECT (workspace_id, role, revoked_at) ON research_workspace_members TO {runtime_role}")
            conn.commit()

            # Composite FKs/checks and retained parents reject privileged/direct SQL bypasses.
            must_fail(cur, "INSERT INTO research_workspaces (tenant_id,matter_id,title) VALUES (%s,%s,'cross tenant')", (ta, matter_b))
            must_fail(cur, "INSERT INTO research_workspace_members (tenant_id,workspace_id,user_id,role) VALUES (%s,%s,%s,'viewer')", (ta, workspace_a, foreign))
            must_fail(cur, "INSERT INTO research_workspace_members (tenant_id,workspace_id,user_id,role) VALUES (%s,%s,%s,'invented')", (ta, workspace_a, other))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class,folder_id) VALUES (%s,%s,'memo','cross-folder','model',%s)", (ta, workspace_b, folder))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class,folder_id) VALUES (%s,%s,'memo','non-folder parent','model',%s)", (ta, workspace_a, record))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,'invented','bad type','model')", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,'authority','unsourced','cited')", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,'memo','bad class','invented')", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class,currentness_state) VALUES (%s,%s,'memo','bad state','model','invented')", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class,treatment_state) VALUES (%s,%s,'memo','bad treatment','model','invented')", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,'exclusion','no reason','model')", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_workspaces (tenant_id,matter_id,title) VALUES (%s,%s,'   ')", (ta, matter_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class,sort_order) VALUES (%s,%s,'memo','bad order','model',-1)", (ta, workspace_a))
            must_fail(cur, "INSERT INTO research_records (tenant_id,workspace_id,record_type,title,evidence_class,revision) VALUES (%s,%s,'memo','bad revision','model',0)", (ta, workspace_a))
            must_fail(cur, "DELETE FROM matters WHERE id=%s", (matter_a,))

            # Every optional user reference rejects foreign-tenant INSERT and UPDATE attempts.
            must_fail(cur, "UPDATE research_workspaces SET created_by_user_id=%s WHERE id=%s", (foreign, workspace_a))
            must_fail(cur, "UPDATE research_records SET created_by_user_id=%s WHERE id=%s", (foreign, record))
            must_fail(cur, "UPDATE research_records SET assigned_reviewer_id=%s WHERE id=%s", (foreign, record))
            must_fail(cur, "INSERT INTO research_workspace_events (tenant_id,workspace_id,record_id,actor_user_id,action,detail) VALUES (%s,%s,%s,%s,'foreign_actor','{}')", (ta, workspace_a, record, foreign))
            must_fail(cur, "INSERT INTO research_workspace_snapshots (tenant_id,workspace_id,sequence,sha256,payload,created_by_user_id) VALUES (%s,%s,97,%s,'{}',%s)", (ta, workspace_a, "a" * 64, foreign))
            must_fail(cur, "INSERT INTO research_record_revisions (tenant_id,workspace_id,record_id,revision,actor_user_id,payload) VALUES (%s,%s,%s,97,%s,'{}')", (ta, workspace_a, record, foreign))

            # Archive-only history/snapshots/revisions and exact retention behavior.
            cur.execute("INSERT INTO research_workspace_snapshots (tenant_id,workspace_id,sequence,sha256,payload,created_by_user_id) VALUES (%s,%s,1,%s,'{}',%s)", (ta, workspace_a, "b" * 64, owner))
            cur.execute("INSERT INTO research_workspace_events (tenant_id,workspace_id,record_id,actor_user_id,action,detail) VALUES (%s,%s,%s,%s,'fixture_history','{\"before\":{},\"after\":{}}')", (ta, workspace_a, record, owner))
            cur.execute("INSERT INTO research_record_revisions (tenant_id,workspace_id,record_id,revision,actor_user_id,payload) VALUES (%s,%s,%s,1,%s,'{\"revision\":1}')", (ta, workspace_a, record, owner))
            conn.commit()
            # A caller-set context never bypasses immutable evidence without
            # the database's exact expired-demo/session facts.
            cur.execute("SELECT set_config('app.research_workspace_demo_purge_tenant_id', %s, true)", (str(ta),))
            cur.execute("SELECT set_config('app.research_workspace_demo_purge_session_id', %s, true)", (str(uuid.uuid4()),))
            must_fail(cur, "DELETE FROM research_workspace_snapshots WHERE workspace_id=%s", (workspace_a,))
            cur.execute("SELECT set_config('app.research_workspace_demo_purge_tenant_id', %s, true)", (str(tb),))
            cur.execute("SELECT set_config('app.research_workspace_demo_purge_session_id', %s, true)", (str(uuid.uuid4()),))
            must_fail(cur, "DELETE FROM research_workspace_events WHERE workspace_id=%s", (workspace_a,))
            must_fail(cur, "UPDATE research_workspace_events SET action='tamper' WHERE workspace_id=%s", (workspace_a,))
            must_fail(cur, "UPDATE research_workspace_snapshots SET created_by_user_id=%s WHERE workspace_id=%s", (foreign, workspace_a))
            must_fail(cur, "UPDATE research_record_revisions SET actor_user_id=%s WHERE workspace_id=%s", (foreign, workspace_a))
            must_fail(cur, "DELETE FROM research_records WHERE id=%s", (record,))
            must_fail(cur, "DELETE FROM research_workspaces WHERE id=%s", (workspace_a,))

    # Runtime requests use a nonsuperuser plus a per-request tenant GUC; no
    # privileged setup connection participates in these RLS assertions.
    runtime_workspace = uuid.uuid4()
    with connect(runtime_role, runtime_password) as runtime:
        with runtime.cursor() as cur:
            cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (str(ta),))
            cur.execute("SELECT count(*) FROM research_workspaces WHERE id=%s", (workspace_a,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM research_records WHERE workspace_id=%s", (workspace_a,))
            assert cur.fetchone()[0] == 2
            cur.execute("INSERT INTO research_workspaces (id,tenant_id,matter_id,title,created_by_user_id) VALUES (%s,%s,%s,'RLS same tenant',%s)", (runtime_workspace, ta, matter_a, owner))
            assert cur.rowcount == 1
            cur.execute("INSERT INTO research_workspace_members (tenant_id,workspace_id,user_id,role) VALUES (%s,%s,%s,'owner')", (ta, runtime_workspace, owner))
            assert cur.rowcount == 1
            runtime.commit()
            cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (str(tb),))
            cur.execute("SELECT count(*) FROM research_workspaces WHERE id=%s", (workspace_a,))
            assert cur.fetchone()[0] == 0
            must_fail_with_code(cur, "INSERT INTO research_workspaces (tenant_id,matter_id,title) VALUES (%s,%s,'RLS cross tenant')", (ta, matter_a), "42501")

    # Atomic record CAS: one expected revision is consumed exactly once.
    cas_rows, cas_lock = [], threading.Lock()
    def compare_and_swap(cur):
        cur.execute("UPDATE research_records SET revision=revision+1 WHERE id=%s AND revision=1", (record,))
        with cas_lock:
            cas_rows.append(cur.rowcount)
        if cur.rowcount:
            cur.execute("INSERT INTO research_workspace_events (tenant_id,workspace_id,record_id,actor_user_id,action,detail) VALUES (%s,%s,%s,%s,'cas_record_updated','{\"before\":{\"revision\":1},\"after\":{\"revision\":2}}')", (ta, workspace_a, record, owner))
            cur.execute("INSERT INTO research_record_revisions (tenant_id,workspace_id,record_id,revision,actor_user_id,payload) VALUES (%s,%s,%s,2,%s,'{\"revision\":2}')", (ta, workspace_a, record, owner))
    cas = concurrently(compare_and_swap, compare_and_swap)
    assert cas.count("committed") == 2
    assert sorted(cas_rows) == [0, 1]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT revision FROM research_records WHERE id=%s", (record,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM research_workspace_events WHERE record_id=%s AND action='cas_record_updated'", (record,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM research_record_revisions WHERE record_id=%s AND revision=2", (record,))
            assert cur.fetchone()[0] == 1

    # Archive has the same expected-revision CAS as PUT, so concurrent archive
    # and update leave either one non-deleted update or one archived revision.
    archive_race_record = uuid.uuid4()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,%s,'memo','Archive race','model')", (archive_race_record, ta, workspace_b))
            conn.commit()
    archive_rows, archive_lock = [], threading.Lock()
    def update_race_record(cur):
        cur.execute("UPDATE research_records SET title='Updated', revision=revision+1 WHERE id=%s AND revision=1 AND deleted_at IS NULL", (archive_race_record,))
        with archive_lock:
            archive_rows.append(cur.rowcount)
        if cur.rowcount:
            cur.execute("INSERT INTO research_workspace_events (tenant_id,workspace_id,record_id,actor_user_id,action,detail) VALUES (%s,%s,%s,%s,'archive_race_updated','{\"before\":{\"revision\":1},\"after\":{\"revision\":2}}')", (ta, workspace_b, archive_race_record, owner))
            cur.execute("INSERT INTO research_record_revisions (tenant_id,workspace_id,record_id,revision,actor_user_id,payload) VALUES (%s,%s,%s,2,%s,'{\"title\":\"Updated\"}')", (ta, workspace_b, archive_race_record, owner))
    def archive_race_record_cas(cur):
        cur.execute("UPDATE research_records SET deleted_at=now(), revision=revision+1 WHERE id=%s AND revision=1 AND deleted_at IS NULL", (archive_race_record,))
        with archive_lock:
            archive_rows.append(cur.rowcount)
        if cur.rowcount:
            cur.execute("INSERT INTO research_workspace_events (tenant_id,workspace_id,record_id,actor_user_id,action,detail) VALUES (%s,%s,%s,%s,'archive_race_archived','{\"before\":{\"revision\":1},\"after\":{\"revision\":2}}')", (ta, workspace_b, archive_race_record, owner))
            cur.execute("INSERT INTO research_record_revisions (tenant_id,workspace_id,record_id,revision,actor_user_id,payload) VALUES (%s,%s,%s,2,%s,'{\"deleted\":true}')", (ta, workspace_b, archive_race_record, owner))
    assert concurrently(update_race_record, archive_race_record_cas).count("committed") == 2
    assert sorted(archive_rows) == [0, 1]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT revision, deleted_at IS NOT NULL FROM research_records WHERE id=%s", (archive_race_record,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM research_workspace_events WHERE record_id=%s AND action LIKE 'archive_race_%%'", (archive_race_record,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM research_record_revisions WHERE record_id=%s AND revision=2", (archive_race_record,))
            assert cur.fetchone()[0] == 1

    # The deferred owner invariant serializes two direct demotions and leaves an owner.
    owner_workspace, second_owner = uuid.uuid4(), uuid.uuid4()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id,tenant_id,email,full_name) VALUES (%s,%s,%s,'Second owner')", (second_owner, ta, f"{second_owner}@{token}.invalid"))
            cur.execute("INSERT INTO research_workspaces (id,tenant_id,matter_id,title,created_by_user_id) VALUES (%s,%s,%s,'Owner race',%s)", (owner_workspace, ta, matter_a, owner))
            for user in (owner, second_owner):
                cur.execute("INSERT INTO research_workspace_members (tenant_id,workspace_id,user_id,role) VALUES (%s,%s,%s,'owner')", (ta, owner_workspace, user))
            conn.commit()
    demotions = concurrently(
        lambda cur: cur.execute("UPDATE research_workspace_members SET role='editor' WHERE workspace_id=%s AND user_id=%s", (owner_workspace, owner)),
        lambda cur: cur.execute("UPDATE research_workspace_members SET role='editor' WHERE workspace_id=%s AND user_id=%s", (owner_workspace, second_owner)),
    )
    assert demotions.count("committed") == 1 and len(demotions) == 2
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM research_workspace_members WHERE workspace_id=%s AND role='owner' AND revoked_at IS NULL", (owner_workspace,))
            assert cur.fetchone()[0] == 1

    # Both deterministic orders preserve the folder invariant: an existing
    # active child blocks archival, and an archived parent rejects a child.
    child_first_folder, child_first_record = uuid.uuid4(), uuid.uuid4()
    archive_first_folder, archive_first_record = uuid.uuid4(), uuid.uuid4()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,%s,'folder','Child first folder','model')", (child_first_folder, ta, workspace_b))
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class,folder_id) VALUES (%s,%s,%s,'memo','Child first record','model',%s)", (child_first_record, ta, workspace_b, child_first_folder))
            conn.commit()
            must_fail(cur, "UPDATE research_records SET deleted_at=now() WHERE id=%s", (child_first_folder,))
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,%s,'folder','Archive first folder','model')", (archive_first_folder, ta, workspace_b))
            cur.execute("UPDATE research_records SET deleted_at=now() WHERE id=%s", (archive_first_folder,))
            conn.commit()
            must_fail(cur, "INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class,folder_id) VALUES (%s,%s,%s,'memo','Archive first record','model',%s)", (archive_first_record, ta, workspace_b, archive_first_folder))

    # Folder lock makes archive versus child insertion serializable; no active orphan remains.
    race_folder, race_child = uuid.uuid4(), uuid.uuid4()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class) VALUES (%s,%s,%s,'folder','Race folder','model')", (race_folder, ta, workspace_b))
            conn.commit()
    folder_race = concurrently(
        lambda cur: cur.execute("UPDATE research_records SET deleted_at=now() WHERE id=%s", (race_folder,)),
        lambda cur: cur.execute("INSERT INTO research_records (id,tenant_id,workspace_id,record_type,title,evidence_class,folder_id) VALUES (%s,%s,%s,'memo','Race child','model',%s)", (race_child, ta, workspace_b, race_folder)),
    )
    assert folder_race.count("committed") == 1 and len(folder_race) == 2
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT parent.deleted_at IS NOT NULL, child.id IS NOT NULL AND child.deleted_at IS NULL FROM research_records parent LEFT JOIN research_records child ON child.id=%s WHERE parent.id=%s", (race_child, race_folder))
            archived, child_active = cur.fetchone()
            assert not (archived and child_active)

    # Advisory-key replay has one durable row; workspace lock allocates sequence 1 then 2.
    idem_key = f"replay-{token}"
    def reserve_idempotency(cur):
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (idem_key,))
        cur.execute("INSERT INTO research_workspace_idempotency (tenant_id,actor_user_id,operation,idempotency_key,request_sha256) VALUES (%s,%s,'snapshot_create',%s,%s)", (ta, owner, idem_key, "c" * 64))
    idem = concurrently(reserve_idempotency, reserve_idempotency)
    assert idem.count("committed") == 1 and len(idem) == 2

    def allocate_snapshot(cur):
        cur.execute("SELECT id FROM research_workspaces WHERE id=%s FOR UPDATE", (workspace_b,))
        cur.execute("SELECT coalesce(max(sequence), 0) + 1 FROM research_workspace_snapshots WHERE workspace_id=%s", (workspace_b,))
        sequence = cur.fetchone()[0]
        cur.execute("INSERT INTO research_workspace_snapshots (tenant_id,workspace_id,sequence,sha256,payload,created_by_user_id) VALUES (%s,%s,%s,%s,'{}',%s)", (ta, workspace_b, sequence, uuid.uuid4().hex * 2, owner))

    assert sorted(concurrently(allocate_snapshot, allocate_snapshot)) == ["committed", "committed"]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT array_agg(sequence ORDER BY sequence) FROM research_workspace_snapshots WHERE workspace_id=%s", (workspace_b,))
            assert cur.fetchone()[0] == [1, 2]
    print("Research workspace PostgreSQL rehearsal passed")


if __name__ == "__main__":
    main()
