"""Fixed catalog of grantable capabilities. NOT a DB table — roles store a
subset of these strings in roles.capabilities. Extend by adding here.

Provider-tier capability resolution is appended below the role catalog.
"""

from __future__ import annotations

from app.services.teams_gate import missing_teams_scopes

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
        "search_firm_memory",
        "manage_workflows",
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
LEGACY_ROLE_TO_SYSTEM_ROLE: dict[str, str] = {
    "admin": "Administrator",
    "accountant": "Accountant",
    "user": "User",
    "client": "Client",
}

WORKSPACE = "workspace"
PERSONAL = "personal"
AZURE_AD = "azure_ad"
CONSUMER = "consumer"
UNKNOWN = "unknown"

GOOGLE_DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
MS_DIRECTORY_SCOPE = "User.Read.All"
_DIRECTORY_TIERS = {WORKSPACE, AZURE_AD}
_LABELS = {
    ("google", WORKSPACE): "Google Workspace",
    ("google", PERSONAL): "Personal Google (Gmail)",
    ("microsoft", AZURE_AD): "Microsoft 365 (Work/School)",
    ("microsoft", CONSUMER): "Personal Microsoft Account",
}


def account_label(provider: str, account_type: str | None) -> str:
    if (provider, account_type) in _LABELS:
        return _LABELS[(provider, account_type)]
    return f"{'Google' if provider == 'google' else 'Microsoft'} (tier unknown)"


def effective_required_scopes(
    provider: str, required: list[str], account_type: str | None
) -> list[str]:
    """Remove directory-only consent from tiers that cannot use it."""
    if account_type not in {PERSONAL, CONSUMER}:
        return list(required)
    directory_scope = (
        GOOGLE_DIRECTORY_SCOPE if provider == "google" else MS_DIRECTORY_SCOPE
    )
    return [scope for scope in required if scope != directory_scope]


def _cap(available: bool, status: str, reason: str) -> dict:
    return {"available": available, "status": status, "reason": reason}


def _directory_sync(
    provider: str, tier: str | None, scopes: set[str], last: str | None
) -> dict:
    if tier in (None, UNKNOWN):
        return _cap(
            False,
            "unavailable",
            "Account tier not yet detected — reconnect to confirm directory access.",
        )
    if tier not in _DIRECTORY_TIERS:
        subject = (
            "personal Google accounts (Gmail)"
            if provider == "google"
            else "personal Microsoft accounts"
        )
        extras = (
            " Your Drive, Gmail, and Calendar still work."
            if provider == "google"
            else " Your OneDrive, mail, and Calendar still work."
        )
        return _cap(
            False,
            "unavailable",
            f"Directory sync isn't available on {subject}.{extras}",
        )
    required = GOOGLE_DIRECTORY_SCOPE if provider == "google" else MS_DIRECTORY_SCOPE
    if required not in scopes:
        return _cap(
            True,
            "needs_reauth",
            "Reconnect and grant directory read access to enable user sync.",
        )
    if last == "failed":
        return _cap(
            True,
            "error",
            "The last directory sync failed — see the error detail and reconnect if needed.",
        )
    return _cap(True, "ok", "Directory sync is available.")


def _teams(tier: str | None, scopes: str | None) -> dict:
    if tier in (None, UNKNOWN):
        return _cap(
            False,
            "unavailable",
            "Account tier not yet detected — reconnect to confirm Teams access.",
        )
    if tier != AZURE_AD:
        return _cap(
            False,
            "unavailable",
            "Microsoft Teams requires a Microsoft 365 Work/School account.",
        )
    if missing_teams_scopes(scopes):
        return _cap(
            True,
            "needs_reauth",
            "Reconnect with Teams enabled to grant the required Teams scopes.",
        )
    return _cap(True, "ok", "Teams integration is available.")


def resolve(
    provider: str,
    account_type: str | None,
    scopes: str | None,
    last_sync_status: str | None,
) -> dict:
    scope_set = {s for s in (scopes or "").split() if s}
    matrix = {
        "directory_sync": _directory_sync(
            provider, account_type, scope_set, last_sync_status
        ),
        "cloud_storage": _cap(True, "ok", "Cloud file storage is available."),
        "email": _cap(True, "ok", "Email access is available."),
        "calendar": _cap(True, "ok", "Calendar access is available."),
    }
    if provider == "microsoft":
        matrix["teams"] = _teams(account_type, scopes)
    return matrix
