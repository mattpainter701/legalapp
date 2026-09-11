"""Platform-managed Twilio sender (operator infrastructure).

Early LawHand customers send SMS through one LawHand-owned Twilio account
rather than bringing their own. The operator stores that account in the
platform settings store with the auth token encrypted by the same
``token_vault`` used for model provider keys. This is operator
infrastructure, not tenant data, so it is intentionally not tenant-scoped.

The separate, firm-owned tenant path (``app.services.sms``) is untouched.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import PlatformSetting
from app.services.token_vault import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

PLATFORM_SMS_KEY = "platform_sms_provider_v1"
PROVIDER = "twilio"
TWILIO_MESSAGES_URL = (
    "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
)
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")
_TOKEN_HINT_CHARS = 4


class PlatformSmsError(RuntimeError):
    """A safe, operator-facing failure for the shared Twilio sender."""

    def __init__(
        self,
        message: str,
        status_code: int = 503,
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class PlatformSmsCredentials:
    account_sid: str
    auth_token: str
    messaging_service_sid: str | None
    from_number: str | None
    status_callback_url: str | None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _mask_account_sid(value: str) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:4]}…{value[-3:]}"


def _sender_ready(config: dict) -> bool:
    return bool(
        _clean(config.get("account_sid"))
        and config.get("encrypted_auth_token")
        and (_clean(config.get("messaging_service_sid")) or _clean(config.get("from_number")))
    )


def _public_view(config: dict | None) -> dict:
    config = config if isinstance(config, dict) else {}
    account_sid = _clean(config.get("account_sid"))
    return {
        "provider": PROVIDER,
        "configured": bool(config),
        "account_sid": _mask_account_sid(account_sid),
        "auth_token_configured": bool(config.get("encrypted_auth_token")),
        "auth_token_hint": config.get("auth_token_hint") or "",
        "messaging_service_sid": config.get("messaging_service_sid") or None,
        "from_number": config.get("from_number") or None,
        "status_callback_url": config.get("status_callback_url") or None,
        "sender_ready": _sender_ready(config),
        "is_active": bool(config.get("is_active", False)),
        "updated_at": config.get("updated_at"),
        "updated_by": config.get("updated_by"),
    }


async def get_platform_sms_config(db: AsyncSession) -> dict | None:
    row = await db.scalar(
        select(PlatformSetting).where(PlatformSetting.key == PLATFORM_SMS_KEY)
    )
    value = row.value if row else None
    return value if isinstance(value, dict) else None


async def get_platform_sms_provider_public(db: AsyncSession) -> dict:
    return _public_view(await get_platform_sms_config(db))


async def upsert_platform_sms_provider(
    db: AsyncSession,
    *,
    account_sid: str | None = None,
    auth_token: str | None = None,
    messaging_service_sid: str | None = None,
    from_number: str | None = None,
    status_callback_url: str | None = None,
    is_active: bool | None = None,
    actor: str | None = None,
) -> dict:
    """Create or update the shared Twilio account.

    Only non-None fields are written. ``auth_token`` is re-encrypted when
    supplied and otherwise left untouched, so the operator can edit the sender
    without re-entering the secret.
    """

    row = await db.scalar(
        select(PlatformSetting).where(PlatformSetting.key == PLATFORM_SMS_KEY)
    )
    config = dict(row.value) if row and isinstance(row.value, dict) else {}

    if account_sid is not None:
        config["account_sid"] = _clean(account_sid)
    if messaging_service_sid is not None:
        config["messaging_service_sid"] = _clean(messaging_service_sid)
    if from_number is not None:
        config["from_number"] = _clean(from_number)
    if status_callback_url is not None:
        config["status_callback_url"] = _clean(status_callback_url)
    if is_active is not None:
        config["is_active"] = bool(is_active)

    if auth_token is not None and _clean(auth_token):
        token = _clean(auth_token)
        config["encrypted_auth_token"] = encrypt_token(token)
        config["auth_token_hint"] = token[-_TOKEN_HINT_CHARS:]

    if auth_token is not None and not _clean(auth_token):
        # Explicitly blanking the field clears the stored secret.
        config.pop("encrypted_auth_token", None)
        config.pop("auth_token_hint", None)

    if not (_clean(config.get("account_sid")) and config.get("encrypted_auth_token")):
        raise PlatformSmsError(
            "Account SID and Auth Token are both required.",
            status_code=400,
            code="platform_sms_incomplete",
        )
    if not (
        _clean(config.get("messaging_service_sid"))
        or _clean(config.get("from_number"))
    ):
        raise PlatformSmsError(
            "A Messaging Service SID or a From number is required.",
            status_code=400,
            code="platform_sms_incomplete",
        )

    config["provider"] = PROVIDER
    config.setdefault("is_active", True)
    config["updated_at"] = datetime.now(timezone.utc).isoformat()
    config["updated_by"] = actor

    if row is None:
        row = PlatformSetting(key=PLATFORM_SMS_KEY, value=config)
        db.add(row)
    else:
        row.value = config
    await db.commit()
    await db.refresh(row)
    return dict(row.value)


async def delete_platform_sms_provider(db: AsyncSession) -> bool:
    row = await db.scalar(
        select(PlatformSetting).where(PlatformSetting.key == PLATFORM_SMS_KEY)
    )
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def resolve_platform_sms_credentials(
    db: AsyncSession,
) -> PlatformSmsCredentials:
    config = await get_platform_sms_config(db)
    if not config or not config.get("is_active") or not _sender_ready(config):
        raise PlatformSmsError(
            "The shared SMS sender is not configured or is inactive.",
            status_code=503,
            code="platform_sms_unconfigured",
        )
    try:
        token = decrypt_token(config["encrypted_auth_token"]).strip()
    except Exception as exc:  # noqa: BLE001 - never leak provider detail
        raise PlatformSmsError(
            "The shared SMS credentials are unavailable.",
            status_code=503,
            code="platform_sms_credentials_unavailable",
        ) from exc
    if not token:
        raise PlatformSmsError(
            "The shared SMS credentials are unavailable.",
            status_code=503,
            code="platform_sms_credentials_unavailable",
        )
    return PlatformSmsCredentials(
        account_sid=_clean(config.get("account_sid")),
        auth_token=token,
        messaging_service_sid=_clean(config.get("messaging_service_sid")) or None,
        from_number=_clean(config.get("from_number")) or None,
        status_callback_url=_clean(config.get("status_callback_url")) or None,
    )


def _validate_destination(to: str) -> str:
    normalized = _clean(to)
    if not _E164.match(normalized):
        raise PlatformSmsError(
            "Enter the destination in E.164 format, for example +15551234567.",
            status_code=400,
            code="platform_sms_invalid_recipient",
        )
    return normalized


async def send_platform_test_sms(
    db: AsyncSession,
    *,
    to: str,
    body: str | None = None,
) -> dict:
    """Send one operator-initiated test message through the shared account."""

    destination = _validate_destination(to)
    credentials = await resolve_platform_sms_credentials(db)

    data: dict[str, str] = {
        "To": destination,
        "Body": _clean(body) or "LawHand SMS test message.",
    }
    if credentials.messaging_service_sid:
        data["MessagingServiceSid"] = credentials.messaging_service_sid
    elif credentials.from_number:
        data["From"] = credentials.from_number
    if credentials.status_callback_url:
        data["StatusCallback"] = credentials.status_callback_url

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                TWILIO_MESSAGES_URL.format(account_sid=credentials.account_sid),
                auth=(credentials.account_sid, credentials.auth_token),
                data=data,
            )
    except httpx.HTTPError as exc:
        raise PlatformSmsError(
            "The SMS provider could not be reached. Try again.",
            status_code=502,
            code="platform_sms_transport_error",
        ) from exc

    if response.status_code not in (200, 201):
        detail = ""
        try:
            detail = str((response.json() or {}).get("message") or "")
        except Exception:  # noqa: BLE001 - provider body may be non-JSON
            detail = ""
        logger.warning(
            "Platform Twilio test send rejected (status=%s)",
            response.status_code,
        )
        raise PlatformSmsError(
            detail or "The SMS provider rejected the test message.",
            status_code=502,
            code="platform_sms_provider_rejected",
        )

    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - a 2xx with a non-JSON body is unusable
        raise PlatformSmsError(
            "The SMS provider returned an unreadable response.",
            status_code=502,
            code="platform_sms_provider_response_invalid",
        )
    return {
        "status": "sent",
        "sid": str(payload.get("sid") or ""),
        "provider_status": str(payload.get("status") or ""),
        "to": destination,
        "from_number": credentials.from_number,
        "messaging_service_sid": credentials.messaging_service_sid,
    }
