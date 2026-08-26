"""Fail-closed deployment gates for customer-facing Assistant surfaces."""

from fastapi import HTTPException

from app.config import get_settings


settings = get_settings()


def require_after_call_concierge() -> None:
    if not settings.VIRTUAL_ASSISTANT_ENABLED:
        raise HTTPException(status_code=404, detail="Virtual assistant is not enabled")
    if not settings.AFTER_CALL_CONCIERGE_ENABLED:
        raise HTTPException(
            status_code=503, detail="After-call concierge is not available"
        )


def require_engagement_packets() -> None:
    require_after_call_concierge()
    if not settings.ENGAGEMENT_PACKETS_ENABLED:
        raise HTTPException(
            status_code=503, detail="Engagement packets are not available"
        )
