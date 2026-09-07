"""Role view profiles narrow navigation without granting or revoking access."""

from sqlalchemy import select

from app.models.rbac import Role, UserRole
from app.schemas.navigation import NAVIGATION_MODULES


async def resolve_navigation(db, user, enabled_modules, default_route):
    profiles = (
        (
            await db.execute(
                select(Role.navigation_paths)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(
                    UserRole.user_id == user.id,
                    UserRole.tenant_id == user.tenant_id,
                    Role.tenant_id == user.tenant_id,
                )
                .order_by(Role.name, Role.id)
            )
        )
        .scalars()
        .all()
    )
    configured = [paths for paths in profiles if paths is not None]
    # Roles without a view profile do not widen an explicitly configured view.
    paths = (
        list(dict.fromkeys(p for profile in configured for p in profile))
        if configured
        else None
    )
    preferences = getattr(user, "navigation_preferences", None) or {}
    allowed = [
        p
        for p, module in NAVIGATION_MODULES.items()
        if module in enabled_modules and (paths is None or p in paths)
    ]
    order = preferences.get("order", [])
    visible = [
        p
        for p in dict.fromkeys([*order, *allowed])
        if p in allowed and p not in preferences.get("hidden", [])
    ]
    # Preserve the plan landing page until a profile or personal layout changes it.
    if paths is not None or preferences.get("hidden") or order:
        default_route = visible[0] if visible else "/profile"
    return paths, preferences, default_route
