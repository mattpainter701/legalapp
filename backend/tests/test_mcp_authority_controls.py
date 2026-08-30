import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import jwt
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.operator_audit import OperatorAuditLog
from app.routers import mcp as mcp_router
from app.services import platform_auth
from app.middleware.platform_request_paths import is_platform_protected_path


def _request(headers=None):
    return SimpleNamespace(
        headers=headers or {}, state=SimpleNamespace(), method="POST"
    )


def test_authority_platform_paths_use_segment_boundaries():
    assert is_platform_protected_path("/api/platform")
    assert is_platform_protected_path("/api/platform/auth/token")
    assert is_platform_protected_path("/api/mcp/authority/promote")
    assert not is_platform_protected_path("/api/platformevil")
    assert not is_platform_protected_path("/api/mcp/authorityfoo")


@pytest.mark.asyncio
async def test_authority_control_forwards_signed_principal_and_ignores_spoofed_actor(
    monkeypatch,
):
    monkeypatch.setattr(mcp_router.settings, "MCP_SERVER_URL", "http://mcp.test")
    principal = SimpleNamespace(actor_id="signed-operator", credential_id="key-1")
    captured = {}

    monkeypatch.setattr(
        mcp_router,
        "require_platform_token",
        lambda request, scopes: principal,
    )

    async def fake_proxy(path, request, payload, *, extra_headers=None):
        captured.update(path=path, payload=payload, headers=extra_headers)
        return {"ok": True}

    monkeypatch.setattr(mcp_router, "_proxy_post", fake_proxy)
    body = mcp_router.AuthorityControlRequest(version="v1", reason="promote fixture")

    result = await mcp_router._authority_control(
        "promote",
        body,
        _request({"X-Operator-Identity": "spoofed-client"}),
    )

    assert result == {"ok": True}
    assert captured["path"] == "/api/mcp/control/promote"
    assert captured["headers"]["X-Operator-Identity"] == "signed-operator"
    assert captured["headers"]["X-Operator-Assertion"]
    assert captured["payload"]["reason"] == "promote fixture"


@pytest.mark.asyncio
async def test_authority_control_denial_does_not_call_upstream(monkeypatch):
    called = False

    def deny(request, scopes):
        raise HTTPException(status_code=403, detail="Platform token scope denied")

    async def unexpected_proxy(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mcp_router, "require_platform_token", deny)
    monkeypatch.setattr(mcp_router, "_proxy_post", unexpected_proxy)
    body = mcp_router.AuthorityControlRequest(version="v1", reason="denied")

    with pytest.raises(HTTPException) as exc:
        await mcp_router._authority_control("stage", body, _request())

    assert exc.value.status_code == 403
    assert called is False


