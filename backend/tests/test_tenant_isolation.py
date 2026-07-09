"""Row Level Security tenant-isolation seed test.

This is the SEED of the full cross-tenant isolation matrix (epic task 1304).
It proves the core RLS mechanism works: the tenant-context GUCs drive
per-tenant visibility, cleared context is fail-closed (zero rows), and the auth
``app.rls_bypass`` GUC re-opens visibility for the cross-tenant auth path.

Critical environment fact this test encodes:
  Postgres SUPERUSERS (and the table OWNER, and roles with BYPASSRLS) bypass RLS
  *even when a table is FORCE ROW LEVEL SECURITY*. The CI/test database connects
  as a superuser, so asserting RLS through that connection would always see every
  row. To observe RLS we therefore run the assertions through a dedicated
  ``NOSUPERUSER NOBYPASSRLS`` login role that does NOT own the probe table —
  exactly the posture production relies on (see scripts/provision_app_role.sql).

conftest's test schema is built via ``Base.metadata.create_all``, which does NOT
create RLS policies (those live only in Alembic migrations). So this test is
self-contained: it creates its own throwaway table + role, ENABLEs/FORCEs RLS,
and installs policies mirroring production. It skips cleanly (never errors at
collection) when Postgres is unavailable.
"""

import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import String, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app import database
from app.database import (
    clear_tenant_context,
    enable_rls_bypass,
    NO_TENANT_CONTEXT,
    set_tenant_context,
)

pytestmark = pytest.mark.asyncio

# Superuser URL (same convention as conftest.py) — used for DDL/seeding.
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/legalapp_test",
)
# Non-superuser role the assertions run as, so RLS is actually enforced.
_RLS_ROLE = "rls_probe_role"
_RLS_PW = "rls_probe_pw"
RLS_ROLE_URL = (
    make_url(TEST_DB_URL)
    .set(username=_RLS_ROLE, password=_RLS_PW)
    .render_as_string(hide_password=False)
)

# A dedicated throwaway table so we never depend on the real schema / FKs.
_TABLE = "rls_seed_probe"


class ProbeBase(DeclarativeBase):
    pass


class RLSProbe(ProbeBase):
    __tablename__ = _TABLE

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)


TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


