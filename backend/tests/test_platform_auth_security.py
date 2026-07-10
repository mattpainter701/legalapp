import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from app.models.operator_audit import OperatorAuditLog
from app.services import platform_auth


RAW_BOOTSTRAP = "operator-bootstrap-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
SIGNING_KEY = "operator-signing-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _configure(monkeypatch, *, expires_delta=timedelta(hours=1)):
    expires_at = datetime.now(timezone.utc) + expires_delta
    entry = {
        "operator_id": "ops@example.com",
        "key_hash": hashlib.sha256(RAW_BOOTSTRAP.encode()).hexdigest(),
        "scopes": ["platform:read"],
        "expires_at": expires_at.isoformat(),
    }
    monkeypatch.setattr(
        platform_auth.settings,
        "PLATFORM_BOOTSTRAP_CREDENTIALS_JSON",
        json.dumps([entry]),
    )
    monkeypatch.setattr(
        platform_auth.settings, "PLATFORM_TOKEN_SIGNING_KEY", SIGNING_KEY
    )
    monkeypatch.setattr(
        platform_auth.settings, "PLATFORM_LEGACY_BOOTSTRAP_ENABLED", False
    )
    return entry


def test_bootstrap_identity_and_scope_come_from_hashed_configuration(monkeypatch):
    _configure(monkeypatch)
    principal = platform_auth.verify_platform_bootstrap_key(RAW_BOOTSTRAP)
    assert principal.operator_id == "ops@example.com"
    assert principal.scopes == ["platform:read"]

    token, _, scopes = platform_auth.issue_platform_token(
        subject=principal.operator_id,
        allowed_scopes=principal.scopes,
    )
    claims = platform_auth.decode_platform_token(token)
    assert claims["sub"] == "ops@example.com"
    assert scopes == ["platform:read"]


def test_bootstrap_cannot_escalate_its_scope(monkeypatch):
    _configure(monkeypatch)
    principal = platform_auth.verify_platform_bootstrap_key(RAW_BOOTSTRAP)
    with pytest.raises(HTTPException) as exc:
        platform_auth.issue_platform_token(
            subject=principal.operator_id,
            scopes=["platform:write"],
            allowed_scopes=principal.scopes,
        )
    assert exc.value.status_code == 403


def test_session_never_outlives_bootstrap_credential(monkeypatch):
    entry = _configure(monkeypatch, expires_delta=timedelta(minutes=1))
    principal = platform_auth.verify_platform_bootstrap_key(RAW_BOOTSTRAP)
    token, expires_at, _ = platform_auth.issue_platform_token(
        subject=principal.operator_id,
        allowed_scopes=principal.scopes,
        ttl_minutes=60,
        not_after=principal.expires_at,
    )
    bootstrap_expiry = datetime.fromisoformat(entry["expires_at"])
    assert expires_at <= bootstrap_expiry
    assert platform_auth.decode_platform_token(token)["exp"] <= int(
        bootstrap_expiry.timestamp()
    )


def test_expired_bootstrap_is_rejected(monkeypatch):
    _configure(monkeypatch, expires_delta=timedelta(seconds=-1))
    with pytest.raises(HTTPException) as exc:
        platform_auth.verify_platform_bootstrap_key(RAW_BOOTSTRAP)
    assert exc.value.status_code == 403


def test_static_legacy_key_is_ignored_without_explicit_bridge(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(platform_auth.settings, "PLATFORM_SECRET_KEY", RAW_BOOTSTRAP)
    with pytest.raises(HTTPException):
        platform_auth.verify_platform_bootstrap_key("different-key")


def test_token_scope_is_enforced_per_request(monkeypatch):
    _configure(monkeypatch)
    token, _, _ = platform_auth.issue_platform_token(
        subject="ops@example.com",
        scopes=["platform:read"],
        allowed_scopes=["platform:read"],
    )
    request = SimpleNamespace(
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
        url=SimpleNamespace(path="/api/platform/tenants/id"),
        state=SimpleNamespace(),
    )
    with pytest.raises(HTTPException) as exc:
        platform_auth.require_platform_token(request)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_bootstrap_exchange_binds_audited_identity(
    client: AsyncClient, db_session, monkeypatch
):
    _configure(monkeypatch)
    response = await client.post(
        "/api/platform/auth/token",
        headers={"X-Platform-Key": RAW_BOOTSTRAP},
        json={},
    )
    assert response.status_code == 200
    claims = platform_auth.decode_platform_token(response.json()["access_token"])
    assert claims["sub"] == "ops@example.com"
    assert claims["scope"] == ["platform:read"]

    logs = (
        (
            await db_session.execute(
                select(OperatorAuditLog).where(
                    OperatorAuditLog.action == "platform.session.issued"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].actor_id == "ops@example.com"


@pytest.mark.asyncio
async def test_bootstrap_secret_cannot_call_operator_api_directly(
    client: AsyncClient, monkeypatch
):
    _configure(monkeypatch)
    response = await client.get(
        "/api/platform/plans",
        headers={"X-Platform-Key": RAW_BOOTSTRAP},
    )
    assert response.status_code == 403
