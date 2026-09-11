"""Platform-operator control of the shared Twilio sender.

Endpoints:
  GET    /api/platform/sms/provider   — masked shared sender configuration
  PUT    /api/platform/sms/provider   — create or update the shared sender
  DELETE /api/platform/sms/provider   — remove the shared sender
  POST   /api/platform/sms/test       — send one test message

The account is operator infrastructure shared by tenants, not tenant data.
Tenant-owned SMS configuration stays on ``/api/sms/config``.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.operator_audit import record_operator_audit
from app.services.platform_auth import require_platform_token
from app.services.platform_sms import (
    PLATFORM_SMS_KEY,
    PlatformSmsError,
    delete_platform_sms_provider,
    get_platform_sms_provider_public,
    send_platform_test_sms,
    upsert_platform_sms_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform/sms", tags=["platform-sms"])


class PlatformSmsProviderUpdate(BaseModel):
    account_sid: str | None = Field(default=None, max_length=64)
    auth_token: str | None = Field(default=None, max_length=128)
    messaging_service_sid: str | None = Field(default=None, max_length=64)
    from_number: str | None = Field(default=None, max_length=32)
    status_callback_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None


class PlatformSmsTestRequest(BaseModel):
    to: str = Field(max_length=32)
    body: str | None = Field(default=None, max_length=480)


def _mask_phone(value: str) -> str:
    value = value or ""
    return f"***{value[-4:]}" if len(value) >= 4 else "***"


def _raise_sms_error(exc: PlatformSmsError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/provider")
async def get_provider(request: Request, db: AsyncSession = Depends(get_db)):
    require_platform_token(request)
    return await get_platform_sms_provider_public(db)


@router.put("/provider")
async def update_provider(
    body: PlatformSmsProviderUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    principal = require_platform_token(request)
    try:
        await upsert_platform_sms_provider(
            db,
            account_sid=body.account_sid,
            auth_token=body.auth_token,
            messaging_service_sid=body.messaging_service_sid,
            from_number=body.from_number,
            status_callback_url=body.status_callback_url,
            is_active=body.is_active,
            actor=principal.actor_id,
        )
    except PlatformSmsError as exc:
        _raise_sms_error(exc)

    await record_operator_audit(
        db,
        request,
        action="platform.sms_provider.updated",
        resource_type="platform_setting",
        resource_id=PLATFORM_SMS_KEY,
        metadata={
            "fields": sorted(
                field
                for field in (
                    "account_sid",
                    "auth_token",
                    "messaging_service_sid",
                    "from_number",
                    "status_callback_url",
                    "is_active",
                )
                if getattr(body, field) is not None
            ),
            "is_active": body.is_active,
        },
    )
    return await get_platform_sms_provider_public(db)


@router.delete("/provider")
async def clear_provider(request: Request, db: AsyncSession = Depends(get_db)):
    require_platform_token(request)
    removed = await delete_platform_sms_provider(db)
    if not removed:
        raise HTTPException(
            status_code=404, detail="No shared SMS sender is configured."
        )
    await record_operator_audit(
        db,
        request,
        action="platform.sms_provider.deleted",
        resource_type="platform_setting",
        resource_id=PLATFORM_SMS_KEY,
    )
    return {"status": "deleted"}


@router.post("/test")
async def send_test(
    body: PlatformSmsTestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_platform_token(request)
    try:
        result = await send_platform_test_sms(db, to=body.to, body=body.body)
    except PlatformSmsError as exc:
        _raise_sms_error(exc)
    await record_operator_audit(
        db,
        request,
        action="platform.sms.test_sent",
        resource_type="platform_setting",
        resource_id=PLATFORM_SMS_KEY,
        metadata={"to": _mask_phone(result["to"]), "sid": result.get("sid")},
    )
    return result
