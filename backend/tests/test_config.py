from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.config import (
    Settings,
    validate_demo_settings,
    validate_jwt_algorithm,
    validate_mcp_security_settings,
    validate_platform_bootstrap_settings,
    validate_qbo_settings,
    validate_studio_render_paths,
    validate_template_studio_settings,
    validate_token_encryption_key,
    validate_worker_settings,
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


def test_durable_worker_concurrency_is_bounded():
    validate_worker_settings(SimpleNamespace(DURABLE_JOB_TENANT_CONCURRENCY=4))
    for concurrency in (0, 17):
        with pytest.raises(ValueError, match="DURABLE_JOB_TENANT_CONCURRENCY"):
            validate_worker_settings(
                SimpleNamespace(DURABLE_JOB_TENANT_CONCURRENCY=concurrency)
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("TEMPLATE_STUDIO_SOURCE_ARTIFACT_QUOTA", 0),
        ("TEMPLATE_STUDIO_SOURCE_BYTES_QUOTA", 1_048_575),
        ("TEMPLATE_STUDIO_SOURCE_ORPHAN_TTL_HOURS", 0),
    ],
)
def test_template_studio_source_limits_are_conservative_and_bounded(field, value):
    settings = _demo_settings()
    assert settings.TEMPLATE_STUDIO_SOURCE_ARTIFACT_QUOTA == 100
    assert settings.TEMPLATE_STUDIO_SOURCE_BYTES_QUOTA == 250 * 1024 * 1024
    assert settings.TEMPLATE_STUDIO_SOURCE_ORPHAN_TTL_HOURS == 24
    setattr(settings, field, value)
    with pytest.raises(ValueError, match=field):
        validate_template_studio_settings(settings)


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

    with pytest.raises(ValueError, match="must be a valid Fernet key"):
        settings = Settings(_env_file=None)
        validate_token_encryption_key(settings)


def test_mcp_server_url_defaults_to_empty_for_local_fallback(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.MCP_SERVER_URL == ""


def test_research_mcp_shorthand_is_public_origin_only():
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        RESEARCH_MCP_PUBLIC_URL="https://research.getlawhand.com/api/mcp",
    )

    assert settings.research_mcp_endpoint == ("https://research.getlawhand.com/api/mcp")
    assert settings.research_mcp_shorthand == "https://research.getlawhand.com"


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


def test_mcp_operator_assertion_signer_is_required_and_distinct():
    base = dict(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        MCP_SERVER_URL="http://courtlistener-mcp:8021",
        MCP_UPSTREAM_API_KEY="u" * 40,
    )
    with pytest.raises(ValueError, match="MCP_OPERATOR_ASSERTION_SECRET"):
        validate_mcp_security_settings(Settings(**base))
    with pytest.raises(ValueError, match="distinct"):
        validate_mcp_security_settings(
            Settings(
                **{
                    **base,
                    "MCP_OPERATOR_ASSERTION_SECRET": "u" * 40,
                    "MCP_CITATOR_SCOPE_ASSERTION_SECRET": "c" * 40,
                }
            )
        )
    with pytest.raises(ValueError, match="MCP_CITATOR_SCOPE_ASSERTION_SECRET"):
        validate_mcp_security_settings(
            Settings(**{**base, "MCP_OPERATOR_ASSERTION_SECRET": "s" * 40})
        )
    validate_mcp_security_settings(
        Settings(
            **{
                **base,
                "MCP_OPERATOR_ASSERTION_SECRET": "s" * 40,
                "MCP_CITATOR_SCOPE_ASSERTION_SECRET": "c" * 40,
            }
        )
    )