@pytest.mark.asyncio
async def test_real_authority_route_session_and_denials_are_audited(
    client, db_session, monkeypatch
):
    raw_bootstrap = "authority-bootstrap-xxxxxxxxxxxxxxxxxxxxxxxx"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(
        platform_auth.settings,
        "PLATFORM_BOOTSTRAP_CREDENTIALS_JSON",
        json.dumps(
            [
                {
                    "operator_id": "authority-operator",
                    "key_hash": hashlib.sha256(raw_bootstrap.encode()).hexdigest(),
                    "scopes": ["platform:read", "platform:write"],
                    "expires_at": expires_at.isoformat(),
                }
            ]
        ),
    )
    monkeypatch.setattr(platform_auth.settings, "PLATFORM_TOKEN_SIGNING_KEY", "z" * 48)
    monkeypatch.setattr(mcp_router.settings, "MCP_SERVER_URL", "http://mcp.test")
    monkeypatch.setattr(mcp_router.settings, "MCP_OPERATOR_ASSERTION_SECRET", "s" * 48)
    calls = []

    async def fake_proxy(path, request, payload, *, extra_headers=None):
        calls.append((path, payload, extra_headers))
        return {"status": "accepted"}

    monkeypatch.setattr(mcp_router, "_proxy_post", fake_proxy)
    issued = await client.post(
        "/api/platform/auth/token",
        headers={"X-Platform-Key": raw_bootstrap},
        json={"scopes": ["platform:write"]},
    )
    assert issued.status_code == 200, issued.text
    session_headers = {
        "Authorization": f"Bearer {issued.json()['access_token']}",
        "X-Operator-Identity": "spoofed-client",
    }
    accepted = await client.post(
        "/api/mcp/authority/stage",
        headers=session_headers,
        json={"version": "v-test", "reason": "authorized fixture"},
    )
    assert accepted.status_code == 200, accepted.text
    assert calls[0][2]["X-Operator-Identity"] == "authority-operator"

    for headers in (
        {"Authorization": ""},
        {"Authorization": "Bearer malformed"},
        {
            "Authorization": f"Bearer {jwt.encode({'sub': 'wrong-issuer', 'jti': 'bad'}, 'z' * 48, algorithm='HS256')}"
        },
    ):
        denied = await client.post(
            "/api/mcp/authority/stage",
            headers=headers,
            json={"version": "v-test", "reason": "denied fixture"},
        )
        assert denied.status_code == 403
    read_only = await client.post(
        "/api/mcp/authority/stage",
        headers={
            "Authorization": f"Bearer {platform_auth.issue_platform_token(subject='read-only', scopes=['platform:read'], allowed_scopes=['platform:read'])[0]}"
        },
        json={"version": "v-test", "reason": "read-only fixture"},
    )
    assert read_only.status_code == 403
    tenant_jwt = await client.post(
        "/api/mcp/authority/stage",
        json={"version": "v-test", "reason": "tenant fixture"},
    )
    assert tenant_jwt.status_code == 403

    minted = await client.post(
        "/api/platform/api-keys",
        headers=session_headers,
        json={"label": "authority-matrix", "scopes": ["platform:write"]},
    )
    assert minted.status_code == 201, minted.text
    minted_headers = {"Authorization": f"Bearer {minted.json()['key']}"}
    minted_success = await client.post(
        "/api/mcp/authority/stage",
        headers=minted_headers,
        json={"version": "v-test", "reason": "minted fixture"},
    )
    assert minted_success.status_code == 200
    revoked = await client.delete(
        f"/api/platform/api-keys/{minted.json()['id']}",
        headers=session_headers,
    )
    assert revoked.status_code == 200
    revoked_denial = await client.post(
        "/api/mcp/authority/stage",
        headers=minted_headers,
        json={"version": "v-test", "reason": "revoked fixture"},
    )
    assert revoked_denial.status_code == 403
    assert len(calls) == 2

    logs = (
        (
            await db_session.execute(
                select(OperatorAuditLog).where(
                    OperatorAuditLog.action == "platform.request"
                )
            )
        )
        .scalars()
        .all()
    )
    operator_logs = [
        row for row in logs if row.resource_id == "/api/mcp/authority/stage"
    ]
    assert any(
        row.actor_id == "authority-operator"
        and row.metadata_json.get("scope") == "platform:write"
        and row.metadata_json.get("status_code") == 200
        for row in operator_logs
    )
    assert any(
        row.actor_id == "read-only"
        and row.metadata_json.get("scope") == "platform:write"
        and row.metadata_json.get("status_code") == 403
        for row in operator_logs
    )
    assert any(
        row.actor_id is None and row.metadata_json.get("status_code") == 403
        for row in operator_logs
    )


