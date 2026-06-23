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


async def provision_tenant_rbac(
    db: AsyncSession, tenant_id: uuid.UUID, admin_user_id: uuid.UUID
) -> None:
    """Seed system roles for a new tenant and assign the creator Administrator.

    Idempotent: safe to call more than once. Flushes; caller commits.
    """
    await seed_system_roles(db, tenant_id)
    admin_role_id = await db.scalar(
        select(Role.id).where(Role.tenant_id == tenant_id, Role.name == "Administrator")
    )
    if admin_role_id is None:
        return
    existing = await db.scalar(
        select(UserRole.id).where(
            UserRole.user_id == admin_user_id, UserRole.role_id == admin_role_id
        )
    )
    if existing is None:
        db.add(UserRole(user_id=admin_user_id, role_id=admin_role_id, source="manual"))
    await db.flush()


async def count_admin_capable_users(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Number of distinct active users in the tenant holding admin_settings."""
    from app.models.user import User

    rows = (
        await db.execute(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                Role.tenant_id == tenant_id,
                User.is_active.is_(True),
                Role.capabilities.contains(["admin_settings"]),
            )
            .distinct()
        )
    ).all()
    return len(rows)
