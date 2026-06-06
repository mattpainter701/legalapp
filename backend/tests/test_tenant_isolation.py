"""Row Level Security tenant-isolation seed test.

This is the SEED of the full cross-tenant isolation matrix (epic task 1304).
It proves the core RLS mechanism works: the `app.current_tenant_id` GUC drives
per-tenant visibility, an unset context is fail-closed (zero rows), and the
auth `app.rls_bypass` GUC re-opens visibility for the cross-tenant auth path.

The exhaustive matrix -- asserting isolation over EVERY tenant-scoped endpoint
and table -- is future work tracked under task 1304.

Notes on the environment:
* conftest's test schema is built via ``Base.metadata.create_all``, which does
  NOT create RLS policies (those live only in Alembic migrations). So this test
  is self-contained: it creates its own throwaway table, ENABLEs + FORCEs RLS,
  and installs a policy mirroring the production
  ``USING (tenant_id::text = current_setting('app.current_tenant_id', true))``
  pattern.
* Postgres is not guaranteed to be reachable in the dev container. The module
  skips cleanly (never errors at collection) when no DB is available.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import (
    clear_tenant_context,
    enable_rls_bypass,
    set_tenant_context,
)

pytestmark = pytest.mark.asyncio

# Reuse the same test DB URL convention as conftest.py.
TEST_DB_URL = "postgresql+asyncpg://test:test@localhost:5432/legalapp_test"

# A dedicated throwaway table so we never depend on the real schema / FKs and
# can install/own a policy without touching application tables.
_TABLE = "rls_seed_probe"

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


@pytest_asyncio.fixture
async def rls_session():
    """Yield a session against a table with prod-style RLS, or skip if no DB.

    We connect as a non-owner-irrelevant role here only to assert policy
    behaviour; FORCE RLS guarantees the policy applies regardless of ownership,
    which is precisely what production relies on for the connecting app role.
    """
    engine = create_async_engine(TEST_DB_URL, echo=False)

    # Try to connect; skip the whole module gracefully if Postgres is absent.
    try:
        async with engine.begin() as conn:
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
            # Mirror the production RLS setup exactly: ENABLE + FORCE + a policy
            # keyed on the app.current_tenant_id GUC (missing_ok => fail-closed).
            await conn.execute(
                text(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
            )
            await conn.execute(
                text(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
            )
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
            # Mirror the rls_bypass_users escape hatch from migration 044.
            await conn.execute(
                text(
                    f"""
                    CREATE POLICY rls_bypass_{_TABLE} ON {_TABLE}
                    FOR ALL TO PUBLIC
                    USING (current_setting('app.rls_bypass', true) = 'on')
                    """
                )
            )
    except (OperationalError, InterfaceError, ConnectionError, OSError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for RLS test: {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed rows for two tenants. Seeding happens with the bypass on so RLS does
    # not block our own inserts during setup.
    async with factory() as session:
        await enable_rls_bypass(session)
        await session.execute(
            text(f"INSERT INTO {_TABLE} (tenant_id, label) VALUES (:t, 'a1')"),
            {"t": TENANT_A},
        )
        await session.execute(
            text(f"INSERT INTO {_TABLE} (tenant_id, label) VALUES (:t, 'a2')"),
            {"t": TENANT_A},
        )
        await session.execute(
            text(f"INSERT INTO {_TABLE} (tenant_id, label) VALUES (:t, 'b1')"),
            {"t": TENANT_B},
        )
        await session.commit()

    async with factory() as session:
        yield session

    # Teardown.
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLE}"))
    await engine.dispose()


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


async def test_no_context_is_fail_closed(rls_session):
    """With no tenant context set, the GUC is empty => zero rows (fail-closed)."""
    await clear_tenant_context(rls_session)
    assert await _count(rls_session) == 0


async def test_rls_bypass_reveals_all_rows(rls_session):
    """enable_rls_bypass opens visibility for the cross-tenant auth path."""
    # First confirm a scoped context only sees its own rows.
    await set_tenant_context(rls_session, TENANT_B)
    assert await _count(rls_session) == 1  # b1 only

    # Bypass on: all three rows (both tenants) become visible.
    await enable_rls_bypass(rls_session)
    assert await _count(rls_session) == 3
