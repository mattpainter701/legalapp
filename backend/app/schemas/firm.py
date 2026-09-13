"""Pydantic schemas for firm branding (Task 1303)."""

from typing import Optional

from pydantic import BaseModel, ValidationInfo, field_validator


# Column widths on ``tenant_settings`` / ``tenants``. Without these a long
# value reaches the database and fails there as a 500 instead of a 422 naming
# the field. Text columns (address, pdf footer) are unbounded and absent here.
_MAX_LENGTHS = {
    "firm_name": 300,
    "firm_logo_url": 1000,
    "firm_phone": 50,
    "firm_email": 320,
    "firm_website": 300,
    "tenant_name": 255,
}


def _clean(value: Optional[str]) -> Optional[str]:
    """Trim a free-text field, collapsing a blank string to ``None``.

    An admin who clears a field wants the fallback back, not an empty string
    rendered onto an invoice.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _check_length(value: Optional[str], field: Optional[str]) -> Optional[str]:
    """Reject a value wider than the column that has to store it."""
    limit = _MAX_LENGTHS.get(field or "")
    if value is not None and limit is not None and len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer")
    return value


class FirmBrandingResponse(BaseModel):
    """Resolved firm branding for the current tenant.

    ``firm_name`` and ``firm_address`` fall back to ``Tenant.name`` /
    ``Tenant.address`` when the tenant-specific override is unset.
    """

    firm_name: Optional[str] = None
    firm_logo_url: Optional[str] = None
    firm_address: Optional[str] = None
    firm_phone: Optional[str] = None
    firm_email: Optional[str] = None
    firm_website: Optional[str] = None
    # ISO 4217 code. Resolved to "USD" when unset.
    firm_currency: Optional[str] = None
    firm_pdf_footer: Optional[str] = None
    # The stored ``firm_name`` before the ``Tenant.name`` fallback — ``None``
    # when the firm has set no override.
    firm_name_override: Optional[str] = None
    # Tenant identity. ``tenant_name`` is the account's own name — the one
    # derived from the sign-up email domain — and is what ``firm_name`` falls
    # back to. ``tenant_domain`` is the immutable tenant key, shown for
    # reference only.
    tenant_name: Optional[str] = None
    tenant_domain: Optional[str] = None


class FirmBrandingUpdate(BaseModel):
    """Partial update for firm branding. All fields optional."""

    firm_name: Optional[str] = None
    firm_logo_url: Optional[str] = None
    firm_address: Optional[str] = None
    firm_phone: Optional[str] = None
    firm_email: Optional[str] = None
    firm_website: Optional[str] = None
    firm_currency: Optional[str] = None
    firm_pdf_footer: Optional[str] = None
    # Renames the tenant itself. Unlike the fields above this is not an
    # override — it replaces the domain-derived name on the account record.
    tenant_name: Optional[str] = None

    @field_validator(
        "firm_name",
        "firm_logo_url",
        "firm_address",
        "firm_phone",
        "firm_email",
        "firm_website",
        "firm_pdf_footer",
        mode="after",
    )
    @classmethod
    def _blank_to_none(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        return _check_length(_clean(value), info.field_name)

    @field_validator("firm_currency", mode="after")
    @classmethod
    def _normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        cleaned = _clean(value)
        if cleaned is None:
            return None
        code = cleaned.upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("firm_currency must be a 3-letter ISO 4217 code")
        return code

    @field_validator("tenant_name", mode="after")
    @classmethod
    def _require_tenant_name(cls, value: Optional[str]) -> Optional[str]:
        cleaned = _clean(value)
        if value is not None and cleaned is None:
            # The tenant name backs every fallback; there is no sensible
            # "unset" for it, so refuse rather than silently ignoring.
            raise ValueError("tenant_name cannot be blank")
        return _check_length(cleaned, "tenant_name")
