"""Short-lived, scoped authentication for the platform operator API."""

import hmac
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from jose import JWTError, jwt

from app.config import get_settings


settings = get_settings()
PLATFORM_TOKEN_AUDIENCE = "clarity-platform-api"
PLATFORM_TOKEN_ISSUER = "clarity-legal"
# ``platform:debug`` unlocks tenant troubleshooting data that can echo customer
# content — stack traces, retained query text, client IPs. It is never implied
# by ``platform:read``; a credential must be granted it explicitly.
PLATFORM_SCOPES = {
    "platform:read",
    "platform:write",
    "platform:debug",
    "platform:llm:read",
    "platform:llm:write",
}

# Minted operator keys are recognisable on sight so a leaked one can be
# identified in a log or a paste without being tried against the API.
PLATFORM_API_KEY_PREFIX = "lhpk_"
PLATFORM_API_KEY_BYTES = 32
# Enough of the plaintext to identify a key in the console, far too little to
# brute-force the remainder.
PLATFORM_API_KEY_DISPLAY_CHARS = 12


@dataclass(frozen=True)
class PlatformBootstrapPrincipal:
    operator_id: str
    scopes: list[str]
    expires_at: datetime
    credential_type: str = "hashed_bootstrap"


@dataclass(frozen=True)
class PlatformPrincipal:
    """A caller that has already proven itself, whatever credential it used."""

    actor_id: str
    scopes: frozenset[str]
    credential_type: str
    credential_id: str | None = None

    @property
    def is_bootstrap_session(self) -> bool:
        """True only for tokens exchanged from the offline bootstrap secret.

        Key management is restricted to these so that a leaked minted key
        cannot mint its own long-lived replacements.
        """
        return self.credential_type == "bootstrap_session"


def _expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Invalid platform bootstrap configuration"
        ) from exc
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=503, detail="Invalid platform bootstrap configuration"
        )
    return parsed.astimezone(timezone.utc)


def issue_platform_token(
    *,
    subject: str,
    scopes: list[str] | None = None,
    allowed_scopes: list[str] | None = None,
    ttl_minutes: int | None = None,
    not_after: datetime | None = None,
) -> tuple[str, datetime, list[str]]:
    maximum = set(allowed_scopes or [])
    requested = sorted(set(scopes) if scopes is not None else maximum)
    unknown = set(requested) - PLATFORM_SCOPES
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown platform scope(s): {sorted(unknown)}",
        )
    if not requested or set(requested) - maximum:
        raise HTTPException(
            status_code=403, detail="Requested platform scopes exceed credential grant"
        )
    ttl = ttl_minutes or settings.PLATFORM_TOKEN_TTL_MINUTES
    if ttl < 1 or ttl > settings.PLATFORM_TOKEN_MAX_TTL_MINUTES:
        raise HTTPException(status_code=400, detail="Invalid platform token lifetime")
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ttl)
    if not_after is not None:
        expires_at = min(expires_at, not_after.astimezone(timezone.utc))
    if expires_at <= now:
        raise HTTPException(status_code=403, detail="Platform credential has expired")
    payload = {
        "iss": PLATFORM_TOKEN_ISSUER,
        "aud": PLATFORM_TOKEN_AUDIENCE,
        "sub": (subject or "operator")[:120],
        "type": "platform_operator",
        # Distinguishes a bootstrap-exchanged session from a minted key, so
        # key-management routes can insist on the former.
        "cred": "bootstrap_session",
        "scope": requested,
        "jti": secrets.token_urlsafe(18),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.PLATFORM_TOKEN_SIGNING_KEY, algorithm="HS256")
    return token, expires_at, requested


def decode_platform_token(token: str) -> dict[str, Any]:
    if not settings.PLATFORM_TOKEN_SIGNING_KEY:
        raise HTTPException(status_code=503, detail="Platform access is disabled")
    try:
        claims = jwt.decode(
            token,
            settings.PLATFORM_TOKEN_SIGNING_KEY,
            algorithms=["HS256"],
            audience=PLATFORM_TOKEN_AUDIENCE,
            issuer=PLATFORM_TOKEN_ISSUER,
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=403, detail="Invalid or expired platform token"
        ) from exc
    if claims.get("type") != "platform_operator" or not claims.get("jti"):
        raise HTTPException(status_code=403, detail="Invalid platform token")
    return claims


def required_scope(request: Request) -> str:
    write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if request.url.path.startswith("/api/platform/llm"):
        return "platform:llm:write" if write else "platform:llm:read"
    return "platform:write" if write else "platform:read"


