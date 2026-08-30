"""Unit coverage for the backend-only citator matter ownership contract."""

import base64
import hashlib
import hmac
import json
import uuid

from app.services.citator_scope import (
    build_citator_reviewer_authorization_assertion,
    build_citator_watch_scope_assertion,
)


def test_citator_scope_assertion_is_short_lived_and_principal_bound():
    tenant_id, matter_id, principal = (uuid.uuid4() for _ in range(3))
    secret = "c" * 48
    assertion = build_citator_watch_scope_assertion(
        tenant_id=tenant_id,
        matter_id=matter_id,
        principal=principal,
        authority_key="case:1",
        delivery_channels=["in_app"],
        quiet_hours={"timezone": "UTC"},
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
        "actor": str(principal),
        "purpose": "citator:watch:save",
        "tenant_id": str(tenant_id),
        "nonce": claims["nonce"],
        "body_sha256": claims["body_sha256"],
    }
    assert claims["nonce"]


def test_reviewer_authorization_command_is_short_lived_and_body_bound():
    assertion = build_citator_reviewer_authorization_assertion(
        actor="admin-id",
        credential="platform-jti",
        principal="attorney-id",
        authorization_basis="bar membership verified",
        signer_secret="o" * 48,
        now=1_700_000_000,
    )
    payload_part, _ = assertion.split(".")
    payload = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
    claims = json.loads(payload)
    assert claims["purpose"] == "citator:reviewer:authorize"
    assert claims["actor"] == "admin-id"
    assert claims["credential"] == "platform-jti"
    assert claims["expires"] - claims["issued"] == 60
    assert len(claims["body_sha256"]) == 64
