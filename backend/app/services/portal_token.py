"""Portal-scoped JWT helpers for external mediation party access.

Opposing parties never get a ``User`` row; instead the invite-accept endpoint
issues a short-lived JWT carrying ``portal: true`` plus the case/party scope.
These reuse the same ``SECRET_KEY``/``ALGORITHM`` but use a dedicated mediation
cookie so opening a portal never replaces a staff session.
"""

import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.config import get_settings

settings = get_settings()

# Portal magic-link sessions are short-lived.
PORTAL_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


def create_portal_token(
    *,
    tenant_id: str,
    case_id: str,
    party_id: str,
    party_role: str,
    invite_id: str,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "portal": True,
        "tenant_id": str(tenant_id),
        "case_id": str(case_id),
        "party_id": str(party_id),
        "party_role": party_role,
        "invite_id": str(invite_id),
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=PORTAL_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_matter_portal_token(
    *,
    tenant_id: str,
    matter_id: str,
    contact_id: str | None,
    email: str | None,
    invite_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Short-lived, matter-scoped JWT for a firm client accessing the portal.

    Carries ``client_portal: true`` plus the matter/contact scope. Reuses the
    same ``SECRET_KEY``/cookie as firm logins, so ``TenantMiddleware`` picks up
    the tenant_id automatically; ``get_client_portal_context`` enforces scope.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "client_portal": True,
        "tenant_id": str(tenant_id),
        "matter_id": str(matter_id),
        "contact_id": str(contact_id) if contact_id else None,
        "email": email,
        "invite_id": str(invite_id) if invite_id else None,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=PORTAL_TOKEN_EXPIRE_MINUTES),
    }
    if user_id:  # pragma: no cover - durable portal integration
        payload["sub"] = str(user_id)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_user_token(*, user_id: str, tenant_id: str, role: str, email: str) -> str:
    """Standard login JWT for a firm client (role="client").

    Matches the claims ``app.routers.auth._create_access_token`` produces so it
    flows through ``TenantMiddleware`` / ``get_current_user`` unchanged.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "email": email,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
