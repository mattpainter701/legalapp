import pytest
from fastapi import HTTPException

from mcp_server.server import require_internal_service_key


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