def require_platform_token(
    request: Request, *, scopes: set[str] | None = None
) -> PlatformPrincipal:
    """Authorise a platform request and return the caller.

    ``scopes`` states the requirement explicitly and overrides the path-based
    default. Routes handling troubleshooting data pass it rather than relying
    on :func:`required_scope` inferring intent from a URL — an inference that
    would fail *open* the moment a path stopped matching.
    """

    principal: PlatformPrincipal | None = getattr(
        request.state, "platform_principal", None
    )
    if principal is None:
        # No minted key was resolved upstream, so this must be a session token.
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=403, detail="Missing platform bearer token")
        claims = decode_platform_token(authorization.split(" ", 1)[1])
        principal = PlatformPrincipal(
            actor_id=str(claims.get("sub") or "operator"),
            scopes=frozenset(claims.get("scope") or []),
            credential_type=str(claims.get("cred") or "bootstrap_session"),
            credential_id=claims.get("jti"),
        )

    needed = set(scopes) if scopes else {required_scope(request)}
    if not needed.issubset(principal.scopes):
        raise HTTPException(status_code=403, detail="Platform token scope denied")

    request.state.platform_principal = principal
    request.state.platform_actor_id = principal.actor_id
    request.state.platform_token_jti = principal.credential_id
    request.state.platform_credential_type = principal.credential_type
    request.state.platform_scope = " ".join(sorted(needed))
    return principal


def require_bootstrap_session(
    request: Request, *, scopes: set[str] | None = None
) -> PlatformPrincipal:
    """Authorise a request that only the offline bootstrap secret may make.

    Minting and revoking operator keys stays anchored to the credential held
    outside the database, so compromising a minted key cannot be parlayed into
    minting fresh ones.
    """

    principal = require_platform_token(request, scopes=scopes)
    if not principal.is_bootstrap_session:
        raise HTTPException(
            status_code=403,
            detail=(
                "Operator key management requires a session issued from the "
                "platform bootstrap credential"
            ),
        )
    return principal


# ── Minted operator keys ──────────────────────────────────────────────────────


def generate_platform_api_key() -> tuple[str, str, str]:
    """Return ``(plaintext, prefix_for_display, sha256_hash)`` for a new key."""

    plaintext = PLATFORM_API_KEY_PREFIX + secrets.token_urlsafe(PLATFORM_API_KEY_BYTES)
    return (
        plaintext,
        plaintext[:PLATFORM_API_KEY_DISPLAY_CHARS],
        hash_platform_api_key(plaintext),
    )


def hash_platform_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def looks_like_platform_api_key(value: str) -> bool:
    return value.startswith(PLATFORM_API_KEY_PREFIX)


def validate_requested_key_scopes(
    requested: list[str] | None, *, granted_by: PlatformPrincipal
) -> list[str]:
    """A minted key may never carry scopes its minter does not itself hold."""

    wanted = sorted(set(requested or []))
    if not wanted:
        raise HTTPException(
            status_code=400, detail="At least one platform scope is required"
        )
    unknown = set(wanted) - PLATFORM_SCOPES
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown platform scope(s): {sorted(unknown)}"
        )
    if not set(wanted).issubset(granted_by.scopes):
        raise HTTPException(
            status_code=403,
            detail="Requested key scopes exceed the minting credential's own grant",
        )
    return wanted


def verify_platform_bootstrap_key(supplied: str) -> PlatformBootstrapPrincipal:
    if len(supplied) < 32:
        raise HTTPException(status_code=403, detail="Invalid platform bootstrap key")
    supplied_hash = hashlib.sha256(supplied.encode()).hexdigest()
    try:
        entries = json.loads(settings.PLATFORM_BOOTSTRAP_CREDENTIALS_JSON or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=503, detail="Invalid platform bootstrap configuration"
        ) from exc
    now = datetime.now(timezone.utc)
    for entry in entries:
        expected_hash = str(entry.get("key_hash") or "")
        if expected_hash and hmac.compare_digest(supplied_hash, expected_hash):
            expires_at = _expiry(str(entry.get("expires_at") or ""))
            if expires_at <= now:
                raise HTTPException(
                    status_code=403, detail="Platform bootstrap credential expired"
                )
            return PlatformBootstrapPrincipal(
                operator_id=str(entry["operator_id"]),
                scopes=sorted(set(entry["scopes"])),
                expires_at=expires_at,
            )

    if settings.PLATFORM_LEGACY_BOOTSTRAP_ENABLED:
        secret = settings.PLATFORM_SECRET_KEY
        if secret and hmac.compare_digest(supplied, secret):
            expires_at = _expiry(settings.PLATFORM_LEGACY_BOOTSTRAP_EXPIRES_AT)
            if expires_at <= now:
                raise HTTPException(
                    status_code=403, detail="Platform bootstrap credential expired"
                )
            scopes = [
                item.strip()
                for item in settings.PLATFORM_LEGACY_BOOTSTRAP_MAX_SCOPES.split(",")
                if item.strip()
            ]
            if not scopes or set(scopes) - PLATFORM_SCOPES:
                raise HTTPException(
                    status_code=503,
                    detail="Invalid legacy platform scope configuration",
                )
            return PlatformBootstrapPrincipal(
                operator_id=settings.PLATFORM_LEGACY_BOOTSTRAP_OPERATOR_ID,
                scopes=scopes,
                expires_at=expires_at,
                credential_type="legacy_bridge",
            )
    raise HTTPException(status_code=403, detail="Invalid platform bootstrap key")
