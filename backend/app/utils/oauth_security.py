"""Shared OAuth security helpers: PKCE challenge generation and ID-token verification."""

import base64
import hashlib
import json
import logging
import secrets
import uuid
from typing import Optional

import httpx
from fastapi import HTTPException
from jose import jwk, jwt

logger = logging.getLogger(__name__)

_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_MICROSOFT_JWKS_URL_TMPL = (
    "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
)
_HTTP_TIMEOUT = 15.0


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE with S256."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def generate_nonce() -> str:
    return secrets.token_urlsafe(24)


def is_oauth_client_configured(client_id: str, client_secret: str) -> bool:
    """Reject empty, whitespace-only, or obviously bogus placeholder values."""
    cid = (client_id or "").strip()
    cs = (client_secret or "").strip()
    if not cid or not cs:
        return False
    if cid.startswith("#") or "TODO" in cid.upper():
        return False
    return True


def _decode_jwt_segment(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


async def _fetch_jwks(url: str) -> dict:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502, detail="Failed to fetch OAuth provider JWKS"
            )
        return response.json()


def _find_jwk(jwks: dict, kid: Optional[str]) -> Optional[dict]:
    for key_data in jwks.get("keys", []):
        if key_data.get("kid") == kid:
            return key_data
    return None


async def verify_google_id_token(
    id_token: str,
    client_id: str,
    expected_nonce: Optional[str] = None,
    access_token: Optional[str] = None,
) -> dict:
    """Verify a Google id_token's RS256 signature, audience, and nonce; return claims."""
    jwks = await _fetch_jwks(_GOOGLE_JWKS_URL)

    header = _decode_jwt_segment(id_token.split(".")[0])
    matching_key = _find_jwk(jwks, header.get("kid"))
    if matching_key is None:
        raise HTTPException(
            status_code=400, detail="No matching Google public key for token kid"
        )

    try:
        public_key = jwk.construct(matching_key)
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=["https://accounts.google.com", "accounts.google.com"],
            access_token=access_token,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Google id_token verification failed: {exc}"
        )

    _check_nonce(claims, expected_nonce, provider="Google")
    return claims


async def verify_microsoft_id_token(
    id_token: str,
    client_id: str,
    tenant: str,
    expected_nonce: Optional[str] = None,
) -> dict:
    """Verify a Microsoft id_token's RS256 signature, audience, issuer, and nonce; return claims."""
    jwks_tenant = (
        tenant
        if tenant and tenant not in ("common", "organizations", "consumers")
        else "common"
    )
    jwks = await _fetch_jwks(_MICROSOFT_JWKS_URL_TMPL.format(tenant=jwks_tenant))

    header = _decode_jwt_segment(id_token.split(".")[0])
    matching_key = _find_jwk(jwks, header.get("kid"))
    if matching_key is None:
        raise HTTPException(
            status_code=400, detail="No matching Microsoft public key for token kid"
        )

    try:
        public_key = jwk.construct(matching_key, algorithm="RS256")
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=client_id,
            options={"verify_iss": False},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Microsoft id_token verification failed: {exc}"
        )

    # Microsoft's issuer is tenant-specific (https://login.microsoftonline.com/{tid}/v2.0)
    # even for "common"/multi-tenant apps, so we can't check it against a fixed string.
    # Instead verify iss and tid agree, which rules out a token whose claims were
    # tampered to swap directories after signature verification.
    iss = claims.get("iss", "")
    tid = claims.get("tid", "")
    if not iss.startswith("https://login.microsoftonline.com/") or (
        tid and tid not in iss
    ):
        raise HTTPException(
            status_code=400, detail="Microsoft id_token issuer/tenant mismatch"
        )

    _check_nonce(claims, expected_nonce, provider="Microsoft")
    return claims


async def verify_microsoft_access_token(
    access_token: str,
    *,
    audience: str,
    required_scope: str,
    client_id: str,
    tenant: str = "common",
) -> dict:
    """Verify an Entra v2 delegated API token used by Office NAA.

    Signature, exact audience, tenant-specific issuer, immutable directory IDs,
    delegated scope, and authorized client are all required before the token is
    allowed to establish a Clarity cookie session.
    """

    if not audience or not required_scope or not client_id:
        raise HTTPException(
            status_code=503, detail="Office Entra API is not configured"
        )

    try:
        segments = access_token.split(".")
        header = _decode_jwt_segment(segments[0])
        unverified = _decode_jwt_segment(segments[1])
        tid = str(uuid.UUID(str(unverified.get("tid", ""))))
        oid = str(uuid.UUID(str(unverified.get("oid", ""))))
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail="Invalid Microsoft access token"
        ) from exc

    if tenant not in ("", "common", "organizations") and tid != tenant:
        raise HTTPException(status_code=401, detail="Microsoft tenant is not allowed")

    jwks = await _fetch_jwks(_MICROSOFT_JWKS_URL_TMPL.format(tenant=tid))
    matching_key = _find_jwk(jwks, header.get("kid"))
    if matching_key is None:
        raise HTTPException(
            status_code=401, detail="No matching Microsoft public key for token kid"
        )

    issuer = f"https://login.microsoftonline.com/{tid}/v2.0"
    try:
        public_key = jwk.construct(matching_key, algorithm="RS256")
        claims = jwt.decode(
            access_token,
            public_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail="Microsoft access token verification failed"
        ) from exc

    scopes = set(str(claims.get("scp", "")).split())
    if required_scope not in scopes:
        raise HTTPException(status_code=403, detail="Office API scope is missing")
    if claims.get("azp") != client_id:
        raise HTTPException(status_code=401, detail="Office client is not authorized")
    if claims.get("tid") != tid or claims.get("oid") != oid:
        raise HTTPException(status_code=401, detail="Microsoft identity claims changed")
    return claims


def _check_nonce(claims: dict, expected_nonce: Optional[str], provider: str) -> None:
    if expected_nonce is None:
        return
    if claims.get("nonce") != expected_nonce:
        logger.warning("%s id_token nonce mismatch", provider)
        raise HTTPException(
            status_code=400, detail=f"{provider} id_token nonce mismatch"
        )
