"""Firm branding API (Task 1303) — firm name, logo, address, contact, PDF footer.

Branding is stored per-tenant on ``TenantSettings``. ``firm_name`` and
``firm_address`` fall back to ``Tenant.name`` / ``Tenant.address`` when the
tenant-specific override is unset, so a firm gets sensible defaults without
configuring anything.

The PUT also accepts ``tenant_name``, which renames the tenant record itself
rather than layering an override over it — the escape hatch for the name
sign-up derived from the account's email domain.
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
    "firm_currency",
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
    # Money appears on portal screens and invoice PDFs; keep an explicit code so
    # the renderer never falls back to a locale guess. Existing tenants default
    # to USD, which is what the old hardcode assumed.
    if not branding["firm_currency"]:
        branding["firm_currency"] = "USD"
    else:
        branding["firm_currency"] = branding["firm_currency"].strip().upper()

    return branding


async def _response(
    db: AsyncSession, branding: dict, tenant: Tenant
) -> FirmBrandingResponse:
    """Pair resolved branding with the tenant identity behind its fallbacks.

    ``firm_name_override`` is the stored value before the ``Tenant.name``
    fallback is applied. An editor needs it to tell "no override set" apart
    from "override happens to match the account name" — without it, merely
    opening the form and saving would silently freeze the fallback into place.
    """
    override = await db.scalar(
        select(TenantSettings.firm_name).where(TenantSettings.tenant_id == tenant.id)
    )
    return FirmBrandingResponse(
        **branding,
        firm_name_override=override,
        tenant_name=tenant.name,
        tenant_domain=tenant.domain,
    )


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

    return await _response(db, await get_firm_branding(db, tenant), tenant)


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
    # The tenant's own name is not a TenantSettings column — it lives on the
    # tenant record and is what every unset branding field falls back to.
    tenant_name = update_data.pop("tenant_name", None)
    if tenant_name:
        tenant.name = tenant_name
    for field, value in update_data.items():
        setattr(settings_row, field, value)

    await db.commit()
    await db.refresh(settings_row)
    await db.refresh(tenant)

    return await _response(db, await get_firm_branding(db, tenant), tenant)
