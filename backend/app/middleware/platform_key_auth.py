"""Resolve minted operator API keys before a platform route runs.

Route handlers authorise synchronously, so the database lookup a minted key
needs cannot happen inside them. This middleware performs it once per request
and leaves the result on ``request.state.platform_principal``; handlers then
treat a minted key and a bootstrap-exchanged session identically.

Fail-closed by construction: anything other than a live, unexpired, unrevoked
key leaves no principal behind, and the handler falls through to session-token
verification, which rejects a key-shaped bearer value.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import async_session_maker
from app.models.platform_api_key import PlatformApiKey
from app.services.platform_auth import (
    PlatformPrincipal,
    hash_platform_api_key,
    looks_like_platform_api_key,
)

logger = logging.getLogger(__name__)

PLATFORM_PATH_PREFIX = "/api/platform"

# A write per request would make every operator call a read-modify-write on the
# same row. Last-used is a staleness signal for revocation decisions, not an
# audit record — the audit trail is operator_audit_logs — so a coarse value is
# the right trade.
LAST_USED_WRITE_INTERVAL = timedelta(minutes=1)


class PlatformKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not request.url.path.startswith(PLATFORM_PATH_PREFIX):
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            presented = authorization.split(" ", 1)[1]
            if looks_like_platform_api_key(presented):
                principal = await _resolve_key(presented)
                if principal is not None:
                    request.state.platform_principal = principal
                    # Stamped here rather than in the handler so the audit
                    # trail names the caller even when the request is about to
                    # be refused for insufficient scope.
                    request.state.platform_actor_id = principal.actor_id
                    request.state.platform_token_jti = principal.credential_id
                    request.state.platform_credential_type = principal.credential_type

        return await call_next(request)


async def _resolve_key(plaintext: str) -> PlatformPrincipal | None:
    """Return the principal for a live key, or None for anything else."""

    try:
        key_hash = hash_platform_api_key(plaintext)
        now = datetime.now(timezone.utc)

        async with async_session_maker() as db:
            row = (
                await db.execute(
                    select(PlatformApiKey).where(PlatformApiKey.key_hash == key_hash)
                )
            ).scalar_one_or_none()

            if row is None or not row.is_usable(now):
                return None

            principal = PlatformPrincipal(
                actor_id=row.created_by or row.label,
                scopes=frozenset(row.scopes or []),
                credential_type="minted_key",
                credential_id=str(row.id),
            )

            if (
                row.last_used_at is None
                or now - row.last_used_at >= LAST_USED_WRITE_INTERVAL
            ):
                await db.execute(
                    update(PlatformApiKey)
                    .where(PlatformApiKey.id == row.id)
                    .values(last_used_at=now)
                )
                await db.commit()

            return principal
    except Exception:
        # An unreachable database must deny the request, never admit it.
        logger.exception("Unable to resolve platform operator key")
        return None