def test_mcp_operator_assertion_signer_cannot_reuse_platform_or_encryption_secret():
    base = dict(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        TOKEN_ENCRYPTION_KEYS="",
        MCP_SERVER_URL="http://courtlistener-mcp:8021",
        MCP_UPSTREAM_API_KEY="u" * 40,
        MCP_OPERATOR_ASSERTION_SECRET="s" * 40,
        MCP_CITATOR_SCOPE_ASSERTION_SECRET="c" * 40,
    )
    for field in ("SECRET_KEY", "PLATFORM_TOKEN_SIGNING_KEY", "PLATFORM_SECRET_KEY"):
        with pytest.raises(ValueError, match=field):
            validate_mcp_security_settings(Settings(**{**base, field: "s" * 40}))
    with pytest.raises(ValueError, match="token-encryption"):
        validate_mcp_security_settings(
            Settings(**{**base, "TOKEN_ENCRYPTION_KEY": "s" * 40})
        )
    with pytest.raises(ValueError, match="token-encryption"):
        validate_mcp_security_settings(
            Settings(
                **{
                    **base,
                    "TOKEN_ENCRYPTION_KEYS": "first-key,s" + "s" * 39,
                }
            )
        )


def test_citator_scope_signer_cannot_reuse_other_mcp_or_platform_secrets():
    base = dict(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        SECRET_KEY="x" * 48,
        TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        TOKEN_ENCRYPTION_KEYS="",
        MCP_SERVER_URL="http://courtlistener-mcp:8021",
        MCP_UPSTREAM_API_KEY="u" * 40,
        MCP_OPERATOR_ASSERTION_SECRET="s" * 40,
        MCP_CITATOR_SCOPE_ASSERTION_SECRET="c" * 40,
    )
    with pytest.raises(ValueError, match="MCP_CITATOR_SCOPE_ASSERTION_SECRET"):
        validate_mcp_security_settings(
            Settings(**{**base, "MCP_CITATOR_SCOPE_ASSERTION_SECRET": "s" * 40})
        )
    with pytest.raises(ValueError, match="SECRET_KEY"):
        validate_mcp_security_settings(
            Settings(**{**base, "MCP_CITATOR_SCOPE_ASSERTION_SECRET": "x" * 48})
        )
    with pytest.raises(ValueError, match="token-encryption"):
        validate_mcp_security_settings(
            Settings(
                **{
                    **base,
                    "MCP_CITATOR_SCOPE_ASSERTION_SECRET": "c" * 40,
                    "TOKEN_ENCRYPTION_KEY": "c" * 40,
                }
            )
        )


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


def test_qbo_production_settings_require_exact_backend_callback():
    settings = _demo_settings(
        BACKEND_URL="https://getlawhand.com",
        QBO_CLIENT_ID="production-client-id",
        QBO_CLIENT_SECRET="production-client-secret",
        QBO_REDIRECT_URI="https://getlawhand.com/api/integrations/qbo/callback",
        QBO_ENVIRONMENT="production",
    )
    validate_qbo_settings(settings)

    settings.QBO_REDIRECT_URI = (
        "https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl"
    )
    with pytest.raises(ValueError, match="must exactly match"):
        validate_qbo_settings(settings)


def test_qbo_settings_reject_invalid_environment_and_partial_credentials():
    validate_qbo_settings(
        _demo_settings(
            QBO_CLIENT_ID="sandbox-client",
            QBO_CLIENT_SECRET="sandbox-secret",
            QBO_REDIRECT_URI="http://localhost:8000/api/integrations/qbo/callback",
            QBO_ENVIRONMENT="sandbox",
        )
    )
    with pytest.raises(ValueError, match="QBO_ENVIRONMENT"):
        validate_qbo_settings(_demo_settings(QBO_ENVIRONMENT="live"))

    with pytest.raises(ValueError, match="QBO_CLIENT_ID and QBO_CLIENT_SECRET"):
        validate_qbo_settings(
            _demo_settings(
                QBO_CLIENT_ID="client-only",
                QBO_REDIRECT_URI="https://getlawhand.com/api/integrations/qbo/callback",
            )
        )


