"""Prove all Studio tables FORCE RLS with a non-owner, non-BYPASSRLS role."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import set_tenant_context

pytestmark = pytest.mark.asyncio

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/legalapp_test",
)
ROLE = "studio_rls_probe_role"
PASSWORD = "studio_rls_probe_pw"
ROLE_URL = (
    make_url(TEST_DB_URL)
    .set(username=ROLE, password=PASSWORD)
    .render_as_string(hide_password=False)
)
TABLES = (
    "studio_source_artifacts",
    "studio_drafts",
    "studio_draft_fields",
    "studio_draft_placements",
    "studio_draft_snapshots",
    "studio_draft_idempotency",
    "studio_draft_audit_events",
)


async def _seed_tenant(conn, tenant_id, marker):
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    field_id = uuid.uuid4()
    placement_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    idempotency_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    digest = marker * 64
    await conn.execute(
        text(
            "INSERT INTO tenants (id, name, domain, billing_tier, is_active) "
            "VALUES (:id, :name, :domain, 'payg', true)"
        ),
        {
            "id": tenant_id,
            "name": f"Studio {marker}",
            "domain": f"studio-{tenant_id}.invalid",
        },
    )
    await conn.execute(
        text("INSERT INTO users (id, tenant_id, email) VALUES (:id, :tenant, :email)"),
        {
            "id": user_id,
            "tenant": tenant_id,
            "email": f"studio-{marker}@example.invalid",
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_source_artifacts "
            "(id, tenant_id, sha256, media_type, format, byte_size, resolver_key, content_bytes, created_by_user_id) "
            "VALUES (:id, :tenant, :hash, 'text/markdown', 'markdown', 1, :resolver, :content, :user)"
        ),
        {
            "id": source_id,
            "tenant": tenant_id,
            "hash": digest,
            "resolver": f"studio-db:v1:{uuid.uuid4()}",
            "content": marker.encode(),
            "user": user_id,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_drafts "
            "(id, tenant_id, source_artifact_id, source_sha256, source_media_type, title, format, identity_sha256, created_by_user_id, updated_by_user_id) "
            "VALUES (:id, :tenant, :source, :hash, 'text/markdown', :title, 'markdown', :hash, :user, :user)"
        ),
        {
            "id": draft_id,
            "tenant": tenant_id,
            "source": source_id,
            "hash": digest,
            "title": marker,
            "user": user_id,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_draft_fields "
            "(id, tenant_id, draft_id, automation_key, label, field_type) "
            "VALUES (:id, :tenant, :draft, :key, :key, 'text')"
        ),
        {
            "id": field_id,
            "tenant": tenant_id,
            "draft": draft_id,
            "key": f"field_{marker}",
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_draft_placements "
            "(id, tenant_id, draft_id, field_id, format, anchor_kind, anchor) "
            "VALUES (:id, :tenant, :draft, :field, 'markdown', 'template_token', CAST(:anchor AS json))"
        ),
        {
            "id": placement_id,
            "tenant": tenant_id,
            "draft": draft_id,
            "field": field_id,
            "anchor": f'{{"token":"field_{marker}"}}',
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_draft_snapshots "
            "(id, tenant_id, draft_id, source_artifact_id, revision, identity_sha256, content_sha256, payload, created_by_user_id) "
            "VALUES (:id, :tenant, :draft, :source, 1, :hash, :hash, '{}'::json, :user)"
        ),
        {
            "id": snapshot_id,
            "tenant": tenant_id,
            "draft": draft_id,
            "source": source_id,
            "hash": digest,
            "user": user_id,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_draft_idempotency "
            "(id, tenant_id, actor_user_id, operation, idempotency_key, request_sha256, expires_at) "
            "VALUES (:id, :tenant, :user, 'create', :key, :hash, now() + interval '1 hour')"
        ),
        {
            "id": idempotency_id,
            "tenant": tenant_id,
            "user": user_id,
            "key": f"key-{marker}",
            "hash": digest,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO studio_draft_audit_events "
            "(id, tenant_id, draft_id, event_type, revision, actor_user_id) "
            "VALUES (:id, :tenant, :draft, 'created', 1, :user)"
        ),
        {"id": audit_id, "tenant": tenant_id, "draft": draft_id, "user": user_id},
    )
    return {
        "_user_id": user_id,
        "studio_source_artifacts": source_id,
        "studio_drafts": draft_id,
        "studio_draft_fields": field_id,
        "studio_draft_placements": placement_id,
        "studio_draft_snapshots": snapshot_id,
        "studio_draft_idempotency": idempotency_id,
        "studio_draft_audit_events": audit_id,
    }


@pytest.mark.parametrize("table", TABLES)
async def test_studio_force_rls_isolates_reads_and_writes(test_engine, table):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ids_a = ids_b = None
    async with test_engine.begin() as conn:
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY"))
        ids_a = await _seed_tenant(conn, tenant_a, "a")
        ids_b = await _seed_tenant(conn, tenant_b, "b")
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
            await conn.execute(
                text(f"DROP POLICY IF EXISTS studio_test_{name}_policy ON {name}")
            )
            await conn.execute(
                text(
                    f"CREATE POLICY studio_test_{name}_policy ON {name} "
                    "USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) "
                    "WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"
                )
            )
        await conn.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') "
                f"THEN CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF; END $$"
            )
        )
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {ROLE}"))

    role_engine = create_async_engine(ROLE_URL, echo=False)
    try:
        factory = async_sessionmaker(role_engine, expire_on_commit=False)
        async with factory() as session:
            flags = (
                await session.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            assert tuple(flags) == (False, False)
            await set_tenant_context(session, str(tenant_a))
            visible = (
                (await session.execute(text(f"SELECT id FROM {table}"))).scalars().all()
            )
            assert visible == [ids_a[table]]
            leaked = (
                await session.execute(
                    text(f"SELECT id FROM {table} WHERE id = :id"),
                    {"id": ids_b[table]},
                )
            ).scalar_one_or_none()
            nonexistent = (
                await session.execute(
                    text(f"SELECT id FROM {table} WHERE id = :id"),
                    {"id": uuid.uuid4()},
                )
            ).scalar_one_or_none()
            assert leaked is None and nonexistent is None
            result = await session.execute(
                text(f"UPDATE {table} SET tenant_id = tenant_id WHERE id = :id"),
                {"id": ids_b[table]},
            )
            assert result.rowcount == 0
            await session.rollback()
            await set_tenant_context(session, str(tenant_a))
            insert_sql = {
                "studio_source_artifacts": """
                    INSERT INTO studio_source_artifacts
                    (id, tenant_id, sha256, media_type, format, byte_size, resolver_key, content_bytes)
                    VALUES (gen_random_uuid(), :tenant, :hash, 'text/markdown', 'markdown', 1, :resolver, 'z')
                """,
                "studio_drafts": """
                    INSERT INTO studio_drafts
                    (id, tenant_id, source_artifact_id, source_sha256, source_media_type, title, format, identity_sha256)
                    VALUES (gen_random_uuid(), :tenant, :source, :source_hash, 'text/markdown', 'Cross', 'markdown', :hash)
                """,
                "studio_draft_fields": """
                    INSERT INTO studio_draft_fields
                    (id, tenant_id, draft_id, automation_key, label, field_type)
                    VALUES (gen_random_uuid(), :tenant, :draft, 'cross_field', 'Cross', 'text')
                """,
                "studio_draft_placements": """
                    INSERT INTO studio_draft_placements
                    (id, tenant_id, draft_id, field_id, format, anchor_kind, anchor)
                    VALUES (gen_random_uuid(), :tenant, :draft, :field, 'markdown', 'template_token', '{"token":"cross_field"}'::json)
                """,
                "studio_draft_snapshots": """
                    INSERT INTO studio_draft_snapshots
                    (id, tenant_id, draft_id, source_artifact_id, revision, identity_sha256, content_sha256, payload)
                    VALUES (gen_random_uuid(), :tenant, :draft, :source, 2, :hash, :hash, '{}'::json)
                """,
                "studio_draft_idempotency": """
                    INSERT INTO studio_draft_idempotency
                    (id, tenant_id, actor_user_id, operation, idempotency_key, request_sha256, expires_at)
                    VALUES (gen_random_uuid(), :tenant, :user, 'patch', 'cross-key', :hash, now() + interval '1 hour')
                """,
                "studio_draft_audit_events": """
                    INSERT INTO studio_draft_audit_events
                    (id, tenant_id, draft_id, event_type, revision)
                    VALUES (gen_random_uuid(), :tenant, :draft, 'cross', 2)
                """,
            }[table]
            with pytest.raises(DBAPIError):
                await session.execute(
                    text(insert_sql),
                    {
                        "tenant": tenant_b,
                        "hash": "d" * 64,
                        "source_hash": "b" * 64,
                        "resolver": f"studio-db:v1:{uuid.uuid4()}",
                        "source": ids_b["studio_source_artifacts"],
                        "draft": ids_b["studio_drafts"],
                        "field": ids_b["studio_draft_fields"],
                        "user": ids_b["_user_id"],
                    },
                )
                await session.flush()
            await session.rollback()
    finally:
        await role_engine.dispose()
        async with test_engine.begin() as conn:
            for name in TABLES:
                await conn.execute(
                    text(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY")
                )
            await conn.execute(
                text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
            )
            await conn.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            await conn.execute(text(f"DROP OWNED BY {ROLE}"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
            for name in TABLES:
                await conn.execute(
                    text(f"DROP POLICY IF EXISTS studio_test_{name}_policy ON {name}")
                )
                await conn.execute(
                    text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
                )
                await conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))


async def test_studio_append_only_rows_require_transaction_scoped_purge(test_engine):
    tenant_id = uuid.uuid4()
    fixture_id = uuid.uuid4()
    demo_session_id = uuid.uuid4()
    async with test_engine.begin() as conn:
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY"))
        ids = await _seed_tenant(conn, tenant_id, "c")
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, domain, billing_tier, is_active) "
                "VALUES (:id, 'Fixture', :domain, 'fixture', true)"
            ),
            {"id": fixture_id, "domain": f"fixture-{fixture_id}.invalid"},
        )
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))

    for table in (
        "studio_source_artifacts",
        "studio_draft_snapshots",
        "studio_draft_audit_events",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(f"UPDATE {table} SET tenant_id = tenant_id WHERE id = :id"),
                    {"id": ids[table]},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(f"DELETE FROM {table} WHERE id = :id"),
                    {"id": ids[table]},
                )

    # Predictable settings alone do not authorize a normal tenant.
    with pytest.raises(DBAPIError, match="append-only"):
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_demo_purge_tenant_id', :tenant, true)"
                ),
                {"tenant": str(tenant_id)},
            )
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_demo_purge_session_id', :session, true)"
                ),
                {"session": str(demo_session_id)},
            )
            await conn.execute(
                text("DELETE FROM studio_draft_audit_events WHERE id = :id"),
                {"id": ids["studio_draft_audit_events"]},
            )

    now = datetime.now(timezone.utc)
    async with test_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET billing_tier='demo', domain=:domain, is_active=true, expires_at=:future WHERE id=:id"
            ),
            {
                "domain": f"candidate-{tenant_id}.demo.invalid",
                "future": now + timedelta(hours=1),
                "id": tenant_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO demo_sessions (id, tenant_id, fixture_tenant_id, fixture_version, prospect_name, prospect_email, status, quota, expires_at) "
                "VALUES (:id, :tenant, :fixture, 'test', 'Prospect', 'prospect@example.invalid', 'active', 20, :future)"
            ),
            {
                "id": demo_session_id,
                "tenant": tenant_id,
                "fixture": fixture_id,
                "future": now + timedelta(hours=1),
            },
        )

    # Active/nonexpired demo, arbitrary retention convention, and wrong session fail.
    for tenant_setting, session_setting in (
        (tenant_id, demo_session_id),
        (tenant_id, uuid.uuid4()),
        (uuid.uuid4(), demo_session_id),
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(
                        "SELECT set_config('app.studio_demo_purge_tenant_id', :tenant, true)"
                    ),
                    {"tenant": str(tenant_setting)},
                )
                await conn.execute(
                    text(
                        "SELECT set_config('app.studio_demo_purge_session_id', :session, true)"
                    ),
                    {"session": str(session_setting)},
                )
                await conn.execute(
                    text(
                        "SELECT set_config('app.studio_retention_purge_reason', 'retention', true)"
                    )
                )
                await conn.execute(
                    text("DELETE FROM studio_draft_audit_events WHERE id = :id"),
                    {"id": ids["studio_draft_audit_events"]},
                )

    async with test_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET is_active=false, expires_at=:past WHERE id=:id"),
            {"past": now - timedelta(minutes=1), "id": tenant_id},
        )
    # Expired/inactive is still insufficient until the demo claim is purging.
    with pytest.raises(DBAPIError, match="append-only"):
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_demo_purge_tenant_id', :tenant, true)"
                ),
                {"tenant": str(tenant_id)},
            )
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_demo_purge_session_id', :session, true)"
                ),
                {"session": str(demo_session_id)},
            )
            await conn.execute(
                text("DELETE FROM studio_draft_audit_events WHERE id = :id"),
                {"id": ids["studio_draft_audit_events"]},
            )

    async with test_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE demo_sessions SET status='purging', purge_started_at=:now WHERE id=:id"
            ),
            {"now": now, "id": demo_session_id},
        )

    # The removed generic retention convention cannot authorize even an otherwise
    # purge-eligible demo without the exact tenant/session claims.
    with pytest.raises(DBAPIError, match="append-only"):
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_retention_purge_reason', 'retention', true)"
                )
            )
            await conn.execute(
                text("DELETE FROM studio_draft_audit_events WHERE id = :id"),
                {"id": ids["studio_draft_audit_events"]},
            )

    for tenant_setting, session_setting in (
        (tenant_id, uuid.uuid4()),
        (uuid.uuid4(), demo_session_id),
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(
                        "SELECT set_config('app.studio_demo_purge_tenant_id', :tenant, true)"
                    ),
                    {"tenant": str(tenant_setting)},
                )
                await conn.execute(
                    text(
                        "SELECT set_config('app.studio_demo_purge_session_id', :session, true)"
                    ),
                    {"session": str(session_setting)},
                )
                await conn.execute(
                    text("DELETE FROM studio_draft_audit_events WHERE id = :id"),
                    {"id": ids["studio_draft_audit_events"]},
                )

    async with test_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.studio_demo_purge_tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        await conn.execute(
            text(
                "SELECT set_config('app.studio_demo_purge_session_id', :session, true)"
            ),
            {"session": str(demo_session_id)},
        )
        for name in reversed(TABLES):
            await conn.execute(
                text(f"DELETE FROM {name} WHERE tenant_id = :tenant"),
                {"tenant": tenant_id},
            )
        await conn.execute(
            text("DELETE FROM demo_sessions WHERE id = :session"),
            {"session": demo_session_id},
        )
        await conn.execute(
            text("DELETE FROM tenants WHERE id IN (:tenant, :fixture)"),
            {"tenant": tenant_id, "fixture": fixture_id},
        )
