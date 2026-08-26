import base64
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
        (
            {
                "WORKSPACE_MCP_RESOURCE": "http://getlawhand.com/api/mcp/workspace",
                "WORKSPACE_MCP_ISSUER": "http://getlawhand.com",
            },
            "HTTPS",
        ),
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


def test_workspace_mcp_accepts_canonical_subdomain_with_apex_issuer_and_aliases():
    legacy_resource = "https://getlawhand.com/api/mcp/workspace"
    extra_alias = "https://legacy.getlawhand.com/api/mcp/workspace"
    settings = _settings(
        WORKSPACE_MCP_RESOURCE=legacy_resource,
        WORKSPACE_MCP_CANONICAL_RESOURCE=(
            "https://mcp.getlawhand.com/api/mcp/workspace"
        ),
        WORKSPACE_MCP_RESOURCE_ALIASES=extra_alias,
        WORKSPACE_MCP_ISSUER="https://getlawhand.com",
    )

    validate_mcp_security_settings(settings)

    assert settings.workspace_mcp_endpoint == (
        "https://mcp.getlawhand.com/api/mcp/workspace"
    )
    assert settings.workspace_mcp_legacy_resources == (
        legacy_resource,
        extra_alias,
    )


def test_workspace_mcp_rejects_canonical_resource_repeated_as_alias():
    with pytest.raises(ValueError, match="unique"):
        validate_mcp_security_settings(
            _settings(
                WORKSPACE_MCP_CANONICAL_RESOURCE=(
                    "https://getlawhand.com/api/mcp/workspace"
                )
            )
        )


def test_research_mcp_public_url_is_exact_and_https():
    with pytest.raises(ValueError, match="RESEARCH_MCP_PUBLIC_URL"):
        validate_mcp_security_settings(
            _settings(
                RESEARCH_MCP_PUBLIC_URL=(
                    "https://research.getlawhand.com/api/mcp?unsafe=1"
                )
            )
        )


def _research_settings(**overrides):
    values = {
        "MCP_PRODUCT_ENABLED": True,
        "RESEARCH_MCP_OAUTH_ENABLED": True,
        "RESEARCH_MCP_PUBLIC_URL": "https://research.getlawhand.com/api/mcp",
        "RESEARCH_MCP_AUDIENCE": "lawhand-research-mcp",
        "RESEARCH_MCP_ISSUER": "https://research.getlawhand.com",
    }
    values.update(overrides)
    return _settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"RESEARCH_MCP_AUDIENCE": ""}, "RESEARCH_MCP_AUDIENCE"),
        ({"RESEARCH_MCP_ISSUER": ""}, "RESEARCH_MCP_ISSUER"),
        (
            {"RESEARCH_MCP_ISSUER": "https://research.getlawhand.com/issuer"},
            "absolute origin",
        ),
        (
            {
                "RESEARCH_MCP_PUBLIC_URL": ("http://research.getlawhand.com/api/mcp"),
                "RESEARCH_MCP_ISSUER": "http://research.getlawhand.com",
            },
            "HTTPS",
        ),
        (
            {"RESEARCH_MCP_ISSUER": "https://auth.getlawhand.com"},
            "match the canonical",
        ),
        ({"RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES": 4}, "ACCESS_TOKEN_MAX_MINUTES"),
        ({"RESEARCH_MCP_AUTH_CODE_TTL_SECONDS": 59}, "AUTH_CODE_TTL_SECONDS"),
        ({"RESEARCH_MCP_REFRESH_TOKEN_DAYS": 0}, "REFRESH_TOKEN_DAYS"),
        (
            {"RESEARCH_MCP_REFRESH_TOKEN_DAYS": 31, "RESEARCH_MCP_GRANT_DAYS": 30},
            "cover refresh lifetime",
        ),
        (
            {"RESEARCH_MCP_CLIENT_REGISTRATION_DAYS": 91},
            "CLIENT_REGISTRATION_DAYS",
        ),
    ],
)
def test_research_mcp_rejects_invalid_oauth_configuration(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_mcp_security_settings(_research_settings(**overrides))


def test_research_mcp_accepts_dedicated_origin_and_shared_keyring():
    settings = _research_settings()

    validate_mcp_security_settings(settings)

    assert settings.research_mcp_endpoint == ("https://research.getlawhand.com/api/mcp")
    assert settings.research_mcp_shorthand == "https://research.getlawhand.com"


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