def _render_settings(**overrides):
    import json
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.gettempdir())
    manifest = {
        "contract_version": 1,
        "capabilities": [
            {
                "kind": "studio_test_render",
                "source_format": "markdown",
                "render_options_contract_version": 1,
                "output_media_type": "application/pdf",
                "renderer_manifest": {
                    "contract_version": 1,
                    "isolation_policy_id": "studio-test-v1",
                    "boundary_kind": "attested_supervisor_v1",
                    "launcher_sha256": "1" * 64,
                    "sandbox_policy_sha256": "2" * 64,
                    "fixed_arguments_sha256": "3" * 64,
                    "environment_sha256": "4" * 64,
                    "runtime_bundle_sha256": "5" * 64,
                    "font_pack_sha256": "6" * 64,
                    "renderer": {
                        "name": "renderer",
                        "version": "1.0.0",
                        "content_sha256": "7" * 64,
                    },
                    "rasterizer": {
                        "name": "rasterizer",
                        "version": "1.0.0",
                        "content_sha256": "8" * 64,
                    },
                    "converter": {
                        "name": "converter",
                        "version": "1.0.0",
                        "content_sha256": "9" * 64,
                    },
                    "validator": {
                        "name": "validator",
                        "version": "1.0.0",
                        "content_sha256": "a" * 64,
                    },
                },
            }
        ],
    }
    profiles = {
        "studio_test_render:markdown:v1": {
            "processor_timeout_seconds": 60,
            "max_output_bytes": 10 * 1024 * 1024,
        }
    }
    values = {
        "BACKEND_URL": "https://studio.example",
        "UPLOAD_DIR": str(tmp / "uploads"),
        "TEMPLATE_STUDIO_RENDER_ENABLED": True,
        "TEMPLATE_STUDIO_RENDER_WORKER_ENABLED": True,
        "TEMPLATE_STUDIO_RENDER_STORAGE_DIR": str(tmp / "studio-storage"),
        "TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR": str(tmp / "studio-workspace"),
        "TEMPLATE_STUDIO_RENDER_STORAGE_TOPOLOGY": "single_host_local",
        "TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON": json.dumps(manifest),
        "TEMPLATE_STUDIO_RENDER_PROFILES_JSON": json.dumps(profiles),
    }
    values.update(overrides)
    return _demo_settings(**values)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("TEMPLATE_STUDIO_RENDER_ACTIVE_JOB_LIMIT", 0, "ACTIVE_JOB_LIMIT"),
        ("TEMPLATE_STUDIO_RENDER_ACTIVE_JOB_LIMIT", 33, "ACTIVE_JOB_LIMIT"),
        ("TEMPLATE_STUDIO_RENDER_JOB_TTL_SECONDS", 299, "JOB_TTL_SECONDS"),
        ("TEMPLATE_STUDIO_RENDER_JOB_TTL_SECONDS", 604_801, "JOB_TTL_SECONDS"),
        ("TEMPLATE_STUDIO_RENDER_ENQUEUE_RATE_LIMIT", 0, "ENQUEUE_RATE_LIMIT"),
        ("TEMPLATE_STUDIO_RENDER_ENQUEUE_RATE_WINDOW_SECONDS", 0, "RATE_WINDOW"),
        ("TEMPLATE_STUDIO_RENDER_QUEUED_BYTE_LIMIT", 0, "QUEUED_BYTE_LIMIT"),
        ("TEMPLATE_STUDIO_RENDER_RETAINED_ARTIFACT_LIMIT", 0, "RETAINED_ARTIFACT"),
        ("TEMPLATE_STUDIO_RENDER_LIVE_ARTIFACT_LIMIT", 0, "LIVE_ARTIFACT"),
        ("TEMPLATE_STUDIO_RENDER_BATCH_SIZE", 0, "BATCH_SIZE"),
        ("TEMPLATE_STUDIO_RENDER_CONCURRENCY", 0, "CONCURRENCY"),
        ("TEMPLATE_STUDIO_RENDER_IDLE_SECONDS", 0.001, "IDLE_SECONDS"),
        ("TEMPLATE_STUDIO_RENDER_HEALTH_MAX_AGE_SECONDS", 19, "HEALTH_MAX_AGE"),
        ("TEMPLATE_STUDIO_RENDER_LEASE_SECONDS", 29, "LEASE_SECONDS"),
        ("TEMPLATE_STUDIO_RENDER_HEARTBEAT_SECONDS", 4, "HEARTBEAT"),
        ("TEMPLATE_STUDIO_RENDER_HEARTBEAT_SECONDS", 500, "HEARTBEAT"),
        ("TEMPLATE_STUDIO_RENDER_PROCESSOR_TIMEOUT_SECONDS", 4, "PROCESSOR_TIMEOUT"),
        ("TEMPLATE_STUDIO_RENDER_ARTIFACT_TTL_SECONDS", 299, "ARTIFACT_TTL"),
        ("TEMPLATE_STUDIO_RENDER_METADATA_TTL_SECONDS", 86_399, "METADATA_TTL"),
    ],
)
def test_template_studio_render_settings_reject_out_of_bounds_values(
    field, value, expected
):
    settings = _render_settings(**{field: value})
    with pytest.raises(ValueError, match=expected):
        validate_template_studio_settings(settings)


