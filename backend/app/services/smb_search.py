"""Compatibility facade for the canonical tenant-bound SMB service.

Historically this module duplicated the relay task protocol. That second path
did not carry the file's assigned agent/share or tenant-namespaced result key,
so allowing it to drift would reintroduce cross-share routing risks. New code
should import ``smb_service`` from :mod:`app.services.smb` directly.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.schemas.smb import SmbSearchResult
from app.services.smb import smb_service

settings = get_settings()


async def search_smb_files(
    db: AsyncSession,
    tenant_id: str,
    query: str,
    matter_id: str | None = None,
    file_extensions: list[str] | None = None,
    limit: int = 20,
) -> list[SmbSearchResult]:
    if not settings.SMB_ENABLED:
        return []
    return await smb_service.search_files(
        db,
        tenant_id,
        query,
        matter_id=matter_id,
        file_extensions=file_extensions,
        limit=limit,
    )


async def request_content_fetch(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    file_id: str,
    conversation_id: str | None = None,
    reason: str = "search_result",
    redis_client=None,
) -> tuple[str, str]:
    return await smb_service.request_content_fetch(
        db,
        tenant_id,
        user_id,
        file_id,
        conversation_id,
        reason,
        redis=redis_client,
    )


async def get_content_result(
    task_id: str,
    redis_client=None,
    timeout_seconds: int | None = None,
    *,
    tenant_id: str | None = None,
    file_id: str | None = None,
) -> str | None:
    """Wait for a result without permitting an unbound legacy lookup."""
    if not tenant_id or not file_id:
        raise ValueError("tenant_id and file_id are required for SMB task results")
    return await smb_service.poll_content_result(
        task_id,
        tenant_id,
        file_id,
        redis=redis_client,
        timeout_seconds=timeout_seconds or settings.SMB_CONTENT_FETCH_TIMEOUT,
    )


async def build_smb_context(
    results: list[SmbSearchResult],
    include_snippets: bool = True,
) -> str:
    return await smb_service.build_smb_context(
        results, include_snippets=include_snippets
    )


async def get_smb_stats(db: AsyncSession, tenant_id: str) -> dict:
    return await smb_service.get_admin_stats(db, tenant_id)
