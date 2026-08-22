import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.fernet import Fernet

from app.config import Settings, validate_mcp_security_settings
from app.models.workspace_mcp_client import WorkspaceMCPClient


def _rsa_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(private_pem).decode(), base64.b64encode(public_pem).decode()


def _settings(**overrides):
    values = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test",
        "SECRET_KEY": "s" * 48,
        "DEV_MODE": False,
        "WORKSPACE_MCP_ENABLED": True,
        "WORKSPACE_MCP_RESOURCE": "https://getlawhand.com/api/mcp/workspace",
        "WORKSPACE_MCP_ISSUER": "https://getlawhand.com",
        "WORKSPACE_MCP_ALLOWED_TENANT_IDS": str(uuid.uuid4()),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    private, public = _rsa_pair()
    values.update(
        WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64=private,
        WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64=public,
        WORKSPACE_MCP_SIGNING_KEY_ID="workspace-test",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"WORKSPACE_MCP_RESOURCE": "https://getlawhand.com/mcp"}, "canonical"),
        (
            {"WORKSPACE_MCP_RESOURCE": "https://getlawhand.com/api/mcp/workspace?x=1"},
            "canonical",
        ),
        (
            {"WORKSPACE_MCP_RESOURCE": "https://user@getlawhand.com/api/mcp/workspace"},
            "canonical",
        ),
        ({"WORKSPACE_MCP_ISSUER": "/issuer"}, "absolute issuer"),
        ({"WORKSPACE_MCP_ISSUER": "https://auth.getlawhand.com"}, "share an origin"),
        (
            {
                "WORKSPACE_MCP_RESOURCE": "http://getlawhand.com/api/mcp/workspace",
                "WORKSPACE_MCP_ISSUER": "http://getlawhand.com",
            },
            "HTTPS",
        ),
        ({"WORKSPACE_MCP_ALLOWED_TENANT_IDS": "not-a-uuid"}, "invalid UUID"),
        ({"WORKSPACE_MCP_ALLOWED_TENANT_IDS": ""}, "pilot tenant UUIDs"),
        ({"WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES": 4}, "ACCESS_TOKEN_MAX_MINUTES"),
        ({"WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS": 59}, "AUTH_CODE_TTL_SECONDS"),
        ({"WORKSPACE_MCP_REFRESH_TOKEN_DAYS": 0}, "REFRESH_TOKEN_DAYS"),
        (
            {"WORKSPACE_MCP_REFRESH_TOKEN_DAYS": 31, "WORKSPACE_MCP_GRANT_DAYS": 30},
            "cover refresh lifetime",
        ),
        ({"WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS": 91}, "CLIENT_REGISTRATION_DAYS"),
    ],
)
def test_workspace_mcp_rejects_invalid_production_configuration(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_mcp_security_settings(_settings(**overrides))


@pytest.mark.parametrize(
    "previous",
    [
        "not-json",
        "{}",
        "[1]",
        '[{"kid":"workspace-test"}]',
        '[{"kid":"bad kid"}]',
    ],
)
def test_workspace_mcp_rejects_invalid_previous_signing_keys(previous):
    with pytest.raises(ValueError):
        validate_mcp_security_settings(
            _settings(WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON=previous)
        )


def test_workspace_mcp_client_defaults_and_activity_boundaries():
    now = datetime.now(timezone.utc)
    client = WorkspaceMCPClient(
        client_id="desktop-client",
        client_name="Desktop",
        redirect_uris=["https://client.example/callback", 7, None],
        expires_at=now + timedelta(minutes=1),
    )
    client.status = "active"
    assert client.redirect_uri_set == frozenset({"https://client.example/callback"})
    assert client.is_active(now)
    client.revoked_at = now
    assert not client.is_active(now)
    client.revoked_at = None
    client.status = "revoked"
    assert not client.is_active(now)
    client.status = "active"
    client.expires_at = now
    assert not client.is_active(now)
