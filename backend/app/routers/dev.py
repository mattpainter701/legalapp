"""
Dev-only convenience endpoints — all return 404 unless DEV_MODE=true.

Provides:
  POST /dev/login          — email-only login, skips OAuth
  POST /dev/set-all-payg   — reset every tenant's billing_tier to payg
  GET  /dev/users          — list all users with pre-built Bearer tokens
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import TokenResponse

settings = get_settings()
router = APIRouter(prefix="/dev", tags=["dev"])


def _dev_guard():
    if not settings.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")


def _mint_token(user: User, tenant: Tenant) -> str:
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "billing_tier": tenant.billing_tier,
        "exp": datetime.now(timezone.utc) + timedelta(days=365),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ── Request / Response schemas ────────────────────────────────────────────────


class DevLoginRequest(BaseModel):
    email: str
    full_name: Optional[str] = None
    role: str = "admin"


class DevUserRow(BaseModel):
    user_id: str
    email: str
    role: str
    tenant_id: str
    tenant_name: str
    billing_tier: str
    token: str


class DevUsersResponse(BaseModel):
    users: list[DevUserRow]


class SetAllPaygResponse(BaseModel):
    tenants_updated: int
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login", response_model=TokenResponse)
async def dev_login(
    body: DevLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Dev shortcut: log in as any email without OAuth.
    Creates the tenant (by email domain) and user if they don't exist.
    First user per tenant becomes admin regardless of the role field.
    """
    _dev_guard()

    email = body.email.lower().strip()
    domain = email.split("@")[-1]

    # Get or create tenant
    result = await db.execute(select(Tenant).where(Tenant.domain == domain))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            id=uuid.uuid4(),
            name=domain,
            domain=domain,
            billing_tier="payg",
            is_active=True,
        )
        db.add(tenant)
        await db.flush()

    # Get or create user
    result = await db.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # First user in tenant gets admin
        count_result = await db.execute(select(User).where(User.tenant_id == tenant.id))
        is_first = len(count_result.scalars().all()) == 0
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=email,
            full_name=body.full_name or email.split("@")[0].replace(".", " ").title(),
            role="admin" if is_first else body.role,
            oauth_provider="dev",
            oauth_subject=f"dev-{email}",
            is_active=True,
        )
        db.add(user)
        await db.flush()

    await db.commit()
    await db.refresh(user)
    await db.refresh(tenant)

    token = _mint_token(user, tenant)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=str(user.id),
        tenant_id=str(tenant.id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


@router.post("/set-all-payg", response_model=SetAllPaygResponse)
async def set_all_payg(db: AsyncSession = Depends(get_db)):
    """Reset every tenant's billing_tier to 'payg'. Dev only."""
    _dev_guard()

    result = await db.execute(
        update(Tenant).values(billing_tier="payg").returning(Tenant.id)
    )
    updated = len(result.fetchall())
    await db.commit()

    return SetAllPaygResponse(
        tenants_updated=updated,
        message=f"Set {updated} tenant(s) to payg billing.",
    )


@router.get("/users", response_model=DevUsersResponse)
async def list_dev_users(db: AsyncSession = Depends(get_db)):
    """List all users with ready-to-use Bearer tokens (365-day expiry). Dev only."""
    _dev_guard()

    rows = await db.execute(
        select(User, Tenant)
        .join(Tenant, Tenant.id == User.tenant_id)
        .order_by(Tenant.domain, User.email)
    )
    users = []
    for user, tenant in rows.all():
        users.append(
            DevUserRow(
                user_id=str(user.id),
                email=user.email,
                role=user.role,
                tenant_id=str(tenant.id),
                tenant_name=tenant.name,
                billing_tier=tenant.billing_tier,
                token=_mint_token(user, tenant),
            )
        )

    return DevUsersResponse(users=users)
