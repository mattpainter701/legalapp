from functools import lru_cache
import json
from datetime import datetime, timezone

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    # Size these together with BACKEND_WORKERS. The production example keeps
    # the total API + scheduler ceiling below PostgreSQL's common 100 clients.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    DATABASE_POOL_TIMEOUT_SECONDS: float = 15.0
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    # Short-lived access token; pair with rotating refresh tokens (see auth router).
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Refresh-token lifetime. Refresh tokens are rotating + single-use; Redis
    # retains consumed-token family tombstones only through original expiry so
    # replay can revoke the live family across every worker.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # ── Auth cookies ─────────────────────────────────────────────────────────
    # When unset (None), Secure/SameSite are derived from BACKEND_URL scheme.
    COOKIE_SECURE: bool | None = None
    COOKIE_SAMESITE: str = "lax"  # lax | strict | none
    # New tenant creation is operator/invite-only for the first-customer
    # release. Do not enable until trial expiry and paid conversion are enforced.
    PUBLIC_SIGNUP_ENABLED: bool = False

    # Guided sales demo. Settings are cached at process startup, so rotating the
    # access code requires an API restart (but no code change).
    DEMO_MODE_ENABLED: bool = False
    DEMO_ACCESS_CODE: str = ""
    DEMO_FIXTURE_TENANT_DOMAIN: str = ""
    DEMO_SESSION_TTL_HOURS: int = 72
    DEMO_MESSAGE_QUOTA: int = 20
    DEMO_MAX_ACTIVE: int = 5

    # ── Background scheduler ─────────────────────────────────────────────────
    # APScheduler must run in EXACTLY ONE process. In prod, API workers set this
    # to False and a single dedicated scheduler container sets it to True. Jobs
    # also take a Postgres advisory lock so a stray second runner cannot double-fire.
    RUN_SCHEDULER: bool = True
    HEALTH_DISK_MAX_PERCENT: int = 90
    # Production receives a non-sensitive aggregate from a host timer through
    # one dedicated read-only mount. Empty keeps local/dev readiness unchanged.
    HOST_DISK_STATUS_FILE: str = ""
    HEALTH_HOST_DISK_MAX_AGE_SECONDS: int = 180
    HEALTH_SCHEDULER_MAX_AGE_MINUTES: int = 5
    HEALTH_QUEUE_MAX_AGE_MINUTES: int = 15

    # ── Reverse proxy / rate limiting ────────────────────────────────────────
    # Number of trusted proxy hops in front of the app (e.g. nginx = 1). The
    # client IP is taken as the Nth-from-rightmost X-Forwarded-For entry so a
    # client cannot spoof its rate-limit identity by sending its own XFF header.
    TRUSTED_PROXY_HOPS: int = 1

    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"

    # Microsoft 365 Office add-in. Fail closed until the Entra exposed API
    # scope and production manifests are configured.
    OFFICE_ASSISTANT_ENABLED: bool = False
    # Comma-separated LawHand tenant UUIDs. Empty or malformed denies every
    # tenant even when the global switch is enabled.
    OFFICE_ASSISTANT_PILOT_TENANT_IDS: str = ""
    OFFICE_ENTRA_CLIENT_ID: str = ""
    OFFICE_ENTRA_API_AUDIENCE: str = ""
    OFFICE_ENTRA_REQUIRED_SCOPE: str = "office.access"
    OFFICE_PLAN_TTL_SECONDS: int = 300
    OFFICE_MAX_WORD_CHARACTERS: int = 50_000
    OFFICE_MAX_EXCEL_CELLS: int = 2_500
    OFFICE_MAX_OUTLOOK_CHARACTERS: int = 50_000

    # ── Microsoft Teams ──────────────────────────────────────────────────────
    # Master feature flag for Teams collaboration features (channel linking,
    # outbound Adaptive Card notifications). When False the Teams admin tab and
    # all /api/integrations/teams endpoints are gated off regardless of consent.
    TEAMS_FEATURE_ENABLED: bool = True
    # GUID of the published LawHand Teams app (manifest "id"). Used to
    # build channel/tab deep links. Empty until the shared app is published.
    TEAMS_APP_ID: str = ""

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # ── Zoom OAuth / meeting provider ────────────────────────────────────────
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    ZOOM_REDIRECT_URI: str = (
        ""  # e.g. https://yourdomain.com/api/integrations/zoom/callback
    )
    ZOOM_PHONE_REDIRECT_URI: str = (
        ""  # e.g. https://yourdomain.com/api/integrations/zoom-phone/callback
    )
    # Least-privilege scopes required for account call history and call-element
    # detail. Recording content is not fetched by the intake integration.
    ZOOM_PHONE_SCOPES: str = "phone:read:list_call_logs:admin phone:read:call_log:admin"

    # Token encryption key for OAuth tokens at rest (Fernet symmetric)
    # Required: base64-encoded Fernet key (generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    TOKEN_ENCRYPTION_KEY: str = ""
    # Staged Fernet rotation keyring, newest key first. New values use the
    # first key while decryption accepts every listed key.
    TOKEN_ENCRYPTION_KEYS: str = ""

    # Azure OpenAI (Copilot backend)
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_KEY: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = ""

    # Google Gemini
    GEMINI_API_KEY: str = ""

    # Google Workspace — domain-wide delegation service account
    GOOGLE_SERVICE_ACCOUNT_EMAIL: str = ""
    GOOGLE_SERVICE_ACCOUNT_KEY: str = ""  # JSON key or path

    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    OPENCODE_KEY: str = ""  # alias accepted alongside DEEPSEEK_API_KEY
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    ANTHROPIC_API_KEY: str = ""

    # OpenRouter — free model access (OpenAI-compatible)
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
    # Comma-separated list of free models available via OpenRouter
    OPENROUTER_FREE_MODELS: str = (
        "google/gemma-4-31b-it:free,"
        "meta-llama/llama-4-maverick:free,"
        "deepseek/deepseek-r1:free,"
        "qwen/qwen3-235b-a22b:free"
    )

    # OpenCode Zen — free-tier LLM access (OpenAI-compatible)
    OPENCODE_ZEN_BASE_URL: str = "https://zen.opencode.ai/v1"

    # LiteLLM Gateway — primary OpenAI-compatible model router
    LITELLM_ENABLED: bool = False
    LITELLM_BASE_URL: str = "http://litellm:4000"
    LITELLM_API_KEY: str = ""
    LITELLM_STANDARD_MODEL: str = "clarity-standard"
    LITELLM_PREMIUM_MODEL: str = "clarity-premium"
    LITELLM_EMBEDDING_MODEL: str = ""
    LITELLM_DB_PASSWORD: str = ""
    LITELLM_DATABASE_URL: str = ""
    GATEWAY_RAW_TEXT_RETENTION_ENABLED: bool = False
    GATEWAY_LOG_RETENTION_DAYS: int = 30
    GATEWAY_DEBUG_LOG_RETENTION_DAYS: int = 7
    GATEWAY_SPEND_LOG_RETENTION_DAYS: int = 365

    # QuickBooks Online OAuth2
    QBO_CLIENT_ID: str = ""
    QBO_CLIENT_SECRET: str = ""
    QBO_REDIRECT_URI: str = (
        ""  # e.g. https://yourdomain.com/api/integrations/qbo/callback
    )
    QBO_ENVIRONMENT: str = "sandbox"  # "sandbox" | "production"

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID: str = ""  # Stripe Price ID for flat subscription
    STRIPE_MCP_METER_EVENT_NAME: str = (
        ""  # Stripe Billing Meter event name for MCP product-key calls
    )
    STRIPE_SUCCESS_URL: str = ""  # e.g. https://yourdomain.com/billing?success=1
    STRIPE_CANCEL_URL: str = ""  # e.g. https://yourdomain.com/billing?cancel=1

    # Super-admin platform key — set a long random token; never commit
    # Leave unset on new deployments; this only backs the time-boxed legacy
    # bootstrap bridge when explicitly enabled below.
    PLATFORM_SECRET_KEY: str = ""
    # Identity-bound, scope-capped SHA-256 bootstrap entries (JSON list).
    PLATFORM_BOOTSTRAP_CREDENTIALS_JSON: str = "[]"
    # Separate signing key for short-lived operator session tokens.
    PLATFORM_TOKEN_SIGNING_KEY: str = ""
    # Time-boxed migration bridge for the legacy static bootstrap key.
    PLATFORM_LEGACY_BOOTSTRAP_ENABLED: bool = False
    PLATFORM_LEGACY_BOOTSTRAP_OPERATOR_ID: str = ""
    PLATFORM_LEGACY_BOOTSTRAP_EXPIRES_AT: str = ""
    PLATFORM_LEGACY_BOOTSTRAP_MAX_SCOPES: str = "platform:read"
    PLATFORM_TOKEN_TTL_MINUTES: int = 15
    PLATFORM_TOKEN_MAX_TTL_MINUTES: int = 60
    PLATFORM_RATE_LIMIT_PER_MINUTE: int = 120
    PLATFORM_BOOTSTRAP_LIMIT_PER_5_MINUTES: int = 5

    # Optional separate vectorDB for public CourtListener chunks (BGE embeddings)
    # If empty, public_chunks table lives in main DATABASE_URL
    VECTORDB_URL: str = ""
    MCP_SERVER_URL: str = ""
    # Public/sellable MCP is fail-closed until every product, protocol and
    # operational release gate has passed. Internal research is independent.
    MCP_PRODUCT_ENABLED: bool = False
    # Dedicated backend-to-CourtListener credential. User/app credentials are
    # never forwarded to the private service.
    MCP_UPSTREAM_API_KEY: str = ""
    MCP_DEFAULT_MONTHLY_CALL_LIMIT: int = 1000
    MCP_MAX_MONTHLY_CALL_LIMIT: int = 100000
    MCP_DEFAULT_BURST_LIMIT_PER_MINUTE: int = 60
    MCP_MAX_BURST_LIMIT_PER_MINUTE: int = 600

    # Per-add-on entitlement enforcement for /api/plugins skills.
    #
    # Explicitly revoked states (disabled/locked) and lapsed expiry dates are
    # ALWAYS enforced. This flag governs only the absent-row case: no tenant
    # has ever been provisioned an entitlement row, so defaulting to strict
    # would revoke every add-on for every existing tenant on deploy. Turn it on
    # once tenants carry real entitlements.
    PLUGIN_ENTITLEMENT_STRICT: bool = False
    # Trials started from the product UI carry no expiry in the request, so the
    # server assigns one. Without this every UI-started trial runs forever.
    PLUGIN_TRIAL_DEFAULT_DAYS: int = 14

    FRONTEND_URL: str = "http://localhost:3000"
    # OAuth callbacks must point to the backend, not the frontend.
    # In prod behind nginx both URLs share the same domain so set this to
    # https://yourdomain.com. In dev set to http://localhost:8000.
    BACKEND_URL: str = "http://localhost:8000"
    # Extra CORS origins (comma-separated); added to defaults (FRONTEND_URL, localhost)
    EXTRA_CORS_ORIGINS: str = ""
    UPLOAD_DIR: str = "/app/uploads"
    MAX_FILE_SIZE_MB: int = 50
    # Rolling retention window for misc-chat (non-matter) attachments stored in
    # UPLOAD_DIR/{tenant_id}/chat-temp/. Matter-linked chat attachments persist.
    CHAT_ATTACHMENT_TTL_DAYS: int = 7

    RAG_TOP_K: int = 8
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536
    PUBLIC_RAG_TOP_K: int = 8
    PUBLIC_EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"

    # Primary: DeepSeek V4 Flash once released; current OpenAI-compat alias is deepseek-chat
    # Set PRIMARY_LLM=deepseek-v4-flash in .env when V4 Flash ships
    PRIMARY_LLM: str = "deepseek-chat"
    # Premium: Claude Opus 4 for complex drafting tasks
    PREMIUM_LLM: str = "claude-opus-4-8"

    # ── Email / Notifications ─────────────────────────────────────────────────
    # Disabled delivery is a typed non-success; it never simulates a sent email.
    EMAIL_ENABLED: bool = False
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASS: str = ""
    EMAIL_FROM: str = "matt@cybersafeadvisor.com"
    SLACK_WEBHOOK_URL: str = ""  # Optional: Slack incoming webhook URL

    # Never set True in production — enables /dev/* endpoints
    DEV_MODE: bool = False

    # Build/deploy metadata surfaced through /api/version and /health so testers
    # can identify exactly which pushed revision is running.
    APP_VERSION: str = "dev"
    APP_COMMIT: str = ""
    APP_BUILD_TIME: str = ""

    # ── Cloud Search (Live RAG) ──────────────────────────────────────────────
    CLOUD_SEARCH_ENABLED: bool = True  # Master feature flag
    CLOUD_SEARCH_MAX_HITS: int = 10  # Cap results per source
    CLOUD_SEARCH_HIT_CONTENT_CHARS: int = 2000  # Max chars per fetched hit
    CLOUD_SEARCH_CACHE_TTL: int = 300  # 5 min for search results
    CLOUD_RETRIEVAL_PLANNER_TIMEOUT_SECONDS: float = 3.0
    CLOUD_METADATA_SYNC_INTERVAL_MIN: int = 15  # Cron interval

    # ── Email Correspondence Capture ─────────────────────────────────────────
    CORRESPONDENCE_CAPTURE_ENABLED: bool = False  # Master flag for the scheduled job
    CORRESPONDENCE_CAPTURE_INTERVAL_MIN: int = 30  # Background scan interval
    CORRESPONDENCE_CAPTURE_MAX_EMAILS: int = 50  # Per mailbox per run

    # ── SMB File Share Relay Agent ──────────────────────────────────────────
    SMB_ENABLED: bool = False  # Master feature flag
    SMB_PAIRING_CODE_TTL_MIN: int = 10  # Pairing code expiry in minutes
    SMB_MAX_FILE_INDEX_PER_SHARE: int = 500  # Cap files per share
    SMB_SNIPPET_MAX_CHARS: int = 500  # Max chars in snippet column
    SMB_TASK_POLL_INTERVAL: int = 30  # Seconds between agent task polls
    SMB_CONTENT_FETCH_TIMEOUT: int = 120  # Seconds to wait for content fetch

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def validate_token_encryption_key(settings: Settings) -> None:
    """Validate TOKEN_ENCRYPTION_KEY is set and valid. Called at startup."""
    try:
        from cryptography.fernet import Fernet

        keys = [
            item.strip()
            for item in settings.TOKEN_ENCRYPTION_KEYS.replace("\n", ",").split(",")
            if item.strip()
        ]
        if not keys and settings.TOKEN_ENCRYPTION_KEY:
            keys = [settings.TOKEN_ENCRYPTION_KEY]
        if not keys:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEYS or TOKEN_ENCRYPTION_KEY is required but not set. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        for key in keys:
            Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as e:
        raise ValueError(f"TOKEN_ENCRYPTION_KEY must be a valid Fernet key: {e}") from e
    except Exception as e:
        raise ValueError(f"TOKEN_ENCRYPTION_KEY must be a valid Fernet key: {e}") from e


