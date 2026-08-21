"""Verified, retry-safe destruction of expired disposable demo tenants."""

from __future__ import annotations

import shutil
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# Register every tenant-scoped table before deriving the purge plan.  The
# scheduler imports this module without necessarily loading every router, so
# relying on incidental application imports can leave valid demo tables absent
# from Base.metadata and strand an expired demo in the "purging" state.
import app.models  # noqa: F401
from app.config import get_settings
from app.database import Base, async_session_maker, set_tenant_context
from app.models.demo_session import DemoSession
from app.models.operator_audit import OperatorAuditLog
from app.models.tenant import Tenant
from app.services.demo_registry import DEMO_TABLE_REGISTRY

_DEMO_DOMAIN_SUFFIX = ".demo.invalid"

# A worker that dies between claiming a session and reaching a terminal state
# leaves the row in "purging".  Without a reclaim window the hourly job would
# refuse that tenant forever and its synthetic data would outlive its expiry.
# The purge itself is idempotent, so a later run may re-claim a stale claim.
#
# The window is measured from ``purge_started_at`` — the moment the claim was
# committed — and never from tenant expiry.  A tenant that expired long before
# its first purge attempt (a missed run, a deploy, a restart) would otherwise
# satisfy an expiry-based window the instant a live worker claimed it, so a
# second worker could enter file and row deletion concurrently.
_PURGE_RECLAIM_AFTER = timedelta(hours=1)


class DemoPurgeRefused(RuntimeError):
    pass


def _tenant_purge_lock_name(tenant_id: uuid.UUID) -> str:
    return f"lawhand:demo-purge:v1:{tenant_id}"


@asynccontextmanager
async def _tenant_purge_lock(db: AsyncSession, tenant_id: uuid.UUID):
    """Fence every destructive phase with one crash-safe database lock.

    The row claim is committed before filesystem and table deletion so a dead
    worker leaves recoverable state. A row lock cannot span that commit, and a
    timestamp is not an ownership token: a slow worker could otherwise overlap
    a reclaimer once the stale window elapsed. The transaction-scoped advisory
    lock stays pinned on a dedicated connection for the whole purge and is
    released automatically if the transaction, connection, or worker dies.
    """
    bind = db.bind
    if bind is None:
        raise RuntimeError("Demo purge database session is not bound to an engine")
    lock_engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    connection = await lock_engine.connect()
    try:
        async with connection.begin():
            acquired = await connection.scalar(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended(:lock_name, 0))"
                ),
                {"lock_name": _tenant_purge_lock_name(tenant_id)},
            )
            if acquired is not True:
                raise DemoPurgeRefused("Demo session is already being purged")
            yield
    finally:
        await connection.close()


def _purge_tables():
    missing = [name for name in DEMO_TABLE_REGISTRY if name not in Base.metadata.tables]
    if missing:
        raise DemoPurgeRefused(f"Purge tables are not registered: {sorted(missing)}")
    return {name: Base.metadata.tables[name] for name in DEMO_TABLE_REGISTRY}


def _delete_order(tables) -> list[str]:
    """Children before parents after nullable FK edges have been cleared."""
    parents: dict[str, set[str]] = {name: set() for name in tables}
    children: dict[str, set[str]] = defaultdict(set)
    for name, table in tables.items():
        for column in table.columns:
            if column.nullable:
                continue
            for fk in column.foreign_keys:
                # Deferred constraints can participate in required cycles when
                # both sides are deleted before this transaction commits.
                if (
                    fk.constraint.deferrable
                    and str(fk.constraint.initially or "").upper() == "DEFERRED"
                ):
                    continue
                parent = fk.column.table.name
                if parent in tables and parent != name:
                    parents[name].add(parent)
                    children[parent].add(name)
    ready = deque(sorted(name for name in tables if not children[name]))
    ordered: list[str] = []
    while ready:
        child = ready.popleft()
        ordered.append(child)
        for parent in sorted(parents[child]):
            children[parent].discard(child)
            if not children[parent]:
                ready.append(parent)
    if len(ordered) != len(tables):
        raise DemoPurgeRefused("Purge dependency graph contains a required FK cycle")
    return ordered


def _claim_started_at(demo: DemoSession) -> datetime | None:
    """The claim timestamp, always tz-aware so it can be compared to ``now``.

    ``purge_started_at`` is a ``timestamptz`` and asyncpg hands back aware
    values, but a naive one would raise TypeError on the comparison below and
    take down the purge rather than merely misjudging staleness.
    """
    claimed_at = demo.purge_started_at
    if claimed_at is not None and claimed_at.tzinfo is None:
        return claimed_at.replace(tzinfo=timezone.utc)
    return claimed_at


