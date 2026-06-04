"""Admin-only endpoints for cloud search testing, metadata sync, and cache management."""

import logging

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin as _require_admin
from app.models.cloud_metadata import CloudMetadata
from app.models.tenant_credential import TenantCredential
from app.services.cache import ExpertiseCacheManager
from app.services.cloud_search import CloudSearchService
from app.services.cloud_sync import CloudSyncService
from app.services.llm import LLMService
from app.services.retrieval_planner import RetrievalPlanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Shared instances (lazy-init at first use) ──────────────────────────

_cloud_search: CloudSearchService | None = None
_planner: RetrievalPlanner | None = None
_cloud_sync: CloudSyncService | None = None
_cache_manager: ExpertiseCacheManager | None = None


def _get_cloud_search() -> CloudSearchService:
    global _cloud_search
    if _cloud_search is None:
        _cloud_search = CloudSearchService()
    return _cloud_search


def _get_planner() -> RetrievalPlanner:
    global _planner
    if _planner is None:
        _planner = RetrievalPlanner(LLMService())
    return _planner


def _get_cloud_sync() -> CloudSyncService:
    global _cloud_sync
    if _cloud_sync is None:
        _cloud_sync = CloudSyncService()
    return _cloud_sync


async def _get_cache_manager() -> ExpertiseCacheManager:
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = ExpertiseCacheManager()
    if not _cache_manager.redis_client:
        await _cache_manager.init()
    return _cache_manager


# ── Schemas ────────────────────────────────────────────────────────────


class _CloudSearchTestRequest(BaseModel):
    query: str
    sources: list[str] = Field(default=["gmail", "drive", "outlook", "onedrive"])
    max_hits: int = Field(default=10, ge=1, le=100)
    fetch_content: bool = False


class _CloudSearchTestResponse(BaseModel):
    plan: dict | None
    hits: list[dict]
    total_hits: int
    fetch_content_results: list[dict] | None = None


# ═══════════════════════════════════════════════════════════════════════
#  1. STATUS
# ═══════════════════════════════════════════════════════════════════════


@router.get("/cloud-search/status")
async def cloud_search_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return current cloud search status for the tenant.

    Shows which providers are connected, token expiry, scopes granted,
    and the indexed metadata count per provider.
    """
    admin = await _require_admin(request, db)
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    # Query connected credentials
    cred_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == admin.tenant_id,
            TenantCredential.is_active.is_(True),
        )
    )
    creds = cred_result.scalars().all()

    # Query metadata counts per provider
    count_result = await db.execute(
        select(
            CloudMetadata.provider,
            func.count(CloudMetadata.id).label("cnt"),
        )
        .where(
            CloudMetadata.tenant_id == admin.tenant_id,
        )
        .group_by(CloudMetadata.provider)
    )
    count_rows = count_result.all()
    metadata_counts: dict[str, int] = {r.provider: r.cnt for r in count_rows}

    # Query total metadata count
    total_result = await db.execute(
        select(func.count(CloudMetadata.id)).where(
            CloudMetadata.tenant_id == admin.tenant_id,
        )
    )
    metadata_total = total_result.scalar_one()

    # Query last sync time across records
    last_sync_result = await db.execute(
        select(func.max(CloudMetadata.last_synced)).where(
            CloudMetadata.tenant_id == admin.tenant_id,
        )
    )
    last_sync_value = last_sync_result.scalar_one()
    last_sync = last_sync_value.isoformat() if last_sync_value else None

    # Build provider status dict
    providers: dict = {
        "google": {
            "connected": False,
            "token_expires": None,
            "scopes": [],
            "metadata_count": metadata_counts.get("google", 0),
        },
        "microsoft": {
            "connected": False,
            "token_expires": None,
            "scopes": [],
            "metadata_count": metadata_counts.get("microsoft", 0),
        },
    }

    for cred in creds:
        prov = cred.provider
        if prov not in providers:
            continue
        providers[prov]["connected"] = True
        providers[prov]["token_expires"] = (
            cred.token_expires_at.isoformat() if cred.token_expires_at else None
        )
        providers[prov]["scopes"] = (
            [s.strip() for s in cred.scopes.split() if s.strip()] if cred.scopes else []
        )

    return {
        "enabled": True,
        "providers": providers,
        "metadata_total": metadata_total,
        "last_sync": last_sync,
    }


# ═══════════════════════════════════════════════════════════════════════
#  2. TEST SEARCH
# ═══════════════════════════════════════════════════════════════════════


@router.post("/cloud-search/test", response_model=_CloudSearchTestResponse)
async def cloud_search_test(
    body: _CloudSearchTestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually test cloud search with a custom query.

    Generates a search plan from the RetrievalPlanner, executes it against
    connected cloud providers, and optionally fetches full content for each
    matching hit.
    """
    admin = await _require_admin(request, db)
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    planner = _get_planner()

    # Build plan -- map user sources to provider-level detection
    source_set = set(body.sources)
    provider_set = set()
    if source_set & {"gmail", "drive"}:
        provider_set.add("google")
    if source_set & {"outlook", "onedrive", "sharepoint"}:
        provider_set.add("microsoft")

    plan = await planner.plan(
        user_question=body.query,
        active_providers=list(provider_set) if provider_set else None,
    )

    if plan and plan.get("should_search"):
        plan["sources"] = body.sources
        plan["max_hits"] = body.max_hits
    elif plan is None:
        plan = {
            "should_search": True,
            "sources": body.sources,
            "keywords": body.query.split(),
            "max_hits": body.max_hits,
        }

    # Execute search
    cloud_search = _get_cloud_search()
    hits = await cloud_search.search(db, plan, tenant_id)

    serialized_hits = [h.to_dict() for h in hits]

    # Optionally fetch full content
    fetch_results = None
    if body.fetch_content and hits:
        raw_results = await cloud_search.fetch_contents(db, hits, tenant_id)
        fetch_results = []
        for r in raw_results:
            hit_obj = r.get("hit")
            fetch_results.append(
                {
                    "hit": hit_obj.to_dict()
                    if hasattr(hit_obj, "to_dict")
                    else str(hit_obj),
                    "content": r.get("content"),
                }
            )

    return _CloudSearchTestResponse(
        plan=plan,
        hits=serialized_hits,
        total_hits=len(serialized_hits),
        fetch_content_results=fetch_results,
    )


