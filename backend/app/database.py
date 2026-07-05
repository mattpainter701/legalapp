from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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
    pool_size=10,
    max_overflow=20,
)

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
