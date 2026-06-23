"""Tenant role registry CRUD + per-user role assignment. manage_roles gated."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.rbac import Role, UserRole
from app.services.access_control import require_capability
from app.services.capabilities import is_valid_capability
from app.services.rbac_service import count_admin_capable_users

router = APIRouter(prefix="/api/admin/roles", tags=["roles"])


class RoleIn(BaseModel):
    name: str
    description: str | None = None
    capabilities: list[str] = []

    @field_validator("capabilities")
    @classmethod
    def _valid_caps(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if not is_valid_capability(c)]
        if bad:
            raise ValueError(f"Unknown capabilities: {bad}")
        return v


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    capabilities: list[str]
    is_system: bool


class AssignIn(BaseModel):
    role_ids: list[uuid.UUID]


@router.get("", response_model=list[RoleOut])
async def list_roles(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    rows = (
        (
            await db.execute(
                select(Role).where(Role.tenant_id == user.tenant_id).order_by(Role.name)
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    role = Role(
        tenant_id=user.tenant_id,
        name=body.name,
        description=body.description,
        capabilities=body.capabilities,
        is_system=False,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.put("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID,
    body: RoleIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    role = await db.get(Role, role_id)
    if role is None or role.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")
    role.name = body.name
    role.description = body.description
    role.capabilities = body.capabilities
    await db.commit()
    await db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    role = await db.get(Role, role_id)
    if role is None or role.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=400, detail="System roles cannot be deleted")
    await db.delete(role)
    await db.commit()


@router.put("/assign/{target_user_id}", status_code=200)
async def assign_roles(
    target_user_id: uuid.UUID,
    body: AssignIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_roles")),
):
    # Replace the user's MANUAL assignments only; group_sync rows are untouched.
    existing = (
        (
            await db.execute(
                select(UserRole).where(
                    UserRole.user_id == target_user_id, UserRole.source == "manual"
                )
            )
        )
        .scalars()
        .all()
    )
    for ur in existing:
        await db.delete(ur)
    await db.flush()

    valid_role_ids = set(
        (
            await db.execute(
                select(Role.id).where(
                    Role.tenant_id == user.tenant_id, Role.id.in_(body.role_ids)
                )
            )
        ).scalars()
    )
    for rid in valid_role_ids:
        db.add(UserRole(user_id=target_user_id, role_id=rid, source="manual"))
    await db.flush()

    # Last-admin guard: never let an assignment leave the tenant with zero admins.
    if await count_admin_capable_users(db, user.tenant_id) == 0:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="This change would remove the last admin in the firm.",
        )
    await db.commit()
    return {"assigned": [str(r) for r in valid_role_ids]}