@pytest_asyncio.fixture
async def rls_session():
    """Yield a session, connected as a non-superuser role, against a table with
    production-style RLS — or skip cleanly if Postgres is unavailable."""
    admin_engine = create_async_engine(TEST_DB_URL, echo=False)

    # Setup as superuser: probe table, RLS policies, the non-superuser role, and
    # seed data. Skip the whole module if Postgres is not reachable.
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLE}"))
            await conn.execute(
                text(
                    f"""
                    CREATE TABLE {_TABLE} (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        tenant_id UUID NOT NULL,
                        label TEXT NOT NULL
                    )
                    """
                )
            )
            # Mirror production RLS: ENABLE + FORCE + a policy keyed on the
            # app.current_tenant_id GUC (missing_ok => fail-closed) and the
            # rls_bypass escape hatch from migration 044.
            await conn.execute(text(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY"))
            await conn.execute(
                text(
                    f"""
                    CREATE POLICY tenant_isolation_{_TABLE} ON {_TABLE}
                    FOR ALL TO PUBLIC
                    USING (
                        tenant_id::text
                        = current_setting('app.current_tenant_id', true)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    f"""
                    CREATE POLICY rls_bypass_{_TABLE} ON {_TABLE}
                    FOR ALL TO PUBLIC
                    USING (current_setting('app.rls_bypass', true) = 'on')
                    """
                )
            )
            # The non-superuser, NOBYPASSRLS login role the test asserts through.
            await conn.execute(
                text(
                    f"""
                    DO $$ BEGIN
                      IF NOT EXISTS (
                        SELECT FROM pg_roles WHERE rolname = '{_RLS_ROLE}'
                      ) THEN
                        CREATE ROLE {_RLS_ROLE} LOGIN PASSWORD '{_RLS_PW}'
                          NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
                      END IF;
                    END $$;
                    """
                )
            )
            await conn.execute(
                text(
                    f"""
                    ALTER ROLE {_RLS_ROLE} LOGIN PASSWORD '{_RLS_PW}'
                      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                    """
                )
            )
            await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_RLS_ROLE}"))
            await conn.execute(text(f"GRANT SELECT, INSERT ON {_TABLE} TO {_RLS_ROLE}"))
            # Seed rows as superuser (RLS does not block the superuser).
            for tenant, label in (
                (TENANT_A, "a1"),
                (TENANT_A, "a2"),
                (TENANT_B, "b1"),
            ):
                await conn.execute(
                    text(f"INSERT INTO {_TABLE} (tenant_id, label) VALUES (:t, :l)"),
                    {"t": tenant, "l": label},
                )
    except (OperationalError, InterfaceError, ConnectionError, OSError) as exc:
        await admin_engine.dispose()
        pytest.skip(f"Postgres not reachable for RLS test: {exc}")

    # Assertion session connects AS the non-superuser role so RLS is enforced.
    rls_engine = create_async_engine(RLS_ROLE_URL, echo=False)
    try:
        factory = async_sessionmaker(rls_engine, expire_on_commit=False)
        async with factory() as session:
            # Sanity: confirm we are NOT a superuser, else the test is meaningless.
            is_super = (
                await session.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
            assert is_super is False, "RLS assertions must run as a non-superuser"
            yield session
    finally:
        await rls_engine.dispose()
        # Teardown as superuser: drop table + role.
        async with admin_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLE}"))
            await conn.execute(text(f"DROP OWNED BY {_RLS_ROLE}"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))
        await admin_engine.dispose()


async def _count(session, label_prefix=None):
    if label_prefix is None:
        result = await session.execute(text(f"SELECT count(*) FROM {_TABLE}"))
    else:
        result = await session.execute(
            text(f"SELECT count(*) FROM {_TABLE} WHERE label LIKE :p"),
            {"p": f"{label_prefix}%"},
        )
    return result.scalar_one()


async def test_tenant_context_isolates_rows(rls_session):
    """Tenant A's context sees only tenant A rows, never tenant B's."""
    await set_tenant_context(rls_session, TENANT_A)

    assert await _count(rls_session) == 2  # a1 + a2 only
    assert await _count(rls_session, "a") == 2
    assert await _count(rls_session, "b") == 0  # tenant B invisible


async def test_tenant_context_sets_current_and_legacy_gucs(rls_session):
    """Both policy generations must see the same tenant id."""
    await set_tenant_context(rls_session, TENANT_A)

    current, legacy = (
        await rls_session.execute(
            text(
                """
                SELECT
                  current_setting('app.current_tenant_id', true),
                  current_setting('app.tenant_id', true)
                """
            )
        )
    ).one()

    assert current == TENANT_A
    assert legacy == TENANT_A


async def test_no_context_is_fail_closed(rls_session):
    """With no tenant context set, the sentinel UUID sees zero rows."""
    await clear_tenant_context(rls_session)
    current, legacy = (
        await rls_session.execute(
            text(
                """
                SELECT
                  current_setting('app.current_tenant_id', true),
                  current_setting('app.tenant_id', true)
                """
            )
        )
    ).one()
    assert current == NO_TENANT_CONTEXT
    assert legacy == NO_TENANT_CONTEXT
    assert await _count(rls_session) == 0


async def test_rls_bypass_reveals_all_rows(rls_session):
    """A scoped context sees only its rows; enable_rls_bypass re-opens all."""
    # Scoped context: tenant B sees only its single row.
    await set_tenant_context(rls_session, TENANT_B)
    assert await _count(rls_session) == 1  # b1 only

    # Bypass on: all three rows (both tenants) become visible.
    await enable_rls_bypass(rls_session)
    assert await _count(rls_session) == 3


async def test_get_db_rebinds_tenant_context_after_commit_for_refresh(
    monkeypatch, rls_session
):
    class ExistingSessionContext:
        async def __aenter__(self):
            return rls_session

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        database, "async_session_maker", lambda: ExistingSessionContext()
    )
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=TENANT_A))
    db_stream = database.get_db(request)

    try:
        db = await db_stream.__anext__()
        await db.commit()

        current = (
            await db.execute(text("SELECT current_setting('app.current_tenant_id')"))
        ).scalar_one()
        assert current == TENANT_A

        row = (
            await db.execute(select(RLSProbe).where(RLSProbe.label == "a1"))
        ).scalar_one()
        await db.commit()

        await db.refresh(row)
        assert row.label == "a1"
    finally:
        await db_stream.aclose()
