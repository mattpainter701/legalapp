"""Prove all Studio tables FORCE RLS with a non-owner, non-BYPASSRLS role."""

import os
import uuid

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
            "(id, tenant_id, sha256, media_type, byte_size, resolver_key, content_bytes, created_by_user_id) "
            "VALUES (:id, :tenant, :hash, 'text/markdown', 1, :resolver, :content, :user)"
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
            "(id, tenant_id, draft_id, revision, identity_sha256, content_sha256, payload, created_by_user_id) "
            "VALUES (:id, :tenant, :draft, 1, :hash, :hash, '{}'::json, :user)"
        ),
        {
            "id": snapshot_id,
            "tenant": tenant_id,
            "draft": draft_id,
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
        await conn.execute(text(f"GRANT SELECT, UPDATE ON {table} TO {ROLE}"))

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
    finally:
        await role_engine.dispose()
        async with test_engine.begin() as conn:
            for name in TABLES:
                await conn.execute(
                    text(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY")
                )
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_retention_purge_tenant_id', :tenant, true)"
                ),
                {"tenant": str(tenant_a)},
            )
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_retention_purge_reason', 'retention', true)"
                )
            )
            for name in reversed(TABLES):
                await conn.execute(
                    text(f"DELETE FROM {name} WHERE tenant_id = :tenant"),
                    {"tenant": tenant_a},
                )
            await conn.execute(
                text(
                    "SELECT set_config('app.studio_retention_purge_tenant_id', :tenant, true)"
                ),
                {"tenant": str(tenant_b)},
            )
            for name in reversed(TABLES):
                await conn.execute(
                    text(f"DELETE FROM {name} WHERE tenant_id = :tenant"),
                    {"tenant": tenant_b},
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
    async with test_engine.begin() as conn:
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY"))
        ids = await _seed_tenant(conn, tenant_id, "c")
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

    async with test_engine.begin() as conn:
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY"))
        await conn.execute(
            text(
                "SELECT set_config('app.studio_retention_purge_tenant_id', :tenant, true)"
            ),
            {"tenant": str(tenant_id)},
        )
        await conn.execute(
            text(
                "SELECT set_config('app.studio_retention_purge_reason', 'retention', true)"
            )
        )
        for name in reversed(TABLES):
            await conn.execute(
                text(f"DELETE FROM {name} WHERE tenant_id = :tenant"),
                {"tenant": tenant_id},
            )
        await conn.execute(
            text("DELETE FROM tenants WHERE id = :tenant"), {"tenant": tenant_id}
        )
        for name in TABLES:
            await conn.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
