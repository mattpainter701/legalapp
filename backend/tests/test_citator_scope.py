"""Unit coverage for the backend-only citator matter ownership contract."""

import base64
import hashlib
import hmac
import json
import uuid

from app.services.citator_scope import build_citator_watch_scope_assertion


def test_citator_scope_assertion_is_short_lived_and_principal_bound():
    tenant_id, matter_id, principal = (uuid.uuid4() for _ in range(3))
    secret = "c" * 48
    assertion = build_citator_watch_scope_assertion(
        tenant_id=tenant_id,
        matter_id=matter_id,
        principal=principal,
        signer_secret=secret,
        now=1_700_000_000,
    )
    payload_part, signature_part = assertion.split(".")

    def decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    payload, signature = decode(payload_part), decode(signature_part)
    assert hmac.compare_digest(
        signature, hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    )
    claims = json.loads(payload)
    assert claims == {
        "expires": 1_700_000_300,
        "issued": 1_700_000_000,
        "matter_id": str(matter_id),
        "principal": str(principal),
        "scope": "citator:watch",
        "tenant_id": str(tenant_id),
        "nonce": claims["nonce"],
    }
    assert claims["nonce"]
