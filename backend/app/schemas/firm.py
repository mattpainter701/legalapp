"""Pydantic schemas for firm branding (Task 1303)."""

from typing import Optional

from pydantic import BaseModel


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
    firm_pdf_footer: Optional[str] = None


class FirmBrandingUpdate(BaseModel):
    """Partial update for firm branding. All fields optional."""

    firm_name: Optional[str] = None
    firm_logo_url: Optional[str] = None
    firm_address: Optional[str] = None
    firm_phone: Optional[str] = None
    firm_email: Optional[str] = None
    firm_website: Optional[str] = None
    firm_pdf_footer: Optional[str] = None
