import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_mcp_security_settings


def _settings(**overrides):
    values = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test",
        "SECRET_KEY": "x" * 48,
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
