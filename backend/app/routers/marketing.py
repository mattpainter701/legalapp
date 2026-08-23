import html
import logging
import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.marketing import MarketingDemoRequest, MarketingEvent
from app.schemas.marketing import (
    DemoRequestAccepted,
    DemoRequestCreate,
    MarketingEventCreate,
)
from app.services.email import EmailDeliveryResult, email_service


logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/marketing", tags=["marketing"])


def _clean(value: str | None) -> str:
    return html.escape(value or "Not provided")


@router.post(
    "/demo-requests",
    response_model=DemoRequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_demo_request(
    payload: DemoRequestCreate,
    db: AsyncSession = Depends(get_db),
) -> DemoRequestAccepted:
    # Return the normal accepted shape without creating a record. This keeps the
    # honeypot invisible to automated submitters.
    if payload.website:
        return DemoRequestAccepted(id=uuid.uuid4())

    request_record = MarketingDemoRequest(
        name=payload.name,
        email=str(payload.email).lower(),
        firm_name=payload.firm_name,
        phone=payload.phone or None,
        team_size=payload.team_size or None,
        message=payload.message or None,
        source_path=payload.source_path or "/demo",
        campaign=payload.campaign,
    )
    db.add(request_record)
    await db.commit()
    await db.refresh(request_record)

    subject = f"LawHand demo request — {payload.firm_name}"
    text_body = (
        f"Name: {payload.name}\nEmail: {payload.email}\nFirm: {payload.firm_name}\n"
        f"Phone: {payload.phone or 'Not provided'}\nTeam size: {payload.team_size or 'Not provided'}\n"
        f"Source: {payload.source_path}\n\n{payload.message or 'No additional message.'}"
    )
    html_body = f"""
      <h2>New LawHand demo request</h2>
      <p><strong>Name:</strong> {_clean(payload.name)}<br>
      <strong>Email:</strong> {_clean(str(payload.email))}<br>
      <strong>Firm:</strong> {_clean(payload.firm_name)}<br>
      <strong>Phone:</strong> {_clean(payload.phone)}<br>
      <strong>Team size:</strong> {_clean(payload.team_size)}<br>
      <strong>Source:</strong> {_clean(payload.source_path)}</p>
      <p>{_clean(payload.message)}</p>
    """
    delivery = await email_service.send_email(
        [settings.MARKETING_LEAD_EMAIL], subject, html_body, text_body
    )
    request_record.notification_status = delivery.value
    await db.commit()
    if delivery is not EmailDeliveryResult.SENT:
        logger.warning(
            "Marketing demo request stored but notification was not sent (request_id=%s, status=%s)",
            request_record.id,
            delivery.value,
        )

    return DemoRequestAccepted(id=request_record.id)


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def record_marketing_event(
    payload: MarketingEventCreate,
    db: AsyncSession = Depends(get_db),
) -> Response:
    db.add(
        MarketingEvent(
            name=payload.name,
            session_id=payload.session_id,
            page=payload.page,
            properties=payload.properties,
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
