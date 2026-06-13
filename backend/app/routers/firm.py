"""Firm branding API (Task 1303) — firm name, logo, address, contact, PDF footer.

Branding is stored per-tenant on ``TenantSettings``. ``firm_name`` and
``firm_address`` fall back to ``Tenant.name`` / ``Tenant.address`` when the
tenant-specific override is unset, so a firm gets sensible defaults without
configuring anything.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.tenant import Tenant, TenantSettings
from app.schemas.firm import FirmBrandingResponse, FirmBrandingUpdate

router = APIRouter(prefix="/api/firm", tags=["firm branding"])

BRANDING_FIELDS = (
    "firm_name",
    "firm_logo_url",
    "firm_address",
    "firm_phone",
    "firm_email",
    "firm_website",
    "firm_pdf_footer",
)


async def get_firm_branding(db: AsyncSession, tenant: Tenant) -> dict:
    """Return the resolved branding dict for ``tenant``.

    Reads ``TenantSettings`` for the tenant (if it exists) and applies
    fallbacks: ``firm_name`` -> ``Tenant.name``, ``firm_address`` ->
    ``Tenant.address``. Other fields default to ``None`` when unset.
    """
    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )
    settings_row = result.scalar_one_or_none()

    branding = {field: None for field in BRANDING_FIELDS}
    if settings_row is not None:
        for field in BRANDING_FIELDS:
            branding[field] = getattr(settings_row, field)

    if not branding["firm_name"]:
        branding["firm_name"] = tenant.name
    if not branding["firm_address"]:
        branding["firm_address"] = tenant.address

    return branding


@router.get("/branding")
async def get_branding(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> FirmBrandingResponse:
    """Get the current tenant's firm branding (any authenticated user)."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = tenant_result.scalar_one()

    branding = await get_firm_branding(db, tenant)
    return FirmBrandingResponse(**branding)


@router.put("/branding")
async def update_branding(
    body: FirmBrandingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> FirmBrandingResponse:
    """Update the current tenant's firm branding (admin only)."""
    user = await require_admin(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = tenant_result.scalar_one()

    settings_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    settings_row = settings_result.scalar_one_or_none()
    if settings_row is None:
        settings_row = TenantSettings(tenant_id=user.tenant_id)
        db.add(settings_row)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings_row, field, value)

    await db.commit()
    await db.refresh(settings_row)

    branding = await get_firm_branding(db, tenant)
    return FirmBrandingResponse(**branding)
