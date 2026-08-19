import os
import pytest
from cryptography.fernet import Fernet

from app.config import (
    Settings,
    validate_demo_settings,
    validate_jwt_algorithm,
    validate_mcp_security_settings,
    validate_platform_bootstrap_settings,
    validate_token_encryption_key,
)


def _demo_settings(**overrides):
    values = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test",
        "SECRET_KEY": "x" * 48,
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    values.update(overrides)
    return Settings(**values)


def test_demo_settings_are_inert_while_disabled():
    validate_demo_settings(
        _demo_settings(DEMO_MODE_ENABLED=False, DEMO_ACCESS_CODE="short")
    )


def test_demo_settings_require_strong_code_and_fixture_when_enabled():
    with pytest.raises(ValueError, match="DEMO_ACCESS_CODE"):
        validate_demo_settings(
            _demo_settings(DEMO_MODE_ENABLED=True, DEMO_ACCESS_CODE="short")
        )

    with pytest.raises(ValueError, match="DEMO_FIXTURE_TENANT_DOMAIN"):
        validate_demo_settings(
            _demo_settings(
                DEMO_MODE_ENABLED=True,
                DEMO_ACCESS_CODE="a-strong-demo-code-123",
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DEMO_SESSION_TTL_HOURS", 0),
        ("DEMO_SESSION_TTL_HOURS", 169),
        ("DEMO_MESSAGE_QUOTA", 0),
        ("DEMO_MESSAGE_QUOTA", 101),
        ("DEMO_MAX_ACTIVE", 0),
        ("DEMO_MAX_ACTIVE", 26),
    ],
)
def test_demo_settings_reject_unsafe_bounds(field, value):
    with pytest.raises(ValueError, match=field):
        validate_demo_settings(
            _demo_settings(
                DEMO_MODE_ENABLED=True,
                DEMO_ACCESS_CODE="a-strong-demo-code-123",
                DEMO_FIXTURE_TENANT_DOMAIN="demo-fixture.example",
                **{field: value},
            )
        )


def test_application_jwt_algorithm_is_strictly_hs256():
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        ALGORITHM="HS256",
    )
    validate_jwt_algorithm(settings)


@pytest.mark.parametrize("algorithm", ["ES256", "RS256", "none", "hs256", ""])
def test_application_jwt_algorithm_rejects_every_other_profile(algorithm):
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        ALGORITHM=algorithm,
    )
    with pytest.raises(ValueError, match="exactly HS256"):
        validate_jwt_algorithm(settings)


def test_token_encryption_key_required(monkeypatch):
    """TOKEN_ENCRYPTION_KEY must be set and valid."""
    # Generate a valid Fernet key
    valid_key = Fernet.generate_key().decode()

    # Create settings with valid key
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", valid_key)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    settings = Settings(_env_file=None)
    # This should not raise
    validate_token_encryption_key(settings)


def test_token_encryption_key_missing_raises(monkeypatch):
    """TOKEN_ENCRYPTION_KEY missing should raise ValueError with helpful message."""
    # Clear the env var
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    with pytest.raises(
        ValueError, match="TOKEN_ENCRYPTION_KEYS or TOKEN_ENCRYPTION_KEY is required"
    ):
        settings = Settings(_env_file=None)
        validate_token_encryption_key(settings)


def test_token_encryption_key_invalid_raises(monkeypatch):
    """Invalid Fernet key should raise ValueError with helpful message."""
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "invalid-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    with pytest.raises(
        ValueError, match="must be a valid Fernet key"
    ):
        settings = Settings(_env_file=None)
        validate_token_encryption_key(settings)


def test_mcp_server_url_defaults_to_empty_for_local_fallback():
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["DATABASE_URL"] = "postgresql://test"
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ.pop("MCP_SERVER_URL", None)

    settings = Settings(_env_file=None)

    assert settings.MCP_SERVER_URL == ""


def test_mcp_upstream_key_required_when_private_service_is_configured():
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        MCP_SERVER_URL="http://courtlistener-mcp:8021",
        MCP_UPSTREAM_API_KEY="",
    )
    with pytest.raises(ValueError, match="MCP_UPSTREAM_API_KEY"):
        validate_mcp_security_settings(settings)


def test_platform_bootstrap_requires_identity_scope_expiry_and_distinct_signing_key():
    import hashlib
    import json

    entry = {
        "operator_id": "ops@example.com",
        "key_hash": hashlib.sha256(b"bootstrap").hexdigest(),
        "scopes": ["platform:read"],
        "expires_at": "2030-01-01T00:00:00Z",
    }
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        PLATFORM_BOOTSTRAP_CREDENTIALS_JSON=json.dumps([entry]),
        PLATFORM_TOKEN_SIGNING_KEY="s" * 48,
    )
    validate_platform_bootstrap_settings(settings)
