"""Firm-wide document search API with fail-closed source authorization."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.tenant import get_current_user
from app.schemas.firm_memory import (
    FirmMemoryCapabilitiesResponse,
    FirmMemoryDocumentSearchRequest,
    FirmMemoryDocumentSearchResponse,
    FirmMemorySourceInfo,
)
from app.config import get_settings
from app.services.rbac_service import get_user_capabilities
from app.services.firm_memory import firm_memory_search_service
from app.services.firm_memory_authorization import FirmMemoryAuthorizationError

router = APIRouter(prefix="/api/v1/firm-memory", tags=["firm-memory"])
settings = get_settings()


@router.get("/capabilities", response_model=FirmMemoryCapabilitiesResponse)
async def get_firm_memory_capabilities(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Return the effective UI rollout state without widening authorization."""
    capabilities = await get_user_capabilities(db, user.id)
    entitled = "search_firm_memory" in capabilities
    generalized = settings.FIRM_MEMORY_GENERAL_SEARCH_ENABLED
    return FirmMemoryCapabilitiesResponse(
        search_entitled=entitled,
        generalized_search_enabled=generalized,
        unified_research_available=entitled and generalized,
    )


@router.get("/sources", response_model=list[FirmMemorySourceInfo])
async def list_firm_memory_sources(
    request: Request,
    matter_ids: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List only sources authorized for the actor and optional matter context."""
    try:
        return await firm_memory_search_service.list_sources(
            db, user=user, matter_id_values=matter_ids
        )
    except FirmMemoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/search", response_model=FirmMemoryDocumentSearchResponse)
async def search_firm_memory(
    body: FirmMemoryDocumentSearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Search only sources this active tenant member is authorized to query."""
    try:
        return await firm_memory_search_service.search(db, user=user, request=body)
    except FirmMemoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
