import pytest
from fastapi import HTTPException
from starlette.requests import Request
import base64
import hashlib
import hmac
import time
import json

from mcp_server.server import require_internal_service_key
from mcp_server import server


def _request(path="/api/mcp/control/stage", body=b"{}"):
    request = Request({"type": "http", "method": "POST", "path": path, "headers": []})
    request._body = body
    return request


def _assertion(secret, *, expires=None, nonce="nonce-1", actor="operator", body=b"{}"):
    now = int(time.time())
    expires = expires if expires is not None else now + 30
    payload = json.dumps(
        {
            "actor": actor,
            "credential": "jti-1",
            "scope": "platform:write",
            "method": "POST",
            "path": "/api/mcp/control/stage",
            "issued": now,
            "expires": expires,
            "nonce": nonce,
            "body_sha256": hashlib.sha256(
                json.dumps(
                    json.loads(body.decode()), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return ".".join(
        (
            base64.urlsafe_b64encode(payload).decode().rstrip("="),
            base64.urlsafe_b64encode(signature).decode().rstrip("="),
        )
    )


def test_private_service_rejects_missing_configuration(monkeypatch):
    monkeypatch.delenv("MCP_UPSTREAM_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_internal_service_key("")
    assert exc.value.status_code == 503


def test_private_service_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", "service-secret-xxxxxxxxxxxxxxxxxx")
    with pytest.raises(HTTPException) as exc:
        require_internal_service_key("wrong")
    assert exc.value.status_code == 401


def test_private_service_accepts_exact_key(monkeypatch):
    secret = "service-secret-xxxxxxxxxxxxxxxxxx"
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", secret)
    assert require_internal_service_key(secret) is None


@pytest.mark.asyncio
async def test_signed_operator_context_rejects_replay_expiry_and_tamper(monkeypatch):
    secret = "service-secret-xxxxxxxxxxxxxxxxxx"
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", secret)
    consumed = set()
    monkeypatch.setattr(
        server,
        "consume_operator_assertion",
        lambda claims: (
            (_ for _ in ()).throw(
                HTTPException(
                    status_code=403, detail="replayed signed operator context"
                )
            )
            if claims["nonce"] in consumed
            else consumed.add(claims["nonce"])
        ),
    )
    request = _request()
    assertion = _assertion(secret)
    assert await server.operator_identity(request, "operator", assertion) == "operator"
    with pytest.raises(HTTPException, match="replayed"):
        await server.operator_identity(request, "operator", assertion)
    with pytest.raises(HTTPException, match="expired"):
        await server.operator_identity(
            request,
            "operator",
            _assertion(secret, expires=int(time.time()) - 1, nonce="nonce-2"),
        )
    with pytest.raises(HTTPException, match="invalid"):
        await server.operator_identity(
            _request(body=b'{"different":true}'),
            "operator",
            _assertion(secret, nonce="nonce-3"),
        )
    with pytest.raises(HTTPException, match="invalid"):
        await server.operator_identity(request, "operator", assertion[:-2] + "xx")
