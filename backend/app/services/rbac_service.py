"""Role/capability resolution and system-role seeding."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Role, UserRole
from app.services.capabilities import SYSTEM_ROLE_CAPABILITIES


async def get_user_capabilities(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = (
        await db.execute(
            select(Role.capabilities)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    ).all()
    caps: set[str] = set()
    for (capabilities,) in rows:
        caps.update(capabilities or [])
    return caps


async def seed_system_roles(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Create the four system roles for a tenant if absent. Idempotent."""
    existing = set(
        (
            await db.execute(
                select(Role.name).where(
                    Role.tenant_id == tenant_id, Role.is_system.is_(True)
                )
            )
        ).scalars()
    )
    for name, caps in SYSTEM_ROLE_CAPABILITIES.items():
        if name in existing:
            continue
        db.add(
            Role(
                tenant_id=tenant_id,
                name=name,
                description=f"System role: {name}",
                capabilities=list(caps),
                is_system=True,
            )
        )
    await db.flush()
