"""Role and licensing access helpers."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.tenant import get_current_user

ADMIN_ROLE = "admin"
ACCOUNTANT_ROLE = "accountant"
STANDARD_ROLES = {"user", ACCOUNTANT_ROLE, ADMIN_ROLE}
FINANCE_ROLES = {ADMIN_ROLE, ACCOUNTANT_ROLE}


def normalize_role(role: str | None) -> str:
    value = (role or "user").strip().lower()
    return value if value in STANDARD_ROLES else "user"


def can_manage_finance(role: str | None) -> bool:
    return normalize_role(role) in FINANCE_ROLES


async def require_finance_admin(request: Request, db: AsyncSession = Depends(get_db)):
    """Allow tenant admins and accountants into billing/licensing surfaces."""
    from app.services.rbac_service import get_user_capabilities

    user = await get_current_user(request, db)
    caps = await get_user_capabilities(db, user.id)
    if "view_billing" in caps or "manage_billing" in caps:
        return user
    if can_manage_finance(user.role):  # legacy fallback
        return user
    raise HTTPException(status_code=403, detail="Finance access required")


def require_capability(capability: str):
    """Dependency factory: 403 unless the user holds `capability` via any role."""

    async def _dep(request: Request, db: AsyncSession = Depends(get_db)):
        from app.services.rbac_service import get_user_capabilities

        user = await get_current_user(request, db)
        caps = await get_user_capabilities(db, user.id)
        if capability not in caps:
            raise HTTPException(
                status_code=403, detail=f"Missing capability: {capability}"
            )
        return user

    return _dep
