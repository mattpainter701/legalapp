"""Verification of one-use SaaS search identity tickets."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class IdentityTicketError(ValueError):
    pass


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise IdentityTicketError("identity ticket encoding is invalid") from exc


@dataclass(frozen=True)
class SearchAuthorization:
    tenant_id: str
    user_id: str
    source_ids: frozenset[str]
    principal_sids: frozenset[str]
    filters: dict
    nonce: str
    expires_at: int


class ReplayCache:
    """Small process-local negative cache for consumed ticket nonces."""

    def __init__(self, max_entries: int = 10_000):
        self.max_entries = max_entries
        self._used: dict[str, int] = {}

    def consume(self, nonce: str, expires_at: int, now: int) -> None:
        self._used = {
            key: expiry for key, expiry in self._used.items() if expiry >= now
        }
        if nonce in self._used:
            raise IdentityTicketError("identity ticket was replayed")
        if len(self._used) >= self.max_entries:
            oldest = min(self._used, key=self._used.get)
            self._used.pop(oldest, None)
        self._used[nonce] = expires_at


def verify_search_identity_ticket(
    ticket: str,
    *,
    public_key: str,
    audience: str,
    tenant_id: str,
    required_source_ids: set[str],
    replay_cache: ReplayCache,
    now: int | None = None,
) -> SearchAuthorization:
    current = int(time.time() if now is None else now)
    if len(ticket) > 262_144:
        raise IdentityTicketError("identity ticket is too large")
    try:
        encoded_header, encoded_payload, encoded_signature = ticket.split(".")
        header = json.loads(_decode(encoded_header))
        payload = json.loads(_decode(encoded_payload))
        key_bytes = _decode(public_key.strip())
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise IdentityTicketError("identity ticket is malformed") from exc
    if header != {"alg": "EdDSA", "kid": "firm-memory-v1", "typ": "JWT"}:
        raise IdentityTicketError("identity ticket header is not trusted")
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(
            _decode(encoded_signature),
            f"{encoded_header}.{encoded_payload}".encode("ascii"),
        )
    except (ValueError, InvalidSignature) as exc:
        raise IdentityTicketError("identity ticket signature is invalid") from exc
    if payload.get("v") != 1 or payload.get("iss") != "lawhand-saas":
        raise IdentityTicketError("identity ticket issuer is invalid")
    if (
        not audience
        or not tenant_id
        or payload.get("aud") != audience
        or payload.get("tenant_id") != tenant_id
        or not payload.get("sub")
    ):
        raise IdentityTicketError("identity ticket identity mismatch")
    try:
        issued_at, expires_at = int(payload["iat"]), int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityTicketError("identity ticket lifetime is invalid") from exc
    if (
        issued_at > current + 30
        or expires_at <= current
        or expires_at - issued_at > 300
    ):
        raise IdentityTicketError("identity ticket is expired")
    sources = frozenset(str(value) for value in payload.get("source_ids") or [])
    if not required_source_ids or not required_source_ids.issubset(sources):
        raise IdentityTicketError("identity ticket source scope mismatch")
    sids = frozenset(
        str(value).upper() for value in payload.get("principal_sids") or []
    )
    if (
        not sids
        or len(sids) > 4096
        or not all(value.startswith("S-") for value in sids)
    ):
        raise IdentityTicketError("identity ticket principal set is invalid")
    nonce = str(payload.get("nonce") or "")
    if not nonce:
        raise IdentityTicketError("identity ticket nonce is missing")
    replay_cache.consume(nonce, expires_at, current)
    return SearchAuthorization(
        tenant_id=tenant_id,
        user_id=str(payload.get("sub") or ""),
        source_ids=sources,
        principal_sids=sids,
        filters=payload.get("filters")
        if isinstance(payload.get("filters"), dict)
        else {},
        nonce=nonce,
        expires_at=expires_at,
    )
