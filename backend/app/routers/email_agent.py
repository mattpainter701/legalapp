from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.schemas.email_agent import (
    EmailScanRequest,
    EmailScanResponse,
    EmailClassification,
    ProcessedEmail,
    CalendarSyncRequest,
    CalendarSyncResponse,
)
from app.services.email_agent import email_agent
from app.services.llm import LLMService
from app.services.llm_routing import resolve_llm_route
from app.routers.calendar import run_calendar_sync

settings = get_settings()
router = APIRouter(prefix="/api/email", tags=["email"])


@router.post("/scan", response_model=EmailScanResponse)
async def scan_emails(
    body: EmailScanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = str(user.id) if not body.user_id else body.user_id

    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    llm = LLMService()
    tenant_name = getattr(user, "tenant", None)
    tenant_name = tenant_name.name if tenant_name else "WellPled"
    standard_route = await resolve_llm_route(db, tenant_id, use_premium=False)
    premium_route = await resolve_llm_route(db, tenant_id, use_premium=True)

    results = await email_agent.process_emails(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        provider=body.provider,
        llm_service=llm,
        tenant_name=tenant_name,
        max_emails=body.max_emails,
        standard_model=standard_route.model,
        premium_model=premium_route.model,
        privacy_mode=getattr(user, "privacy_mode", False),
    )

    processed = [
        ProcessedEmail(
            email_id=r["email_id"],
            subject=r.get("subject"),
            sender=r.get("from"),
            received=r.get("received"),
            classification=EmailClassification(**r["classification"]),
            draft_response=r.get("draft_response"),
        )
        for r in results
    ]

    return EmailScanResponse(
        provider=body.provider,
        user_id=user_id,
        emails_processed=len(processed),
        results=processed,
    )


@router.post("/draft-response")
async def draft_email_response(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")
    await set_tenant_context(db, tenant_id)
    llm = LLMService()
    tenant_name_obj = getattr(user, "tenant", None)
    tenant_name = tenant_name_obj.name if tenant_name_obj else "WellPled"
    premium_route = await resolve_llm_route(db, tenant_id, use_premium=True)

    draft = await email_agent.draft_response(
        email=body.get("email", {}),
        classification=body.get("classification", {}),
        llm_service=llm,
        tenant_name=tenant_name,
        practice_context=body.get("practice_context", "General legal practice"),
        model=premium_route.model,
        privacy_mode=getattr(user, "privacy_mode", False),
    )

    return {"draft_response": draft}


@router.post("/calendar", response_model=CalendarSyncResponse)
async def sync_calendar(
    body: CalendarSyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    return await run_calendar_sync(body, user, db)
