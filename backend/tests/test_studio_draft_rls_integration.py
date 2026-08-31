"""Prove Studio FORCE RLS using a non-owner, non-BYPASSRLS login role."""

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


async def test_studio_drafts_force_rls_isolates_reads_and_writes(test_engine):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    draft_a = uuid.uuid4()
    draft_b = uuid.uuid4()
    cross_artifact = uuid.uuid4()

    async with test_engine.begin() as conn:
        await conn.execute(text("DROP POLICY IF EXISTS studio_test_tenant_policy ON studio_drafts"))
        await conn.execute(text("ALTER TABLE studio_drafts DISABLE ROW LEVEL SECURITY"))
        await conn.execute(text("INSERT INTO tenants (id, name, domain, billing_tier, is_active) VALUES (:id, 'Studio A', :domain, 'payg', true)"), {"id": tenant_a, "domain": f"studio-a-{tenant_a}.invalid"})
        await conn.execute(text("INSERT INTO tenants (id, name, domain, billing_tier, is_active) VALUES (:id, 'Studio B', :domain, 'payg', true)"), {"id": tenant_b, "domain": f"studio-b-{tenant_b}.invalid"})
        for draft_id, tenant_id, marker in (
            (draft_a, tenant_a, "a"), (draft_b, tenant_b, "b")
        ):
            artifact_id = uuid.uuid4()
            await conn.execute(text("""
                INSERT INTO studio_source_artifacts (id, tenant_id, sha256, media_type)
                VALUES (:id, :tenant, :hash, 'text/markdown')
            """), {"id": artifact_id, "tenant": tenant_id, "hash": marker * 64})
            await conn.execute(text("""
                INSERT INTO studio_drafts (
                    id, tenant_id, source_artifact_id, source_sha256,
                    source_media_type, title, format, identity_sha256
                ) VALUES (:id, :tenant, :artifact, :hash,
                          'text/markdown', :title, 'markdown', :hash)
            """), {"id": draft_id, "tenant": tenant_id, "artifact": artifact_id, "hash": marker * 64, "title": f"Draft {marker}"})
        await conn.execute(text("""
            INSERT INTO studio_source_artifacts (id, tenant_id, sha256, media_type)
            VALUES (:id, :tenant, :hash, 'text/markdown')
        """), {"id": cross_artifact, "tenant": tenant_b, "hash": "c" * 64})
        await conn.execute(text("ALTER TABLE studio_drafts ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE studio_drafts FORCE ROW LEVEL SECURITY"))
        await conn.execute(text("""
            CREATE POLICY studio_test_tenant_policy ON studio_drafts
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """))
        await conn.execute(text(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                    CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'
                        NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
                END IF;
            END $$
        """))
        await conn.execute(text(
            f"ALTER ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
        ))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))
        await conn.execute(text(f"GRANT SELECT, INSERT ON studio_drafts TO {ROLE}"))

    role_engine = create_async_engine(ROLE_URL, echo=False)
    try:
        factory = async_sessionmaker(role_engine, expire_on_commit=False)
        async with factory() as session:
            role_flags = (await session.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            ))).one()
            assert tuple(role_flags) == (False, False)

            await set_tenant_context(session, str(tenant_a))
            visible = (await session.execute(text(
                "SELECT id FROM studio_drafts ORDER BY id"
            ))).scalars().all()
            assert visible == [draft_a]

            with pytest.raises(DBAPIError):
                await session.execute(text("""
                    INSERT INTO studio_drafts (
                        id, tenant_id, source_artifact_id, source_sha256,
                        source_media_type, title, format, identity_sha256
                    ) VALUES (gen_random_uuid(), :tenant, :artifact, :hash,
                              'text/markdown', 'Cross tenant', 'markdown', :hash)
                """), {"tenant": tenant_b, "artifact": cross_artifact, "hash": "c" * 64})
                await session.flush()
            await session.rollback()
    finally:
        await role_engine.dispose()
        async with test_engine.begin() as conn:
            await conn.execute(text("DROP POLICY IF EXISTS studio_test_tenant_policy ON studio_drafts"))
            await conn.execute(text("ALTER TABLE studio_drafts DISABLE ROW LEVEL SECURITY"))
            await conn.execute(text("DELETE FROM studio_drafts WHERE tenant_id IN (:a, :b)"), {"a": tenant_a, "b": tenant_b})
            await conn.execute(text("DELETE FROM studio_source_artifacts WHERE tenant_id IN (:a, :b)"), {"a": tenant_a, "b": tenant_b})
            await conn.execute(text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": tenant_a, "b": tenant_b})
            await conn.execute(text(f"DROP OWNED BY {ROLE}"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
