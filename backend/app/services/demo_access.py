"""Demo-specific product restrictions shared by AI entry points."""

from fastapi import HTTPException


def reject_demo_premium(user, requested: bool) -> None:
    tenant = getattr(user, "tenant", None)
    if requested and tenant is not None and tenant.billing_tier == "demo":
        raise HTTPException(
            status_code=403,
            detail="Premium AI is not available in demo workspaces",
        )
