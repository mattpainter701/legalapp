"""Fixed catalog of grantable capabilities. NOT a DB table — roles store a
subset of these strings in roles.capabilities. Extend by adding here."""

from __future__ import annotations

CAPABILITIES: frozenset[str] = frozenset(
    {
        "manage_users",
        "manage_roles",
        "manage_billing",
        "view_billing",
        "manage_matters",
        "manage_intake",
        "view_confidential_call_content",
        "manage_documents",
        "manage_integrations",
        "admin_settings",
        "approve_legal_work",
        "use_premium_ai",
    }
)


def is_valid_capability(cap: str) -> bool:
    return cap in CAPABILITIES


# Capability sets for the four seeded system roles.
SYSTEM_ROLE_CAPABILITIES: dict[str, list[str]] = {
    "Administrator": sorted(CAPABILITIES),
    "Accountant": ["view_billing", "manage_billing"],
    # Internal staff have access by default. Intake/reception roles should
    # explicitly omit this capability when the firm wants to restrict them.
    "User": [
        "manage_matters",
        "manage_intake",
        "manage_documents",
        "view_confidential_call_content",
    ],
    "Client": [],
}

# Maps the legacy user.role value to the seeded system role name.
LEGACY_ROLE_TO_SYSTEM_ROLE: dict[str, str] = {
    "admin": "Administrator",
    "accountant": "Accountant",
    "user": "User",
    "client": "Client",
}
