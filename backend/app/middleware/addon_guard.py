"""Live entitlement checks for specialized CRUD workspaces."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.services.plugin_entitlements import (
    load_plugin_entitlement,
    plugin_entitlement_is_active,
)


def require_addon_workflow(plugin_name: str):
    async def require_access(request: Request, db: AsyncSession = Depends(get_db)):
        user = await get_current_user(request, db)
        await set_tenant_context(db, str(user.tenant_id))
        entitlement = await load_plugin_entitlement(db, user.tenant_id, plugin_name)
        if plugin_entitlement_is_active(entitlement):
            return
        # Preserve the existing installation-wide legacy provisioning policy.
        # An explicit disabled, expired, or malformed entitlement always wins.
        if not get_settings().PLUGIN_ENTITLEMENT_STRICT and (
            entitlement is None or entitlement.status == "available"
        ):
            return
        disabled = entitlement is not None and entitlement.status in {
            "disabled",
            "locked",
        }
        raise HTTPException(
            status_code=403 if disabled else 402,
            detail="This add-on is turned off for this firm"
            if disabled
            else "This add-on is not active for this firm",
        )

    return require_access
