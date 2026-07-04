"""Helpers for integration health, scope audit, and sync-run reporting."""

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.integration_sync_run import IntegrationSyncRun
from app.services.error_tracker import capture_error


def normalize_scope_list(scopes: str | Iterable[str] | None) -> list[str]:
    if scopes is None:
        return []
    if isinstance(scopes, str):
        return [s.strip() for s in scopes.split() if s.strip()]
    return [str(s).strip() for s in scopes if str(s).strip()]


def missing_scopes(
    provider: str,
    granted: str | Iterable[str] | None,
    required: str | Iterable[str] | None,
    scope_matcher,
) -> list[str]:
    granted_set = set(normalize_scope_list(granted))
    return sorted(
        scope
        for scope in normalize_scope_list(required)
        if not scope_matcher(scope, granted_set, provider)
    )


def health_from_missing(missing: list[str], active: bool = True) -> str:
    if not active:
        return "revoked"
    return "healthy" if not missing else "missing_scopes"


def apply_scope_audit(row, provider: str, required: str, scope_matcher) -> list[str]:
    missing = missing_scopes(provider, row.scopes, required, scope_matcher)
    row.missing_scopes = " ".join(missing) if missing else None
    if getattr(row, "health", None) != "revoked":
        row.health = health_from_missing(missing, getattr(row, "is_active", True))
    return missing


async def record_integration_sync_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    provider: str,
    job_type: str,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    items_ok: int = 0,
    items_failed: int = 0,
    error_summary: str | None = None,
) -> IntegrationSyncRun:
    tenant_uuid = uuid.UUID(str(tenant_id))
    await set_tenant_context(db, str(tenant_uuid))
    run = IntegrationSyncRun(
        tenant_id=tenant_uuid,
        provider=provider,
        job_type=job_type,
        started_at=started_at or datetime.now(timezone.utc),
        finished_at=finished_at or datetime.now(timezone.utc),
        status=status,
        items_ok=items_ok,
        items_failed=items_failed,
        error_summary=error_summary[:4000] if error_summary else None,
    )
    db.add(run)
    await db.flush()
    return run


async def capture_integration_error(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    provider: str,
    job_type: str,
    message: str,
    severity: str = "error",
) -> None:
    await capture_error(
        db=db,
        tenant_id=uuid.UUID(str(tenant_id)),
        error_type="integration_sync_error",
        severity=severity,
        message=f"{provider} {job_type}: {message}",
    )