# Substrings that indicate a secret was left at its template/placeholder value
# rather than replaced with a real random value. Every JWT in the system is
# forgeable if SECRET_KEY matches one of these, so this must be fatal at boot.
# NOTE: keep this list to markers that only appear in *unfilled* templates —
# not generic words like "secret-key", which legitimately appears inside the
# documented local test fixture value (see memory/backend-test-env.md) and
# would false-positive on a perfectly fine random-enough dev/test secret.
_PLACEHOLDER_SECRET_MARKERS = (
    "changeme",
    "change-me",
    "change_me",
    "change-this",
    "change_this",
    "replace-this",
    "replace_this",
    "your-secret",
    "yoursecret",
    "<todo>",
    "insert-secret",
    "insert_secret",
    "generate-with-openssl",
    "generate_with_openssl",
    "managed-outside-git",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_SECRET_MARKERS)


def validate_secret_key(settings: Settings) -> None:
    """Validate SECRET_KEY is present, long enough, and not a template placeholder.

    SECRET_KEY signs every access/refresh JWT and the mediation-portal magic-link
    tokens. A short or placeholder value (e.g. a template's "change-me-in-prod")
    lets an attacker forge tokens for any user/tenant, including admins — this
    must fail closed at startup rather than log a warning.
    """
    key = settings.SECRET_KEY
    if not key or len(key) < 32:
        raise ValueError(
            "SECRET_KEY must be set to a random value of at least 32 characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if _looks_like_placeholder(key):
        raise ValueError(
            "SECRET_KEY appears to be a template placeholder value, not a real "
            "secret. Generate a real one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


def validate_jwt_algorithm(settings: Settings) -> None:
    """Keep application session tokens on the single reviewed HMAC profile."""
    if settings.ALGORITHM != "HS256":
        raise ValueError(
            "ALGORITHM must be exactly HS256 for application session tokens"
        )


def validate_demo_settings(settings: Settings) -> None:
    """Fail closed before exposing a session-minting public demo endpoint."""
    if not settings.DEMO_MODE_ENABLED:
        return

    access_code = settings.DEMO_ACCESS_CODE.strip()
    if len(access_code) < 16 or _looks_like_placeholder(access_code):
        raise ValueError(
            "DEMO_ACCESS_CODE must be a non-placeholder value of at least 16 characters"
        )
    if not settings.DEMO_FIXTURE_TENANT_DOMAIN.strip():
        raise ValueError(
            "DEMO_FIXTURE_TENANT_DOMAIN is required when demo mode is enabled"
        )
    if not 1 <= settings.DEMO_SESSION_TTL_HOURS <= 168:
        raise ValueError("DEMO_SESSION_TTL_HOURS must be between 1 and 168")
    if not 1 <= settings.DEMO_MESSAGE_QUOTA <= 100:
        raise ValueError("DEMO_MESSAGE_QUOTA must be between 1 and 100")
    if not 1 <= settings.DEMO_MAX_ACTIVE <= 25:
        raise ValueError("DEMO_MAX_ACTIVE must be between 1 and 25")


def validate_platform_secret_key(settings: Settings) -> None:
    """Validate the optional, time-boxed legacy bootstrap bridge secret."""
    key = settings.PLATFORM_SECRET_KEY
    if not key:
        if settings.PLATFORM_LEGACY_BOOTSTRAP_ENABLED:
            raise ValueError(
                "PLATFORM_SECRET_KEY is required while the legacy bootstrap bridge is enabled"
            )
        return
    if len(key) < 32:
        raise ValueError(
            "PLATFORM_SECRET_KEY must be at least 32 characters (or unset to "
            "disable platform operator endpoints)."
        )
    if _looks_like_placeholder(key):
        raise ValueError(
            "PLATFORM_SECRET_KEY appears to be a template placeholder value, not "
            "a real secret."
        )

    if settings.PLATFORM_LEGACY_BOOTSTRAP_ENABLED:
        if not settings.PLATFORM_LEGACY_BOOTSTRAP_OPERATOR_ID.strip():
            raise ValueError(
                "PLATFORM_LEGACY_BOOTSTRAP_OPERATOR_ID is required while the legacy bridge is enabled"
            )
        expiry = _parse_platform_expiry(
            settings.PLATFORM_LEGACY_BOOTSTRAP_EXPIRES_AT,
            "PLATFORM_LEGACY_BOOTSTRAP_EXPIRES_AT",
        )
        if expiry <= datetime.now(timezone.utc):
            raise ValueError("The legacy platform bootstrap bridge has expired")


_PLATFORM_SCOPES = {
    "platform:read",
    "platform:write",
    "platform:llm:read",
    "platform:llm:write",
}


def _parse_platform_expiry(value: str, field: str) -> datetime:
    if not value:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_platform_bootstrap_settings(settings: Settings) -> None:
    try:
        entries = json.loads(settings.PLATFORM_BOOTSTRAP_CREDENTIALS_JSON or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(
            "PLATFORM_BOOTSTRAP_CREDENTIALS_JSON must be valid JSON"
        ) from exc
    if not isinstance(entries, list):
        raise ValueError("PLATFORM_BOOTSTRAP_CREDENTIALS_JSON must be a JSON list")
    seen_hashes: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Platform bootstrap entry {index} must be an object")
        operator_id = str(entry.get("operator_id") or "").strip()
        key_hash = str(entry.get("key_hash") or "")
        scopes = entry.get("scopes")
        if not operator_id or len(operator_id) > 120:
            raise ValueError(
                f"Platform bootstrap entry {index} has an invalid operator_id"
            )
        if len(key_hash) != 64 or any(ch not in "0123456789abcdef" for ch in key_hash):
            raise ValueError(
                f"Platform bootstrap entry {index} has an invalid key_hash"
            )
        if (
            not isinstance(scopes, list)
            or not scopes
            or not all(isinstance(scope, str) for scope in scopes)
            or set(scopes) - _PLATFORM_SCOPES
        ):
            raise ValueError(f"Platform bootstrap entry {index} has invalid scopes")
        if key_hash in seen_hashes:
            raise ValueError("Platform bootstrap key hashes must be unique")
        seen_hashes.add(key_hash)
        _parse_platform_expiry(
            str(entry.get("expires_at") or ""),
            f"platform bootstrap entry {index} expires_at",
        )

    has_bootstrap = bool(entries) or settings.PLATFORM_LEGACY_BOOTSTRAP_ENABLED
    if settings.PLATFORM_LEGACY_BOOTSTRAP_ENABLED:
        legacy_scopes = {
            item.strip()
            for item in settings.PLATFORM_LEGACY_BOOTSTRAP_MAX_SCOPES.split(",")
            if item.strip()
        }
        if not legacy_scopes or legacy_scopes - _PLATFORM_SCOPES:
            raise ValueError("PLATFORM_LEGACY_BOOTSTRAP_MAX_SCOPES is invalid")
    if has_bootstrap:
        signing_key = settings.PLATFORM_TOKEN_SIGNING_KEY
        if len(signing_key) < 32 or _looks_like_placeholder(signing_key):
            raise ValueError(
                "PLATFORM_TOKEN_SIGNING_KEY must be a distinct random value of at least 32 characters"
            )
        if signing_key == settings.PLATFORM_SECRET_KEY:
            raise ValueError(
                "PLATFORM_TOKEN_SIGNING_KEY must not equal PLATFORM_SECRET_KEY"
            )


def validate_mcp_security_settings(settings: Settings) -> None:
    if settings.MCP_SERVER_URL and len(settings.MCP_UPSTREAM_API_KEY) < 32:
        raise ValueError(
            "MCP_UPSTREAM_API_KEY must be at least 32 characters whenever "
            "MCP_SERVER_URL is configured"
        )
    if (
        not 1
        <= settings.MCP_DEFAULT_MONTHLY_CALL_LIMIT
        <= settings.MCP_MAX_MONTHLY_CALL_LIMIT
    ):
        raise ValueError("Invalid MCP monthly limit defaults")
    if (
        not 1
        <= settings.MCP_DEFAULT_BURST_LIMIT_PER_MINUTE
        <= settings.MCP_MAX_BURST_LIMIT_PER_MINUTE
    ):
        raise ValueError("Invalid MCP burst limit defaults")
    if (
        not 1
        <= settings.PLATFORM_TOKEN_TTL_MINUTES
        <= settings.PLATFORM_TOKEN_MAX_TTL_MINUTES
    ):
        raise ValueError("Invalid platform token TTL defaults")


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    validate_token_encryption_key(settings)
    validate_secret_key(settings)
    validate_jwt_algorithm(settings)
    validate_demo_settings(settings)
    validate_platform_secret_key(settings)
    validate_platform_bootstrap_settings(settings)
    validate_mcp_security_settings(settings)
    return settings