# ═══════════════════════════════════════════════════════════════════════
#  3. SYNC METADATA
# ═══════════════════════════════════════════════════════════════════════


@router.post("/cloud-search/sync")
async def cloud_search_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger metadata sync for all connected cloud providers.

    Walks recent files and emails from each connected provider and upserts
    lightweight routing metadata into the local index.
    """
    admin = await _require_admin(request, db)
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await _get_cloud_sync().sync_all(db, tenant_id)
    return result


# ═══════════════════════════════════════════════════════════════════════
#  4. LIST METADATA
# ═══════════════════════════════════════════════════════════════════════


@router.get("/cloud-search/metadata")
async def cloud_search_list_metadata(
    request: Request,
    db: AsyncSession = Depends(get_db),
    provider: str | None = Query(
        None, description="Filter by provider (google, microsoft)"
    ),
    object_type: str | None = Query(None, description="Filter by type (file, email)"),
    q: str | None = Query(None, description="Search in title (ILIKE)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List cloud metadata entries with optional filters and pagination."""
    admin = await _require_admin(request, db)
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    filters = [CloudMetadata.tenant_id == admin.tenant_id]

    if provider:
        filters.append(CloudMetadata.provider == provider)
    if object_type:
        filters.append(CloudMetadata.object_type == object_type)
    if q:
        filters.append(CloudMetadata.title.ilike(f"%{q}%"))

    # Total count
    count_result = await db.execute(
        select(func.count(CloudMetadata.id)).where(*filters)
    )
    total_count = count_result.scalar_one()

    # Paginated rows
    rows_result = await db.execute(
        select(CloudMetadata)
        .where(*filters)
        .order_by(CloudMetadata.last_synced.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    rows = rows_result.scalars().all()

    items = []
    for row in rows:
        items.append(
            {
                "id": str(row.id),
                "provider": row.provider,
                "object_type": row.object_type,
                "object_id": row.object_id,
                "title": row.title,
                "path": row.path,
                "owner_email": row.owner_email,
                "participants": row.participants,
                "modified_time": row.modified_time.isoformat()
                if row.modified_time
                else None,
                "created_time": row.created_time.isoformat()
                if row.created_time
                else None,
                "mime_type": row.mime_type,
                "snippet": row.snippet[:200] if row.snippet else None,
                "size_bytes": row.size_bytes,
                "web_url": row.web_url,
                "last_synced": row.last_synced.isoformat() if row.last_synced else None,
            }
        )

    return {
        "items": items,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


# ═══════════════════════════════════════════════════════════════════════
#  5. INVALIDATE CACHE
# ═══════════════════════════════════════════════════════════════════════


@router.delete("/cloud-search/cache")
async def cloud_search_invalidate_cache(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Invalidate all cached cloud search results for the tenant.

    Use this after major data changes (e.g., re-sync, OAuth re-connect)
    to force fresh searches on the next query.
    """
    admin = await _require_admin(request, db)
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    cache_manager = await _get_cache_manager()
    result = await cache_manager.invalidate_cloud_search_cache(tenant_id)

    return {
        "status": "ok" if result else "cache_disabled",
        "tenant_id": tenant_id,
        "message": "Cloud search cache invalidated"
        if result
        else "Cache is not enabled (no Redis configured)",
    }
