"""Short-lived, signed identity envelopes for customer-node search work.

The browser never supplies this envelope.  It is minted only after the SaaS
has authenticated the user and resolved the user's immutable native identity.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class SearchIdentityTicketError(ValueError):
    """A ticket cannot be minted safely."""


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _raw_key(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except Exception as exc:
        raise SearchIdentityTicketError(
            "identity ticket signing key is invalid"
        ) from exc
    if len(raw) != 32:
        raise SearchIdentityTicketError("identity ticket signing key must be 32 bytes")
    return raw


@dataclass(frozen=True)
class SearchIdentity:
    tenant_id: str
    user_id: str
    source_ids: tuple[str, ...]
    principal_sids: tuple[str, ...]
    identity_version: int


def mint_search_identity_ticket(
    identity: SearchIdentity,
    *,
    private_key: str,
    audience: str,
    filters: dict | None = None,
    ttl_seconds: int = 60,
    now: int | None = None,
) -> str:
    """Return a compact Ed25519 ticket bound to one user and source set."""
    if (
        not identity.tenant_id
        or not identity.user_id
        or not audience
        or not identity.source_ids
        or not identity.principal_sids
    ):
        raise SearchIdentityTicketError("identity ticket scope is empty")
    if len(set(identity.source_ids)) > 256 or len(set(identity.principal_sids)) > 4096:
        raise SearchIdentityTicketError("identity ticket scope is too large")
    issued_at = int(time.time() if now is None else now)
    ttl = max(5, min(int(ttl_seconds), 300))
    header = {"alg": "EdDSA", "kid": "firm-memory-v1", "typ": "JWT"}
    payload = {
        "v": 1,
        "iss": "lawhand-saas",
        "aud": audience,
        "sub": identity.user_id,
        "tenant_id": identity.tenant_id,
        "source_ids": sorted(set(identity.source_ids)),
        "principal_sids": sorted({sid.upper() for sid in identity.principal_sids}),
        "identity_version": identity.identity_version,
        "filters": filters or {},
        "nonce": secrets.token_urlsafe(18),
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    encoded_header = _b64(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    )
    encoded_payload = _b64(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = Ed25519PrivateKey.from_private_bytes(_raw_key(private_key)).sign(signed)
    return f"{signed.decode('ascii')}.{_b64(signature)}"
