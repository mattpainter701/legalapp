import pytest
from fastapi import HTTPException
from starlette.requests import Request
import base64
import hashlib
import hmac
import time

from mcp_server.server import require_internal_service_key
from mcp_server import server


def _request(path="/api/mcp/control/stage"):
    return Request({"type": "http", "method": "POST", "path": path, "headers": []})


def _assertion(secret, *, expires=None, nonce="nonce-1", actor="operator"):
    now = int(time.time())
    expires = expires if expires is not None else now + 30
    payload = "|".join((actor, "jti-1", "platform:write", "POST",
                         "/api/mcp/control/stage", str(now), str(expires), nonce))
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload.encode() + b"|" + signature).decode().rstrip("=")


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


def test_signed_operator_context_rejects_replay_expiry_and_tamper(monkeypatch):
    secret = "service-secret-xxxxxxxxxxxxxxxxxx"
    monkeypatch.setenv("MCP_UPSTREAM_API_KEY", secret)
    request = _request()
    assertion = _assertion(secret)
    assert server.operator_identity(request, "operator", assertion) == "operator"
    with pytest.raises(HTTPException, match="replayed"):
        server.operator_identity(request, "operator", assertion)
    with pytest.raises(HTTPException, match="expired"):
        server.operator_identity(request, "operator", _assertion(secret, expires=int(time.time()) - 1, nonce="nonce-2"))
    with pytest.raises(HTTPException, match="invalid"):
        server.operator_identity(request, "operator", assertion[:-2] + "xx")