def test_template_studio_render_worker_requires_rendering_enabled():
    settings = _render_settings(
        TEMPLATE_STUDIO_RENDER_ENABLED=False,
        TEMPLATE_STUDIO_RENDER_WORKER_ENABLED=True,
    )
    with pytest.raises(ValueError, match="worker requires rendering"):
        validate_template_studio_settings(settings)


def test_template_studio_render_requires_https_backend_origin():
    settings = _render_settings(BACKEND_URL="http://studio.example")
    with pytest.raises(ValueError, match="BACKEND_URL"):
        validate_template_studio_settings(settings)


def test_template_studio_render_requires_single_host_local_topology():
    settings = _render_settings(
        TEMPLATE_STUDIO_RENDER_STORAGE_TOPOLOGY="distributed"
    )
    with pytest.raises(ValueError, match="single_host_local"):
        validate_template_studio_settings(settings)


def test_template_studio_render_rejects_invalid_manifests_and_profiles():
    settings = _render_settings(TEMPLATE_STUDIO_RENDER_MANIFESTS_JSON="not-json")
    with pytest.raises(ValueError, match="MANIFESTS_JSON"):
        validate_template_studio_settings(settings)

    import json

    settings = _render_settings(
        TEMPLATE_STUDIO_RENDER_PROFILES_JSON=json.dumps({"wrong-key": {}})
    )
    with pytest.raises(ValueError, match="PROFILES_JSON"):
        validate_template_studio_settings(settings)


def test_studio_render_paths_reject_overlap_and_invalid_roots():
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.gettempdir())
    settings = _render_settings()
    validate_studio_render_paths(settings, require_workspace=True)

    with pytest.raises(ValueError, match="absolute"):
        validate_studio_render_paths(
            _render_settings(TEMPLATE_STUDIO_RENDER_STORAGE_DIR="relative/path"),
            require_workspace=True,
        )
    with pytest.raises(ValueError, match="filesystem root"):
        root = "C:\\" if Path("C:\\").is_absolute() else "/"
        validate_studio_render_paths(
            _render_settings(TEMPLATE_STUDIO_RENDER_STORAGE_DIR=root),
            require_workspace=True,
        )
    with pytest.raises(ValueError, match="overlaps the UPLOAD_DIR"):
        validate_studio_render_paths(
            _render_settings(
                UPLOAD_DIR=str(tmp / "studio-storage" / "uploads"),
                TEMPLATE_STUDIO_RENDER_STORAGE_DIR=str(tmp / "studio-storage"),
            ),
            require_workspace=True,
        )
    with pytest.raises(ValueError, match="storage and workspace paths must be disjoint"):
        validate_studio_render_paths(
            _render_settings(
                TEMPLATE_STUDIO_RENDER_STORAGE_DIR=str(tmp / "studio" / "storage"),
                TEMPLATE_STUDIO_RENDER_WORKSPACE_DIR=str(
                    tmp / "studio" / "storage" / "workspace"
                ),
            ),
            require_workspace=True,
        )