@pytest.mark.asyncio
async def test_backend_to_mcp_asgi_assertion_is_consumed_and_replay_is_rejected(
    client, db_session, monkeypatch
):
    """Exercise the actual proxy, MCP verifier, and durable nonce consumer."""
    db_url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://test:test@localhost:5432/legalapp_test"
    ).replace("postgresql+asyncpg://", "postgresql://")
    monkeypatch.setenv("VECTORDB_URL", db_url)
    mcp_root = str(Path(__file__).parents[2] / "mcp-server")
    if mcp_root not in sys.path:
        sys.path.insert(0, mcp_root)
    from mcp_server.loader import init_schema
    import mcp_server.server as mcp_server

    init_schema(db_url)
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", "u" * 48)
    monkeypatch.setenv("MCP_OPERATOR_ASSERTION_SECRET", "s" * 48)
    monkeypatch.setattr(mcp_router.settings, "MCP_SERVER_URL", "http://mcp.test")
    monkeypatch.setattr(mcp_router.settings, "MCP_UPSTREAM_API_KEY", "u" * 48)
    monkeypatch.setattr(mcp_router.settings, "MCP_OPERATOR_ASSERTION_SECRET", "s" * 48)
    monkeypatch.setattr(platform_auth.settings, "PLATFORM_TOKEN_SIGNING_KEY", "z" * 48)
    token, _, _ = platform_auth.issue_platform_token(
        subject="asgi-operator",
        scopes=["platform:write"],
        allowed_scopes=["platform:write"],
    )
    captured = {}
    real_client = mcp_router.httpx.AsyncClient

    class RecordingClient:
        def __init__(self, *args, **kwargs):
            self.inner = real_client(
                *args,
                transport=ASGITransport(app=mcp_server.app),
                base_url="http://mcp.test",
                **kwargs,
            )

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.inner.__aexit__(*args)

        async def post(self, url, **kwargs):
            captured.update(
                url=url,
                headers=dict(kwargs.get("headers") or {}),
                json=kwargs.get("json"),
            )
            return await self.inner.post(url, **kwargs)

    monkeypatch.setattr(mcp_router.httpx, "AsyncClient", RecordingClient)
    body = {
        "version": f"asgi-route-{uuid.uuid4().hex}",
        "reason": "real verifier fixture",
        "manifest_hash": "fixture-manifest",
        "as_of": "2026-08-30",
        "audit_kind": None,
        "embedding_model": "mixedbread-ai/mxbai-embed-large-v1",
        "embedding_version": "1",
        "embedding_dimension": 1024,
    }
    response = await client.post(
        "/api/mcp/authority/stage",
        headers={"Authorization": f"Bearer {token}", "X-Operator-Identity": "spoof"},
        json=body,
    )
    assert response.status_code == 200, response.text
    assert captured["headers"]["X-Operator-Identity"] == "asgi-operator"
    assertion = captured["headers"]["X-Operator-Assertion"]
    encoded_payload = assertion.split(".", 1)[0]
    claims = json.loads(
        base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
    )
    from mcp_server.database import connect

    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT credential_id, actor, scope, method, path, body_sha256
                FROM authority_operator_assertions WHERE nonce=%s
                """,
                [claims["nonce"]],
            )
            assertion_row = cur.fetchone()
    assert assertion_row == (
        claims["credential"],
        claims["actor"],
        claims["scope"],
        claims["method"],
        claims["path"],
        claims["body_sha256"],
    )
    async with AsyncClient(
        transport=ASGITransport(app=mcp_server.app), base_url="http://mcp.test"
    ) as direct:
        replay = await direct.post(
            "/api/mcp/control/stage",
            headers={
                "X-Clarity-Internal-Key": "u" * 48,
                "X-Operator-Identity": "asgi-operator",
                "X-Operator-Assertion": assertion,
            },
            json=captured["json"],
        )
    assert replay.status_code == 403
    assert replay.json()["detail"] == "replayed signed operator context"
    duplicate = await client.post(
        "/api/mcp/authority/stage",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert duplicate.status_code == 409
    monkeypatch.setenv("MCP_OPERATOR_ASSERTION_SECRET", "wrong" + "s" * 43)
    bad_signer = await client.post(
        "/api/mcp/authority/stage",
        headers={"Authorization": f"Bearer {token}"},
        json={**body, "version": f"asgi-bad-signer-{uuid.uuid4().hex}"},
    )
    assert bad_signer.status_code == 403
    monkeypatch.setenv("MCP_OPERATOR_ASSERTION_SECRET", "s" * 48)
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", "wrong" + "u" * 43)
    bad_transport_credential = await client.post(
        "/api/mcp/authority/stage",
        headers={"Authorization": f"Bearer {token}"},
        json={**body, "version": f"asgi-bad-key-{uuid.uuid4().hex}"},
    )
    assert bad_transport_credential.status_code == 401
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", "u" * 48)
    monkeypatch.delenv("MCP_OPERATOR_ASSERTION_SECRET")
    unconfigured_signer = await client.post(
        "/api/mcp/authority/stage",
        headers={"Authorization": f"Bearer {token}"},
        json={**body, "version": f"asgi-no-signer-{uuid.uuid4().hex}"},
    )
    assert unconfigured_signer.status_code == 503
    audit_rows = (
        (
            await db_session.execute(
                select(OperatorAuditLog).where(
                    OperatorAuditLog.resource_id == "/api/mcp/authority/stage"
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(row.actor_id == "asgi-operator" for row in audit_rows)
