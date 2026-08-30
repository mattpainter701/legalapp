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

_MAX_TTL_SECONDS: Final = 300
_REVIEWER_AUTH_TTL_SECONDS: Final = 60


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _command_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_citator_watch_scope_assertion(
    *,
    tenant_id: uuid.UUID | str,
    matter_id: uuid.UUID | str,
    principal: uuid.UUID | str,
    authority_key: str,
    delivery_channels: list[str],
    quiet_hours: dict[str, object] | None,
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
            "actor": str(principal),
            "purpose": "citator:watch:save",
            "issued": issued,
            "expires": issued + _MAX_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(18),
            "body_sha256": _command_hash(
                {
                    "action": "save",
                    "tenant_id": str(tenant_id),
                    "matter_id": str(matter_id),
                    "principal": str(principal),
                    "authority_key": authority_key,
                    "delivery_channels": delivery_channels,
                    "quiet_hours": quiet_hours or {},
                }
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(signer_secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


async def issue_citator_watch_scope_assertion(
    db: AsyncSession,
    *,
    current_user: User,
    matter_id: uuid.UUID | str,
    authority_key: str,
    delivery_channels: list[str],
    quiet_hours: dict[str, object] | None = None,
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
        authority_key=authority_key,
        delivery_channels=delivery_channels,
        quiet_hours=quiet_hours,
        signer_secret=settings.MCP_CITATOR_SCOPE_ASSERTION_SECRET,
    )


def build_citator_reviewer_authorization_assertion(
    *,
    actor: uuid.UUID | str,
    credential: str,
    principal: str,
    authorization_basis: str,
    signer_secret: str,
    now: int | None = None,
) -> str:
    """Build a single-use, platform-authenticated reviewer registration command."""
    if len(signer_secret) < 32:
        raise ValueError("MCP_OPERATOR_ASSERTION_SECRET is not configured")
    issued = int(time.time()) if now is None else int(now)
    body = {
        "action": "authorize_reviewer",
        "principal": principal.strip(),
        "authorization_basis": authorization_basis.strip()[:1000],
    }
    payload = json.dumps(
        {
            "actor": str(actor),
            "credential": credential,
            "purpose": "citator:reviewer:authorize",
            "issued": issued,
            "expires": issued + _REVIEWER_AUTH_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(18),
            "body_sha256": _command_hash(body),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(signer_secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def issue_citator_reviewer_authorization_assertion(
    *, current_user: User, credential: str, principal: str, authorization_basis: str
) -> str:
    """Allow only an authenticated LawHand administrator to authorize reviewers."""
    if current_user.role != "admin" or not current_user.is_active:
        raise PermissionError(
            "administrator authorization is required for citator reviewers"
        )
    settings = get_settings()
    return build_citator_reviewer_authorization_assertion(
        actor=current_user.id,
        credential=credential,
        principal=principal,
        authorization_basis=authorization_basis,
        signer_secret=settings.MCP_OPERATOR_ASSERTION_SECRET,
    )
