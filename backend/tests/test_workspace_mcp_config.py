import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_mcp_security_settings


def _settings(**overrides):
    values = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test",
        "SECRET_KEY": "x" * 48,
        "DEV_MODE": True,
        "WORKSPACE_MCP_RESOURCE": "https://auth.getlawhand.com/api/mcp/workspace",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    values.update(overrides)
    return Settings(**values)


def test_workspace_mcp_defaults_fail_closed():
    settings = _settings()

    assert settings.WORKSPACE_MCP_ENABLED is False
    validate_mcp_security_settings(settings)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "WORKSPACE_MCP_ENABLED": True,
                "WORKSPACE_MCP_AUDIENCE": "",
                "WORKSPACE_MCP_ISSUER": "https://auth.getlawhand.com",
            },
            "WORKSPACE_MCP_AUDIENCE",
        ),
        (
            {
                "WORKSPACE_MCP_ENABLED": True,
                "WORKSPACE_MCP_AUDIENCE": "lawhand-workspace-mcp",
                "WORKSPACE_MCP_ISSUER": "",
            },
            "WORKSPACE_MCP_ISSUER",
        ),
        (
            {
                "WORKSPACE_MCP_ENABLED": True,
                "WORKSPACE_MCP_AUDIENCE": "lawhand-workspace-mcp",
                "WORKSPACE_MCP_ISSUER": "https://auth.getlawhand.com",
                "WORKSPACE_MCP_TOKEN_SIGNING_KEY": "",
            },
            "WORKSPACE_MCP_TOKEN_SIGNING_KEY",
        ),
        (
            {
                "WORKSPACE_MCP_ENABLED": True,
                "WORKSPACE_MCP_AUDIENCE": "lawhand-workspace-mcp",
                "WORKSPACE_MCP_ISSUER": "https://auth.getlawhand.com",
                "WORKSPACE_MCP_TOKEN_SIGNING_KEY": "x" * 48,
            },
            "must not equal SECRET_KEY",
        ),
    ],
)
def test_workspace_mcp_requires_audience_and_issuer(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_mcp_security_settings(_settings(**overrides))


def test_workspace_mcp_accepts_a_distinct_signing_key():
    settings = _settings(
        WORKSPACE_MCP_ENABLED=True,
        WORKSPACE_MCP_AUDIENCE="lawhand-workspace-mcp",
        WORKSPACE_MCP_ISSUER="https://auth.getlawhand.com",
        WORKSPACE_MCP_TOKEN_SIGNING_KEY="w" * 48,
    )

    validate_mcp_security_settings(settings)

    assert settings.WORKSPACE_MCP_TOKEN_SIGNING_KEY != settings.SECRET_KEY


@pytest.mark.parametrize("minutes", [0, 4, 61, 600])
def test_workspace_mcp_rejects_unbounded_access_token_lifetime(minutes):
    settings = _settings(
        WORKSPACE_MCP_ENABLED=True,
        WORKSPACE_MCP_AUDIENCE="lawhand-workspace-mcp",
        WORKSPACE_MCP_ISSUER="https://auth.getlawhand.com",
        WORKSPACE_MCP_TOKEN_SIGNING_KEY="w" * 48,
        WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES=minutes,
    )

    with pytest.raises(ValueError, match="WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES"):
        validate_mcp_security_settings(settings)


def _rsa_pair() -> tuple[str, str]:
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
    return (
        base64.b64encode(private_pem).decode(),
        base64.b64encode(public_pem).decode(),
    )


def test_workspace_mcp_accepts_production_asymmetric_signing():
    private_key, public_key = _rsa_pair()
    settings = _settings(
        DEV_MODE=False,
        WORKSPACE_MCP_ENABLED=True,
        WORKSPACE_MCP_RESOURCE="https://getlawhand.com/api/mcp/workspace",
        WORKSPACE_MCP_ISSUER="https://getlawhand.com",
        WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64=private_key,
        WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64=public_key,
        WORKSPACE_MCP_SIGNING_KEY_ID="workspace-2026-08",
    )

    validate_mcp_security_settings(settings)


def test_workspace_mcp_production_accepts_native_tenant_admin_rollout():
    private_key, public_key = _rsa_pair()
    settings = _settings(
        DEV_MODE=False,
        WORKSPACE_MCP_ENABLED=True,
        WORKSPACE_MCP_RESOURCE="https://getlawhand.com/api/mcp/workspace",
        WORKSPACE_MCP_ISSUER="https://getlawhand.com",
        WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64=private_key,
        WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64=public_key,
        WORKSPACE_MCP_SIGNING_KEY_ID="workspace-2026-08",
    )

    validate_mcp_security_settings(settings)
