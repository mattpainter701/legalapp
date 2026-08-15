"""Transactional generation counter for tenant-private RAG materializations."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


async def advance_rag_corpus_revision(
    db: AsyncSession,
    tenant_id,
) -> None:
    """Advance a tenant revision inside the caller's corpus transaction."""
    await db.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(rag_corpus_revision=Tenant.rag_corpus_revision + 1)
    )
