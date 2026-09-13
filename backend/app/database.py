from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
from uuid import UUID

from app.config import get_settings

settings = get_settings()
NO_TENANT_CONTEXT = "00000000-0000-0000-0000-000000000000"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
)

# Conversation generation holds a session-level advisory lock for the whole
# turn, so its connection cannot be returned to the pool between statements the
# way an ordinary request's is. Those long checkouts share nothing with
# request traffic except the pool, and that sharing is the failure: once
# concurrent chat turns reach the pool ceiling, unrelated reads queue for
# DATABASE_POOL_TIMEOUT_SECONDS and then error. A separate bounded pool keeps
# the two workloads in separate failure domains — chat saturating returns a
# "busy" on chat, and everything else keeps its own slots.
#
# ``get_generation_engine()`` is the only supported accessor: tests bind their
# sessions to a throwaway engine, and the lease must follow the session it was
# handed rather than reaching for this one (see
# ``chat._try_conversation_generation_lease``).
generation_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_GENERATION_POOL_SIZE,
    max_overflow=settings.DATABASE_GENERATION_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_GENERATION_POOL_TIMEOUT_SECONDS,
)


def get_generation_engine(request_engine: AsyncEngine) -> AsyncEngine:
    """Return the pool a generation lease should draw its connection from.

    Falls back to ``request_engine`` whenever it is not the application engine.
    Tests (and any caller that binds a session to its own engine) must keep
    their lease on that engine, or the lock would be taken on a connection to a
    different database than the one under test.
    """
    return generation_engine if request_engine is engine else request_engine


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db(request: Request = None) -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session with the tenant RLS context applied automatically.

    FastAPI injects the live ``Request`` for ``Depends(get_db)``. The
    ``request.state.tenant_id`` is set upstream by the tenant middleware; when
    present we bind it to the transaction-local ``app.current_tenant_id`` GUC so
    that Row Level Security filters every query structurally — no per-route
    ``.where(tenant_id == ...)`` is required for isolation. The ``= None`` default
    keeps any direct ``get_db()`` callers (e.g. background jobs) working; they get
    a session with no tenant context (fail-closed: RLS yields zero rows).
    """
    async with async_session_maker() as session:
        tenant_id = None
        if request is not None:
            tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            tenant_id = str(UUID(str(tenant_id)))

            @event.listens_for(session.sync_session, "after_begin")
            def _rebind_tenant_context(sync_session, transaction, connection):
                connection.execute(
                    text(
                        """
                        SELECT
                          set_config('app.current_tenant_id', :tenant_id, true),
                          set_config('app.tenant_id', :tenant_id, true)
                        """
                    ),
                    {"tenant_id": tenant_id},
                )

        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def bind_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """Pin a session to one tenant for its whole life, across commits.

    ``set_tenant_context`` writes transaction-local GUCs, so the next commit
    drops them and every later query on that session runs with no tenant — RLS
    then fails closed and returns nothing. ``get_db`` avoids that for ordinary
    requests by registering an ``after_begin`` rebind, but it can only do so for
    a tenant the middleware already resolved from the firm ``access_token``.

    Surfaces that resolve their own tenant after the session already exists —
    the client portal authenticates on its own cookie, which the middleware
    never reads — need the same rebind installed at that point instead.
    """
    normalized = str(UUID(str(tenant_id))) if tenant_id else NO_TENANT_CONTEXT

    @event.listens_for(session.sync_session, "after_begin")
    def _rebind_tenant_context(sync_session, transaction, connection):
        connection.execute(
            text(
                """
                SELECT
                  set_config('app.current_tenant_id', :tenant_id, true),
                  set_config('app.tenant_id', :tenant_id, true)
                """
            ),
            {"tenant_id": normalized},
        )

    await set_tenant_context(session, normalized)


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """Set the current tenant context for RLS policies.

    Older migrations created policies against ``app.tenant_id``; newer ones use
    ``app.current_tenant_id``. Keep both transaction-local GUCs in sync until
    all policies are normalized.
    """
    tenant_id = str(UUID(str(tenant_id))) if tenant_id else NO_TENANT_CONTEXT
    await session.execute(
        text(
            """
            SELECT
              set_config('app.current_tenant_id', :tenant_id, true),
              set_config('app.tenant_id', :tenant_id, true)
            """
        ),
        {"tenant_id": tenant_id},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    """Clear the tenant RLS context (transaction-local).

    Resets both tenant-context GUCs to a sentinel UUID so RLS policies match no
    rows (fail-closed), including legacy policies that cast directly to UUID.
    Useful before/after a cross-tenant operation on a reused session.
    """
    await session.execute(
        text(
            """
            SELECT
              set_config('app.current_tenant_id', :tenant_id, true),
              set_config('app.tenant_id', :tenant_id, true)
            """
        ),
        {"tenant_id": NO_TENANT_CONTEXT},
    )


async def enable_rls_bypass(session: AsyncSession) -> None:
    """Enable the auth cross-tenant RLS bypass for this transaction only.

    Sets the transaction-local ``app.rls_bypass`` GUC to ``'on'``, which the
    ``rls_bypass_users`` policy on the ``users`` table keys on, allowing the
    auth router to perform legitimate cross-tenant lookups (login / forgot /
    reset by email, OAuth exchange by id) under a non-owner DB role.

    WARNING: this is ONLY for the auth cross-tenant lookup path. It is
    transaction-local (the third ``set_config`` arg is ``true``) and MUST NEVER
    be called from tenant-scoped request handlers — doing so would defeat
    tenant isolation.
    """
    await session.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))


async def set_inbound_email_route_lookup(
    session: AsyncSession, *, enabled: bool
) -> None:
    """Briefly permit opaque inbound-alias resolution before tenant binding.

    The corresponding RLS policy is SELECT-only and exists solely on
    ``inbound_email_aliases`` and ``firm_inbound_email_aliases``. Callers must authenticate the delivery before
    enabling it and bind the resolved tenant immediately afterward.
    """
    await session.execute(
        text("SELECT set_config('app.inbound_email_route_lookup', :value, true)"),
        {"value": "on" if enabled else "off"},
    )


async def set_smb_agent_bootstrap_lookup(
    session: AsyncSession,
    *,
    api_key_hash: str | None = None,
    pairing_code: str | None = None,
) -> None:
    """Allow exactly one pre-tenant SMB agent lookup for this transaction.

    SMB authentication and pairing necessarily discover the tenant from the
    credential itself.  The migration-129 SELECT policy permits only the row
    whose hash/code equals one of these transaction-local values; it does not
    bypass RLS or grant access to any other SMB table.  Callers must bind the
    returned agent's tenant immediately and then clear the lookup values.
    """
    if (api_key_hash is None) == (pairing_code is None):
        raise ValueError("exactly one SMB bootstrap lookup value is required")
    await session.execute(
        text(
            """
            SELECT
              set_config('app.smb_agent_api_key_hash', :api_key_hash, true),
              set_config('app.smb_agent_pairing_code', :pairing_code, true)
            """
        ),
        {
            "api_key_hash": api_key_hash or "",
            "pairing_code": pairing_code or "",
        },
    )


async def clear_smb_agent_bootstrap_lookup(session: AsyncSession) -> None:
    """Remove the transaction-local SMB bootstrap selectors."""
    await session.execute(
        text(
            """
            SELECT
              set_config('app.smb_agent_api_key_hash', '', true),
              set_config('app.smb_agent_pairing_code', '', true)
            """
        )
    )