def _remove_tenant_files(tenant_id: uuid.UUID) -> None:
    upload_root = Path(get_settings().UPLOAD_DIR).resolve()
    target = (upload_root / str(tenant_id)).resolve()
    if not target.is_relative_to(upload_root) or target == upload_root:
        raise DemoPurgeRefused("Demo storage path failed its containment guard")
    if target.exists():
        shutil.rmtree(target)


async def _purge_demo_tenant_locked(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    tenant = await db.get(Tenant, tenant_id)
    if (
        tenant is None
        or tenant.billing_tier != "demo"
        or not tenant.domain.endswith(_DEMO_DOMAIN_SUFFIX)
        or tenant.expires_at is None
        or tenant.expires_at > now
    ):
        raise DemoPurgeRefused("Tenant is not an expired disposable demo")
    await set_tenant_context(db, str(tenant_id))
    # Serialize purgers on the session row.  Without the lock, two scheduler
    # workers can both observe an active session, both commit ``purging``, and
    # then race through file/database deletion.  The second worker must see
    # the first worker's state transition after waiting for this lock.
    demo = await db.scalar(
        select(DemoSession).where(DemoSession.tenant_id == tenant_id).with_for_update()
    )
    if demo is None or demo.fixture_tenant_id == tenant_id:
        raise DemoPurgeRefused("Demo session provenance check failed")
    claimed_at = _claim_started_at(demo)
    # A claim with no recorded start is never reclaimed: refusing strands one
    # tenant, while guessing risks two workers deleting the same rows and files.
    reclaimable = (
        demo.status == "purging"
        and claimed_at is not None
        and claimed_at <= now - _PURGE_RECLAIM_AFTER
    )
    if demo.status not in {"active", "expired", "failed"} and not reclaimable:
        raise DemoPurgeRefused("Demo session is already being purged")
    fixture_version = demo.fixture_version
    session_id = demo.id
    tenant.is_active = False
    demo.status = "purging"
    # Re-stamped on a reclaim too, so the next worker measures its own window.
    demo.purge_started_at = now
    await db.commit()

    try:
        _remove_tenant_files(tenant_id)
        tables = _purge_tables()
        await set_tenant_context(db, str(tenant_id))
        # Break optional cycles (invoice/retainer and self-references) first.
        for table in tables.values():
            values = {}
            for column in table.columns:
                if not column.nullable:
                    continue
                if any(fk.column.table.name in tables for fk in column.foreign_keys):
                    values[column.name] = None
            if values:
                await db.execute(
                    update(table).where(table.c.tenant_id == tenant_id).values(**values)
                )

        deleted: dict[str, int] = {}
        for name in _delete_order(tables):
            table = tables[name]
            result = await db.execute(
                delete(table).where(table.c.tenant_id == tenant_id)
            )
            deleted[name] = int(result.rowcount or 0)

        survivors = {}
        for name, table in tables.items():
            count = await db.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c.tenant_id == tenant_id)
            )
            if count:
                survivors[name] = int(count)
        if survivors:
            raise DemoPurgeRefused(f"Demo purge verification failed: {survivors}")

        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        db.add(
            OperatorAuditLog(
                action="demo.session.purged",
                actor_type="scheduler",
                resource_type="demo_session",
                resource_id=str(session_id),
                metadata_json={
                    "tenant_id": str(tenant_id),
                    "fixture_version": fixture_version,
                    "deleted_rows": sum(deleted.values()),
                },
            )
        )
        await db.commit()
        return deleted
    except Exception as exc:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id))
        failed = await db.scalar(
            select(DemoSession).where(DemoSession.tenant_id == tenant_id)
        )
        if failed is not None:
            failed.status = "failed"
        db.add(
            OperatorAuditLog(
                action="demo.session.purge_failed",
                actor_type="scheduler",
                resource_type="demo_session",
                resource_id=str(session_id),
                metadata_json={
                    "tenant_id": str(tenant_id),
                    "fixture_version": fixture_version,
                    "error_type": type(exc).__name__,
                },
            )
        )
        await db.commit()
        raise


async def purge_demo_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    """Purge one tenant while holding a cluster-wide, crash-safe fence."""
    async with _tenant_purge_lock(db, tenant_id):
        return await _purge_demo_tenant_locked(db, tenant_id)


async def purge_expired_demo_tenants() -> int:
    """Hourly scheduler entrypoint; never considers non-demo tenant rows."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as db:
        tenant_ids = list(
            (
                await db.execute(
                    select(Tenant.id).where(
                        Tenant.billing_tier == "demo",
                        Tenant.domain.endswith(_DEMO_DOMAIN_SUFFIX),
                        Tenant.expires_at <= now,
                    )
                )
            ).scalars()
        )
    purged = 0
    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            try:
                await purge_demo_tenant(db, tenant_id)
            except DemoPurgeRefused:
                continue
            purged += 1
    return purged
