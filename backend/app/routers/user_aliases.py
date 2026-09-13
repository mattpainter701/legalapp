import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.user import User
from app.models.user_alias import UserAliasAddress
from app.models.tenant import Tenant
from app.services.email import (
    EmailCategory,
    EmailDeliveryResult,
    email_service,
)

router = APIRouter(prefix="/admin/users", tags=["user-aliases"])
settings = get_settings()
logger = logging.getLogger(__name__)


class UserAliasCreateRequest(BaseModel):
    address: EmailStr

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


async def _admin_user(user_id: str, request: Request, db: AsyncSession) -> User:
    admin = await require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))
    user = await db.scalar(
        select(User).where(User.id == user_id, User.tenant_id == admin.tenant_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/{user_id}/aliases")
async def list_user_aliases(
    user_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _admin_user(user_id, request, db)
    rows = (
        (
            await db.execute(
                select(UserAliasAddress)
                .where(
                    UserAliasAddress.user_id == user.id,
                    UserAliasAddress.tenant_id == user.tenant_id,
                )
                .order_by(UserAliasAddress.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "aliases": [
            {
                "id": str(row.id),
                "address": row.address,
                "is_verified": row.is_verified,
                "verification_method": row.verification_method,
                "verified_at": row.verified_at,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.post("/{user_id}/aliases", status_code=201)
async def add_user_alias(
    user_id: str,
    body: UserAliasCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _admin_user(user_id, request, db)
    # Serialize primary/alias ownership checks for this tenant.  The unique
    # alias index remains the final race-safe backstop.
    await db.scalar(
        select(Tenant.id).where(Tenant.id == user.tenant_id).with_for_update()
    )
    address = body.address
    if await db.scalar(
        select(User.id).where(
            User.tenant_id == user.tenant_id, func.lower(User.email) == address
        )
    ):
        raise HTTPException(
            status_code=409, detail="That address is already a primary user address"
        )
    if await db.scalar(
        select(UserAliasAddress.id).where(
            UserAliasAddress.tenant_id == user.tenant_id,
            UserAliasAddress.normalized_address == address,
        )
    ):
        raise HTTPException(
            status_code=409, detail="That address is already registered in this tenant"
        )
    raw_token = secrets.token_urlsafe(32)
    row = UserAliasAddress(
        tenant_id=user.tenant_id,
        user_id=user.id,
        address=address,
        normalized_address=address,
        verification_method="email_link",
        verification_token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        verification_expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(row)
    await db.flush()
    try:
        backend = getattr(settings, "BACKEND_URL", "").rstrip("/")
        result = await email_service.send_email(
            [address],
            "Verify your LawHand email alias",
            f'<p>Verify this address for LawHand: <a href="{backend}/api/auth/verify-alias?tenant_id={user.tenant_id}&token={raw_token}">Verify email address</a></p>',
            f"Verify this address for LawHand: {backend}/api/auth/verify-alias?tenant_id={user.tenant_id}&token={raw_token}",
            category=EmailCategory.SECURITY,
        )
        if result != EmailDeliveryResult.SENT:
            await db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Alias was not added because verification email delivery failed; try again",
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unable to deliver alias verification email")
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Alias was not added because verification email delivery failed; try again",
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="That address is already registered in this tenant"
        ) from exc
    return {
        "id": str(row.id),
        "address": row.address,
        "is_verified": False,
        "verification_required": True,
    }


@router.delete("/{user_id}/aliases/{alias_id}", status_code=204)
async def delete_user_alias(
    user_id: str, alias_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _admin_user(user_id, request, db)
    row = await db.scalar(
        select(UserAliasAddress).where(
            UserAliasAddress.id == alias_id,
            UserAliasAddress.user_id == user.id,
            UserAliasAddress.tenant_id == user.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Alias not found")
    await db.delete(row)
    await db.commit()
