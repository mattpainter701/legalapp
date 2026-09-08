"""Resolve explicit template references to the current firm's shared profile.

Sample text and field-name guesses never become authority for firm details.
The existing branding profile remains the single source of truth.
"""

from sqlalchemy import select

from app.models.tenant import Tenant
from app.routers.firm import get_firm_branding
from app.schemas.document_template import DocumentTemplateVariableSuggestion
from app.services.template_bindings import binding_label


FIRM_FIELDS = {
    "firm.name": "firm_name",
    "firm.address": "firm_address",
    "firm.phone": "firm_phone",
    "firm.email": "firm_email",
    "firm.website": "firm_website",
}


async def suggestions(db, tenant_id, bindings):
    requested = {name: path for name, path in bindings.items() if path in FIRM_FIELDS}
    if not requested:
        return {}
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    branding = await get_firm_branding(db, tenant) if tenant is not None else {}
    resolved = {}
    for name, path in requested.items():
        field = FIRM_FIELDS[path]
        raw = branding.get(field)
        value = raw.strip() if isinstance(raw, str) and raw.strip() else None
        resolved[name] = DocumentTemplateVariableSuggestion(
            variable=name,
            suggested_value=value,
            source_type="firm_profile",
            source_field=field,
            provenance={
                "source_type": "firm_profile",
                "record_id": str(tenant_id),
                "binding": path,
                "binding_label": binding_label(path),
                "status": "configured" if value is not None else "firm_profile_missing",
            },
            confidence=1.0 if value is not None else 0.0,
            review_required=value is None,
        )
    return resolved
