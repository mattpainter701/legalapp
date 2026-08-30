"""Unit coverage for the backend-only citator matter ownership contract."""

import base64
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import citator_scope
from app.services.citator_scope import (
    build_citator_reviewer_authorization_assertion,
    build_citator_watch_scope_assertion,
    issue_citator_reviewer_authorization_assertion,
    issue_citator_watch_scope_assertion,
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


def test_citator_assertion_builders_reject_unconfigured_signers():
    with pytest.raises(ValueError, match="MCP_CITATOR_SCOPE_ASSERTION_SECRET"):
        build_citator_watch_scope_assertion(
            tenant_id="tenant", matter_id="matter", principal="principal",
            authority_key="case:1", delivery_channels=["in_app"], quiet_hours=None,
            signer_secret="too-short",
        )
    with pytest.raises(ValueError, match="MCP_OPERATOR_ASSERTION_SECRET"):
        build_citator_reviewer_authorization_assertion(
            actor="admin", credential="credential", principal="reviewer",
            authorization_basis="verified", signer_secret="too-short",
        )


@pytest.mark.asyncio
async def test_issue_watch_assertion_requires_owned_canonical_matter(monkeypatch):
    tenant_id, matter_id, user_id = (uuid.uuid4() for _ in range(3))
    user = SimpleNamespace(tenant_id=tenant_id, id=user_id)
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(PermissionError, match="invalid matter"):
        await issue_citator_watch_scope_assertion(
            db, current_user=user, matter_id="not-a-uuid", authority_key="case:1",
            delivery_channels=["in_app"],
        )
    db.execute.assert_not_awaited()

    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with pytest.raises(PermissionError, match="not available"):
        await issue_citator_watch_scope_assertion(
            db, current_user=user, matter_id=matter_id, authority_key="case:1",
            delivery_channels=["in_app"],
        )

    monkeypatch.setattr(
        citator_scope, "get_settings",
        lambda: SimpleNamespace(MCP_CITATOR_SCOPE_ASSERTION_SECRET="c" * 48),
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: matter_id)
    assertion = await issue_citator_watch_scope_assertion(
        db, current_user=user, matter_id=matter_id, authority_key="case:1",
        delivery_channels=["in_app"], quiet_hours={"timezone": "UTC"},
    )
    payload_part, _ = assertion.split(".")
    payload = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
    assert json.loads(payload)["matter_id"] == str(matter_id)


def test_issue_reviewer_authorization_requires_active_administrator(monkeypatch):
    kwargs = {
        "credential": "platform-jti",
        "principal": "reviewer-id",
        "authorization_basis": "bar membership verified",
    }
    with pytest.raises(PermissionError, match="administrator authorization"):
        issue_citator_reviewer_authorization_assertion(
            current_user=SimpleNamespace(role="member", is_active=True), **kwargs
        )
    with pytest.raises(PermissionError, match="administrator authorization"):
        issue_citator_reviewer_authorization_assertion(
            current_user=SimpleNamespace(role="admin", is_active=False), **kwargs
        )

    monkeypatch.setattr(
        citator_scope, "get_settings",
        lambda: SimpleNamespace(MCP_OPERATOR_ASSERTION_SECRET="o" * 48),
    )
    assertion = issue_citator_reviewer_authorization_assertion(
        current_user=SimpleNamespace(id="admin-id", role="admin", is_active=True), **kwargs
    )
    payload_part, _ = assertion.split(".")
    payload = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
    assert json.loads(payload)["actor"] == "admin-id"