def test_studio_render_paths_workspace_optional_when_worker_disabled():
    settings = _render_settings(TEMPLATE_STUDIO_RENDER_WORKER_ENABLED=False)
    storage, workspace = validate_studio_render_paths(
        settings, require_workspace=False
    )
    assert workspace is None
    assert storage is not None


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("TEMPLATE_STUDIO_RENDER_MAX_OBJECT_BYTES", 0, "MAX_OBJECT_BYTES"),
        (
            "TEMPLATE_STUDIO_RENDER_MAX_OBJECT_BYTES",
            100 * 1024 * 1024 + 1,
            "MAX_OBJECT_BYTES",
        ),
        (
            "TEMPLATE_STUDIO_RENDER_MAX_INPUT_BINDING_BYTES",
            0,
            "MAX_INPUT_BINDING_BYTES",
        ),
        (
            "TEMPLATE_STUDIO_RENDER_MAX_INPUT_BINDING_BYTES",
            200 * 1024 * 1024,
            "MAX_INPUT_BINDING_BYTES",
        ),
        ("TEMPLATE_STUDIO_RENDER_MAX_DOWNLOAD_BYTES", 0, "MAX_DOWNLOAD_BYTES"),
        (
            "TEMPLATE_STUDIO_RENDER_MAX_DOWNLOAD_BYTES",
            50 * 1024 * 1024,
            "MAX_DOWNLOAD_BYTES",
        ),
        (
            "TEMPLATE_STUDIO_RENDER_RETAINED_BYTE_LIMIT",
            0,
            "RETAINED_BYTE_LIMIT",
        ),
        ("TEMPLATE_STUDIO_RENDER_LIVE_BYTE_LIMIT", 0, "LIVE_BYTE_LIMIT"),
        ("TEMPLATE_STUDIO_RENDER_TENANT_SCAN_BATCH", 0, "TENANT_SCAN_BATCH"),
        (
            "TEMPLATE_STUDIO_RENDER_MAINTENANCE_INTERVAL_SECONDS",
            9,
            "MAINTENANCE_INTERVAL",
        ),
    ],
)
def test_template_studio_render_settings_reject_additional_out_of_bounds_values(
    field, value, expected
):
    settings = _render_settings(**{field: value})
    with pytest.raises(ValueError, match=expected):
        validate_template_studio_settings(settings)


def test_template_studio_render_settings_require_retained_within_live_limits():
    settings = _render_settings(
        TEMPLATE_STUDIO_RENDER_RETAINED_ARTIFACT_LIMIT=200,
        TEMPLATE_STUDIO_RENDER_LIVE_ARTIFACT_LIMIT=100,
    )
    with pytest.raises(ValueError, match="retained limits must fit"):
        validate_template_studio_settings(settings)

    settings = _render_settings(
        TEMPLATE_STUDIO_RENDER_RETAINED_BYTE_LIMIT=200 * 1024**2,
        TEMPLATE_STUDIO_RENDER_LIVE_BYTE_LIMIT=100 * 1024**2,
    )
    with pytest.raises(ValueError, match="retained limits must fit"):
        validate_template_studio_settings(settings)


def test_template_studio_render_metadata_ttl_must_outlive_artifact_ttl():
    settings = _render_settings(
        TEMPLATE_STUDIO_RENDER_ARTIFACT_TTL_SECONDS=86_400,
        TEMPLATE_STUDIO_RENDER_METADATA_TTL_SECONDS=86_400,
    )
    with pytest.raises(ValueError, match="metadata TTL must outlive"):
        validate_template_studio_settings(settings)


def test_template_studio_render_rejects_malformed_profiles_json():
    settings = _render_settings(TEMPLATE_STUDIO_RENDER_PROFILES_JSON="{broken")
    with pytest.raises(ValueError, match="PROFILES_JSON"):
        validate_template_studio_settings(settings)
