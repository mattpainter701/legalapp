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
    CalendarEventResponse,
)
from app.services.email_agent import email_agent
from app.services.calendar_sync import calendar_sync
from app.services.llm import LLMService

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
    tenant_name = tenant_name.name if tenant_name else "Clarity Legal"

    results = await email_agent.process_emails(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        provider=body.provider,
        llm_service=llm,
        tenant_name=tenant_name,
        max_emails=body.max_emails,
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
    await get_current_user(request, db)
    llm = LLMService()
    tenant_name = "Clarity Legal"

    draft = await email_agent.draft_response(
        email=body.get("email", {}),
        classification=body.get("classification", {}),
        llm_service=llm,
        tenant_name=tenant_name,
        practice_context=body.get("practice_context", "General legal practice"),
    )

    return {"draft_response": draft}


@router.post("/calendar", response_model=CalendarSyncResponse)
async def sync_calendar(
    body: CalendarSyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = str(user.id) if not body.user_id else body.user_id

    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    if body.provider == "microsoft":
        events = await calendar_sync.ms_get_events(db, tenant_id, user_id)
    elif body.provider == "google":
        events = await calendar_sync.google_get_events(db, tenant_id, user_id)
    else:
        raise HTTPException(
            status_code=400, detail=f"Unsupported provider: {body.provider}"
        )

    deadlines_created = 0
    if body.sync_deadlines:
        sync_result = await calendar_sync.sync_deadlines_to_calendar(
            db, tenant_id, user_id, body.provider
        )
        deadlines_created = sync_result.get("created", 0)

    return CalendarSyncResponse(
        provider=body.provider,
        events=[
            CalendarEventResponse(
                id=e["id"],
                provider=e["provider"],
                subject=e.get("subject"),
                start=e.get("start"),
                end=e.get("end"),
                location=e.get("location"),
            )
            for e in events
        ],
        deadlines_created=deadlines_created,
    )
