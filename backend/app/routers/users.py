"""Tenant user search — accessible to all authenticated users (non-admin)."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/users", tags=["users"])


class UserSearchResult(BaseModel):
    id: str
    full_name: str | None
    email: str
    role: str


@router.get("/search", response_model=list[UserSearchResult])
async def search_users(
    q: str = Query(..., min_length=2, max_length=100),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search users in the current tenant by name or email. Open to all authenticated users."""
    await set_tenant_context(db, str(current_user.tenant_id))

    pattern = f"%{q}%"
    result = await db.execute(
        select(User)
        .where(
            User.tenant_id == current_user.tenant_id,
            User.is_active.is_(True),
            or_(
                User.full_name.ilike(pattern),
                User.email.ilike(pattern),
            ),
        )
        .order_by(User.full_name.asc())
        .limit(15)
    )
    users = result.scalars().all()

    return [
        UserSearchResult(
            id=str(u.id),
            full_name=u.full_name,
            email=u.email,
            role=u.role,
        )
        for u in users
    ]
