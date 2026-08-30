"""Fail-closed tenant/matter scope assertions for the private citator store.

The authority PostgreSQL service intentionally has no mirror of LawHand's
private matter tables.  Only this backend service may prove membership against
the canonical database before minting a short-lived assertion for a watch
write.  This is an internal integration contract; it is not a Research MCP
tool and does not grant public-corpus access to matter data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.plugin import Matter
from app.models.user import User

_SCOPE: Final = "citator:watch"
_MAX_TTL_SECONDS: Final = 300


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def build_citator_watch_scope_assertion(
    *,
    tenant_id: uuid.UUID | str,
    matter_id: uuid.UUID | str,
    principal: uuid.UUID | str,
    signer_secret: str,
    now: int | None = None,
) -> str:
    """Sign a five-minute, principal-bound assertion after canonical lookup."""
    if len(signer_secret) < 32:
        raise ValueError("MCP_CITATOR_SCOPE_ASSERTION_SECRET is not configured")
    issued = int(time.time()) if now is None else int(now)
    payload = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "matter_id": str(matter_id),
            "principal": str(principal),
            "scope": _SCOPE,
            "issued": issued,
            "expires": issued + _MAX_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(18),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(signer_secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


async def issue_citator_watch_scope_assertion(
    db: AsyncSession, *, current_user: User, matter_id: uuid.UUID | str
) -> str:
    """Prove canonical tenant ownership before minting a private MCP assertion."""
    try:
        canonical_matter_id = uuid.UUID(str(matter_id))
    except ValueError as exc:
        raise PermissionError("invalid matter identifier") from exc
    result = await db.execute(
        select(Matter.id).where(
            Matter.id == canonical_matter_id,
            Matter.tenant_id == current_user.tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise PermissionError("matter is not available in the current tenant")
    settings = get_settings()
    return build_citator_watch_scope_assertion(
        tenant_id=current_user.tenant_id,
        matter_id=canonical_matter_id,
        principal=current_user.id,
        signer_secret=settings.MCP_CITATOR_SCOPE_ASSERTION_SECRET,
    )
