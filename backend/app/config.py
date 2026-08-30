import base64
from functools import lru_cache
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

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
    DEMO_MAX_ACTIVE: int = 10

    # ── Background scheduler ─────────────────────────────────────────────────
    # APScheduler must run in EXACTLY ONE process. In prod, API workers set this
    # to False and a single dedicated scheduler container sets it to True. Jobs
    # also take a Postgres advisory lock so a stray second runner cannot double-fire.
    RUN_SCHEDULER: bool = True
    # General durable work stays sequential within each tenant, but unrelated
    # tenants may progress in parallel up to this process-wide bound.
    DURABLE_JOB_TENANT_CONCURRENCY: int = 4
    HEALTH_DISK_MAX_PERCENT: int = 90
    # Production receives a non-sensitive aggregate from a host timer through
    # one dedicated read-only mount. Empty keeps local/dev readiness unchanged.
    HOST_DISK_STATUS_FILE: str = ""
    HEALTH_HOST_DISK_MAX_AGE_SECONDS: int = 180
    # Written atomically only after a complete encrypted off-site backup and
    # mounted read-only beside the host disk status. Empty keeps local/dev
    # readiness independent of production backup infrastructure.
    BACKUP_STATUS_FILE: str = ""
    HEALTH_BACKUP_MAX_AGE_SECONDS: int = 7200
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
    # Canonical OpenCode names. DEEPSEEK_API_KEY remains a deployment
    # compatibility alias until existing production secrets are rotated.
    OPENCODE_GO_API_KEY: str = ""
    OPENCODE_ZEN_API_KEY: str = ""
    OPENCODE_API_KEY: str = ""  # legacy Zen alias
    OPENCODE_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    ANTHROPIC_API_KEY: str = ""

    # Template OCR is local by default. Azure is an explicit opt-in because
    # document bytes may contain privileged client information.
    TEMPLATE_OCR_PROVIDER: str = "local"
    # RapidOCR sessions are not shared across concurrent inference. A small
    # bounded pool prevents one long scan from serializing every intake while
    # keeping model memory predictable on production hosts.
    TEMPLATE_OCR_LOCAL_CONCURRENCY: int = 2
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: str = ""
    AZURE_DOCUMENT_INTELLIGENCE_KEY: str = ""
    AZURE_DOCUMENT_INTELLIGENCE_API_VERSION: str = "2024-11-30"
    TEMPLATE_OCR_AZURE_TIMEOUT_SECONDS: float = 30.0
    TEMPLATE_OCR_AZURE_MAX_POLL_SECONDS: float = 75.0
    TEMPLATE_OCR_AZURE_MAX_POLL_INTERVAL_SECONDS: float = 10.0

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
    OPENCODE_ZEN_BASE_URL: str = "https://opencode.ai/zen/v1"

    # LiteLLM Gateway — primary OpenAI-compatible model router
    LITELLM_ENABLED: bool = False
    LITELLM_BASE_URL: str = "http://litellm:4000"
    LITELLM_API_KEY: str = ""
    LITELLM_STANDARD_MODEL: str = "clarity-standard"
    LITELLM_PREMIUM_MODEL: str = "clarity-premium"
    # Platform-owned route for scheduled/event-driven assistant work. This is
    # deliberately separate from tenant Standard/Premium profiles and BYOK.
    LITELLM_BACKGROUND_MODEL: str = "clarity-background"
    LITELLM_BACKGROUND_TRANSPORT: str = "responses"
    LITELLM_EMBEDDING_MODEL: str = ""
    LITELLM_DB_PASSWORD: str = ""
    LITELLM_DATABASE_URL: str = ""
    GATEWAY_RAW_TEXT_RETENTION_ENABLED: bool = False
    GATEWAY_LOG_RETENTION_DAYS: int = 30
    GATEWAY_DEBUG_LOG_RETENTION_DAYS: int = 7
    GATEWAY_SPEND_LOG_RETENTION_DAYS: int = 365

    # ── LawHand Assistant launch wedge ──────────────────────────────────────
    # These switches fail closed. Building/deploying the code does not activate
    # customer-content inference or prospect-facing background work.
    VIRTUAL_ASSISTANT_ENABLED: bool = False
    AFTER_CALL_CONCIERGE_ENABLED: bool = False
    ENGAGEMENT_PACKETS_ENABLED: bool = False
    BACKGROUND_ASSISTANT_ENABLED: bool = False
    BACKGROUND_PROSPECT_CONFIDENTIAL_ENABLED: bool = False
    BACKGROUND_MATTER_CONFIDENTIAL_ENABLED: bool = False
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    BACKGROUND_AI_POOL: str = "background-default"
    BACKGROUND_AI_ACCOUNT_FIVE_HOUR_LIMIT: int = 2050
    BACKGROUND_AI_ACCOUNT_WEEKLY_LIMIT: int = 5100
    BACKGROUND_AI_ACCOUNT_MONTHLY_LIMIT: int = 10250
    BACKGROUND_AI_TENANT_FIVE_HOUR_LIMIT: int = 250
    BACKGROUND_AI_TENANT_WEEKLY_LIMIT: int = 750
    BACKGROUND_AI_TENANT_MONTHLY_LIMIT: int = 1500
    BACKGROUND_AI_RESERVATION_TTL_MINUTES: int = 15
    # Authoritative pool budget. The provider meters value, not calls, so these
    # windows are what admission enforces; the request limits above remain a
    # coarse backstop. Defaults mirror the published OpenCode Go value windows.
    BACKGROUND_AI_ACCOUNT_FIVE_HOUR_USD: float = 12.0
    BACKGROUND_AI_ACCOUNT_WEEKLY_USD: float = 30.0
    BACKGROUND_AI_ACCOUNT_MONTHLY_USD: float = 60.0
    BACKGROUND_AI_TENANT_FIVE_HOUR_USD: float = 3.0
    BACKGROUND_AI_TENANT_WEEKLY_USD: float = 8.0
    BACKGROUND_AI_TENANT_MONTHLY_USD: float = 15.0
    # Reconciliation of reservations whose outcome the provider never confirmed.
    BACKGROUND_AI_RECONCILE_GRACE_MINUTES: int = 10
    BACKGROUND_AI_RECONCILE_MAX_AGE_HOURS: int = 24
    BACKGROUND_AI_RECONCILE_BATCH: int = 100
    BACKGROUND_AI_RECONCILE_LOOKUP_TIMEOUT_SECONDS: float = 10.0

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

    # Certified e-sign provider. Empty means external signing is unavailable;
    # the API must fail closed instead of silently using the internal flow.
    DROPBOX_SIGN_API_KEY: str = ""
    ESIGN_WEBHOOK_SECRET: str = ""
    ESIGN_PROVIDER_BASE_URL: str = "https://api.hellosign.com/v3"

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
    # Canonical public research transport. Empty preserves the historical
    # BACKEND_URL/api/mcp endpoint for non-LawHand/self-hosted deployments.
    RESEARCH_MCP_PUBLIC_URL: str = ""
    # Dedicated backend-to-CourtListener credential. User/app credentials are
    # never forwarded to the private service.
    MCP_UPSTREAM_API_KEY: str = ""
    # Separate short-lived operator assertion signer; never reuse the service
    # transport key for actor/scope authorization claims.
    MCP_OPERATOR_ASSERTION_SECRET: str = ""
    MCP_DEFAULT_MONTHLY_CALL_LIMIT: int = 1000
    MCP_MAX_MONTHLY_CALL_LIMIT: int = 100000
    # Customer-facing Research price. Stripe owns invoice calculation, while
    # this value snapshots the price onto each key for budgets and portal
    # estimates. One successful tool call is currently $0.45.
    MCP_PRODUCT_CALL_PRICE_CENTS: int = 45
    MCP_DEFAULT_BURST_LIMIT_PER_MINUTE: int = 60
    MCP_MAX_BURST_LIMIT_PER_MINUTE: int = 600
    # Protocol lifecycle traffic is limited independently from per-tool burst
    # and monthly product metering so discovery/initialize cannot evade limits.
    RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE: int = 240
    RESEARCH_MCP_TENANT_REQUESTS_PER_MINUTE: int = 2400
    # OAuth 2.1 for interactive Research MCP clients (ChatGPT/Claude).  The
    # issuer and signing key-ring are shared with Workspace MCP, while the
    # audience, token type, resource and durable grant are independently bound.
    RESEARCH_MCP_OAUTH_ENABLED: bool = True
    RESEARCH_MCP_AUDIENCE: str = "lawhand-research-mcp"
    RESEARCH_MCP_ISSUER: str = ""
    RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES: int = 60
    RESEARCH_MCP_AUTH_CODE_TTL_SECONDS: int = 300
    RESEARCH_MCP_REFRESH_TOKEN_DAYS: int = 30
    RESEARCH_MCP_GRANT_DAYS: int = 90
    RESEARCH_MCP_CLIENT_REGISTRATION_DAYS: int = 30
    RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED: bool = True
    # Streamable HTTP requests are capped independently of reverse-proxy
    # settings so a direct/internal route cannot feed unbounded JSON to the
    # protocol SDK. Tool inputs are structured and should remain compact.
    MCP_PROTOCOL_MAX_REQUEST_BYTES: int = 262144
    # User-bound workspace MCP is a separate product from the research-key
    # gateway. It stays off until an OAuth issuer can mint audience-bound,
    # revocable grants for individual LawHand users.
    WORKSPACE_MCP_ENABLED: bool = False
    WORKSPACE_MCP_RESOURCE: str = ""
    # Optional canonical resource used during a hostname migration. When set,
    # WORKSPACE_MCP_RESOURCE remains an accepted legacy resource automatically.
    WORKSPACE_MCP_CANONICAL_RESOURCE: str = ""
    # Additional comma-separated, exact legacy resource identifiers. Aliases
    # are accepted only for OAuth migration and are never newly advertised.
    WORKSPACE_MCP_RESOURCE_ALIASES: str = ""
    WORKSPACE_MCP_AUDIENCE: str = "lawhand-workspace-mcp"
    WORKSPACE_MCP_ISSUER: str = ""
    # Legacy symmetric test key. Production OAuth uses the asymmetric pair.
    WORKSPACE_MCP_TOKEN_SIGNING_KEY: str = ""
    WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64: str = ""
    WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64: str = ""
    WORKSPACE_MCP_SIGNING_KEY_ID: str = ""
    # Retain up to three public verification keys during bounded rotation.
    WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON: str = "[]"
    WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES: int = 60
    WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS: int = 300
    WORKSPACE_MCP_REFRESH_TOKEN_DAYS: int = 30
    WORKSPACE_MCP_GRANT_DAYS: int = 90
    WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS: int = 30
    # Per-token and tenant-aggregate protocol limits cover initialize,
    # discovery, notifications, and tool calls. Nginx separately limits IPs.
    WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE: int = 120
    WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE: int = 1200
    WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED: bool = False

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
    # Roll out only after counsel-owned agreement definitions are published and
    # existing tenants have had an acceptance window. When disabled, status and
    # evidence collection remain available without blocking onboarding/OAuth.
    TENANT_AGREEMENT_GATE_ENABLED: bool = False

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
    EMAIL_FROM: str = "support@getlawhand.com"
    MARKETING_LEAD_EMAIL: str = "support@getlawhand.com"
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

    # ── Inbound Matter Email ─────────────────────────────────────────────────
    # Cloudflare Email Worker sends the original RFC 822 bytes to the signed
    # backend ingress endpoint. Keep the HMAC secret only in the backend secret
    # store and the Worker's encrypted secret binding.
    INBOUND_EMAIL_ENABLED: bool = False
    INBOUND_EMAIL_DOMAIN: str = "intake.getlawhand.com"
    INBOUND_EMAIL_WEBHOOK_SECRET: str = ""
    INBOUND_EMAIL_MAX_BYTES: int = 25 * 1024 * 1024
    INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS: int = 300

    # ── SMB File Share Relay Agent ──────────────────────────────────────────
    SMB_ENABLED: bool = False  # Master feature flag
    SMB_PAIRING_CODE_TTL_MIN: int = 10  # Pairing code expiry in minutes
    # Metadata-only rows are small; 500 made ordinary legal shares silently
    # partial. Keep a configurable safety ceiling high enough for real firms.
    SMB_MAX_FILE_INDEX_PER_SHARE: int = 250_000
    SMB_SNIPPET_MAX_CHARS: int = 500  # Max chars in snippet column
    SMB_TASK_POLL_INTERVAL: int = 30  # Seconds between agent task polls
    SMB_CONTENT_FETCH_TIMEOUT: int = 120  # Seconds to wait for content fetch
    SMB_AGENT_MANIFEST_CACHE_SECONDS: int = 300

    @property
    def research_mcp_endpoint(self) -> str:
        configured = self.RESEARCH_MCP_PUBLIC_URL.strip()
        if configured:
            return configured.rstrip("/")
        return f"{self.BACKEND_URL.rstrip('/')}/api/mcp"

    @property
    def research_mcp_shorthand(self) -> str:
        parsed = urlsplit(self.research_mcp_endpoint)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return self.research_mcp_endpoint

    @property
    def workspace_mcp_endpoint(self) -> str:
        configured = self.WORKSPACE_MCP_CANONICAL_RESOURCE.strip()
        return (configured or self.WORKSPACE_MCP_RESOURCE.strip()).rstrip("/")

    @property
    def workspace_mcp_legacy_resources(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.WORKSPACE_MCP_CANONICAL_RESOURCE.strip():
            values.append(self.WORKSPACE_MCP_RESOURCE.strip())
        values.extend(
            item.strip() for item in self.WORKSPACE_MCP_RESOURCE_ALIASES.split(",")
        )
        return tuple(item.rstrip("/") for item in values if item)

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


def _decoded_workspace_pem(value: str, field: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(f"{field} must be valid base64") from exc
    if not decoded:
        raise ValueError(f"{field} must not be empty")
    return decoded


def _validate_workspace_signing_keys(settings: Settings) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_pem = _decoded_workspace_pem(
        settings.WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64,
        "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64",
    )
    public_pem = _decoded_workspace_pem(
        settings.WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64,
        "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64",
    )
    try:
        private_key = serialization.load_pem_private_key(private_pem, password=None)
        public_key = serialization.load_pem_public_key(public_pem)
    except Exception as exc:
        raise ValueError("Workspace MCP signing keys must be valid PEM keys") from exc
    if not isinstance(private_key, rsa.RSAPrivateKey) or not isinstance(
        public_key, rsa.RSAPublicKey
    ):
        raise ValueError("Workspace MCP signing keys must be RSA keys")
    if private_key.key_size < 2048 or public_key.key_size < 2048:
        raise ValueError("Workspace MCP signing keys must be at least 2048 bits")
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        raise ValueError("Workspace MCP signing private/public keys do not match")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", settings.WORKSPACE_MCP_SIGNING_KEY_ID):
        raise ValueError("WORKSPACE_MCP_SIGNING_KEY_ID is invalid")

    try:
        previous = json.loads(settings.WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(
            "WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON must be valid JSON"
        ) from exc
    if not isinstance(previous, list) or len(previous) > 3:
        raise ValueError(
            "WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON must list at most 3 keys"
        )
    seen = {settings.WORKSPACE_MCP_SIGNING_KEY_ID}
    for entry in previous:
        if not isinstance(entry, dict):
            raise ValueError("Previous workspace MCP signing keys must be objects")
        kid = str(entry.get("kid") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", kid) or kid in seen:
            raise ValueError("Previous workspace MCP signing key IDs must be unique")
        seen.add(kid)
        try:
            prior_key = serialization.load_pem_public_key(
                _decoded_workspace_pem(
                    str(entry.get("public_key_b64") or ""),
                    "previous workspace MCP public key",
                )
            )
        except Exception as exc:
            raise ValueError(
                "Previous workspace MCP public keys must be valid PEM keys"
            ) from exc
        if not isinstance(prior_key, rsa.RSAPublicKey) or prior_key.key_size < 2048:
            raise ValueError(
                "Previous workspace MCP public keys must be RSA-2048 or stronger"
            )


def _validate_mcp_endpoint_url(
    raw_value: str,
    *,
    setting_name: str,
    expected_path: str,
    dev_mode: bool,
) -> None:
    parsed = urlsplit(raw_value.strip())
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.path.rstrip("/") != expected_path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            f"{setting_name} must be an absolute canonical {expected_path} URL"
        )
    if not dev_mode and parsed.scheme != "https":
        raise ValueError(f"{setting_name} must use HTTPS outside development")


def validate_mcp_security_settings(settings: Settings) -> None:
    if settings.MCP_SERVER_URL and len(settings.MCP_UPSTREAM_API_KEY) < 32:
        raise ValueError(
            "MCP_UPSTREAM_API_KEY must be at least 32 characters whenever "
            "MCP_SERVER_URL is configured"
        )
    if settings.MCP_SERVER_URL and len(settings.MCP_OPERATOR_ASSERTION_SECRET) < 32:
        raise ValueError(
            "MCP_OPERATOR_ASSERTION_SECRET must be at least 32 characters whenever "
            "MCP_SERVER_URL is configured"
        )
    if settings.MCP_SERVER_URL and settings.MCP_OPERATOR_ASSERTION_SECRET == settings.MCP_UPSTREAM_API_KEY:
        raise ValueError(
            "MCP_OPERATOR_ASSERTION_SECRET must be distinct from MCP_UPSTREAM_API_KEY"
        )
    if settings.MCP_PRODUCT_ENABLED or settings.RESEARCH_MCP_PUBLIC_URL.strip():
        _validate_mcp_endpoint_url(
            settings.research_mcp_endpoint,
            setting_name="RESEARCH_MCP_PUBLIC_URL",
            expected_path="/api/mcp",
            dev_mode=settings.DEV_MODE,
        )

    if settings.MCP_PRODUCT_ENABLED and settings.RESEARCH_MCP_OAUTH_ENABLED:
        if not settings.RESEARCH_MCP_AUDIENCE.strip():
            raise ValueError(
                "RESEARCH_MCP_AUDIENCE is required when Research MCP OAuth is enabled"
            )
        if not settings.RESEARCH_MCP_ISSUER.strip():
            raise ValueError(
                "RESEARCH_MCP_ISSUER is required when Research MCP OAuth is enabled"
            )
        issuer = urlsplit(settings.RESEARCH_MCP_ISSUER)
        if (
            not issuer.scheme
            or not issuer.netloc
            or issuer.path.rstrip("/")
            or issuer.query
            or issuer.fragment
            or issuer.username
            or issuer.password
        ):
            raise ValueError("RESEARCH_MCP_ISSUER must be an absolute origin URL")
        if not settings.DEV_MODE and issuer.scheme != "https":
            raise ValueError("RESEARCH_MCP_ISSUER must use HTTPS")
        resource = urlsplit(settings.research_mcp_endpoint)
        if (issuer.scheme, issuer.netloc) != (resource.scheme, resource.netloc):
            raise ValueError(
                "RESEARCH_MCP_ISSUER must match the canonical Research MCP origin"
            )
        if not 5 <= settings.RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES <= 60:
            raise ValueError(
                "RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES must be between 5 and 60"
            )
        if not 60 <= settings.RESEARCH_MCP_AUTH_CODE_TTL_SECONDS <= 600:
            raise ValueError(
                "RESEARCH_MCP_AUTH_CODE_TTL_SECONDS must be between 60 and 600"
            )
        if not 1 <= settings.RESEARCH_MCP_REFRESH_TOKEN_DAYS <= 90:
            raise ValueError("RESEARCH_MCP_REFRESH_TOKEN_DAYS must be between 1 and 90")
        if not (
            settings.RESEARCH_MCP_REFRESH_TOKEN_DAYS
            <= settings.RESEARCH_MCP_GRANT_DAYS
            <= 365
        ):
            raise ValueError(
                "RESEARCH_MCP_GRANT_DAYS must cover refresh lifetime and be at most 365"
            )
        if not 1 <= settings.RESEARCH_MCP_CLIENT_REGISTRATION_DAYS <= 90:
            raise ValueError(
                "RESEARCH_MCP_CLIENT_REGISTRATION_DAYS must be between 1 and 90"
            )
        # Both MCP resources deliberately share one rotating asymmetric
        # signing key-ring. Validate it even when Workspace MCP itself is off.
        if not settings.WORKSPACE_MCP_ENABLED:
            if settings.DEV_MODE and not settings.WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64:
                signing_key = settings.WORKSPACE_MCP_TOKEN_SIGNING_KEY
                if len(signing_key) < 32 or _looks_like_placeholder(signing_key):
                    raise ValueError(
                        "WORKSPACE_MCP_TOKEN_SIGNING_KEY is required for MCP OAuth dev tests"
                    )
                if signing_key == settings.SECRET_KEY:
                    raise ValueError(
                        "WORKSPACE_MCP_TOKEN_SIGNING_KEY must not equal SECRET_KEY"
                    )
            else:
                _validate_workspace_signing_keys(settings)

    if settings.WORKSPACE_MCP_ENABLED:
        if not settings.WORKSPACE_MCP_AUDIENCE.strip():
            raise ValueError(
                "WORKSPACE_MCP_AUDIENCE is required when workspace MCP is enabled"
            )
        if not settings.WORKSPACE_MCP_ISSUER.strip():
            raise ValueError(
                "WORKSPACE_MCP_ISSUER is required when workspace MCP is enabled"
            )
        canonical_setting = (
            "WORKSPACE_MCP_CANONICAL_RESOURCE"
            if settings.WORKSPACE_MCP_CANONICAL_RESOURCE.strip()
            else "WORKSPACE_MCP_RESOURCE"
        )
        _validate_mcp_endpoint_url(
            settings.workspace_mcp_endpoint,
            setting_name=canonical_setting,
            expected_path="/api/mcp/workspace",
            dev_mode=settings.DEV_MODE,
        )

        legacy_resources = settings.workspace_mcp_legacy_resources
        if len(legacy_resources) > 5:
            raise ValueError(
                "Workspace MCP resource migration accepts at most five aliases"
            )
        seen_resources = {settings.workspace_mcp_endpoint}
        for legacy_resource in legacy_resources:
            _validate_mcp_endpoint_url(
                legacy_resource,
                setting_name="WORKSPACE_MCP_RESOURCE_ALIASES",
                expected_path="/api/mcp/workspace",
                dev_mode=settings.DEV_MODE,
            )
            if legacy_resource in seen_resources:
                raise ValueError(
                    "Workspace MCP resource aliases must be unique and must "
                    "exclude the canonical resource"
                )
            seen_resources.add(legacy_resource)

        issuer = urlsplit(settings.WORKSPACE_MCP_ISSUER)
        if (
            not issuer.scheme
            or not issuer.netloc
            or issuer.query
            or issuer.fragment
            or issuer.username
            or issuer.password
        ):
            raise ValueError("WORKSPACE_MCP_ISSUER must be an absolute issuer URL")
        if not settings.DEV_MODE and issuer.scheme != "https":
            raise ValueError("WORKSPACE_MCP_ISSUER must use HTTPS")

        if settings.DEV_MODE and not settings.WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64:
            signing_key = settings.WORKSPACE_MCP_TOKEN_SIGNING_KEY
            if len(signing_key) < 32 or _looks_like_placeholder(signing_key):
                raise ValueError(
                    "WORKSPACE_MCP_TOKEN_SIGNING_KEY is required for legacy dev tests"
                )
            if signing_key == settings.SECRET_KEY:
                raise ValueError(
                    "WORKSPACE_MCP_TOKEN_SIGNING_KEY must not equal SECRET_KEY"
                )
        else:
            _validate_workspace_signing_keys(settings)

        if not 5 <= settings.WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES <= 60:
            raise ValueError(
                "WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES must be between 5 and 60"
            )
        if not 60 <= settings.WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS <= 600:
            raise ValueError(
                "WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS must be between 60 and 600"
            )
        if not 1 <= settings.WORKSPACE_MCP_REFRESH_TOKEN_DAYS <= 90:
            raise ValueError(
                "WORKSPACE_MCP_REFRESH_TOKEN_DAYS must be between 1 and 90"
            )
        if not (
            settings.WORKSPACE_MCP_REFRESH_TOKEN_DAYS
            <= settings.WORKSPACE_MCP_GRANT_DAYS
            <= 365
        ):
            raise ValueError(
                "WORKSPACE_MCP_GRANT_DAYS must cover refresh lifetime and be at most 365"
            )
        if not 1 <= settings.WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS <= 90:
            raise ValueError(
                "WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS must be between 1 and 90"
            )
        if not 10 <= settings.WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE <= 600:
            raise ValueError(
                "WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE must be between 10 and 600"
            )
        if not (
            settings.WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE
            <= settings.WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE
            <= 10000
        ):
            raise ValueError(
                "WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE must cover the token "
                "limit and be at most 10000"
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
    if not 10 <= settings.RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE <= 1200:
        raise ValueError(
            "RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE must be between 10 and 1200"
        )
    if not (
        settings.RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE
        <= settings.RESEARCH_MCP_TENANT_REQUESTS_PER_MINUTE
        <= 20000
    ):
        raise ValueError(
            "RESEARCH_MCP_TENANT_REQUESTS_PER_MINUTE must cover the key limit and be at most 20000"
        )
    if not 16384 <= settings.MCP_PROTOCOL_MAX_REQUEST_BYTES <= 1048576:
        raise ValueError(
            "MCP_PROTOCOL_MAX_REQUEST_BYTES must be between 16384 and 1048576"
        )
    if (
        not 1
        <= settings.PLATFORM_TOKEN_TTL_MINUTES
        <= settings.PLATFORM_TOKEN_MAX_TTL_MINUTES
    ):
        raise ValueError("Invalid platform token TTL defaults")


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def validate_dev_mode_urls(settings: Settings) -> None:
    """Fail closed when DEV_MODE is combined with a non-loopback deployment URL.

    DEV_MODE mounts the /dev/* router (email-only login, 365-day tokens for
    every user) and serves interactive API docs. Reachable on a public URL,
    that is full unauthenticated tenant compromise. Refuse to boot rather than
    rely on an operator noticing a warning in the logs.
    """
    if not settings.DEV_MODE:
        return
    from urllib.parse import urlparse

    for field in ("BACKEND_URL", "FRONTEND_URL"):
        host = (urlparse(getattr(settings, field)).hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"DEV_MODE=true but {field} points at non-localhost host "
                f"'{host}'. /dev/* endpoints (email-only login, tokens for "
                "every user) would be publicly reachable. Set DEV_MODE=false "
                "for non-local deployments."
            )


def validate_qbo_settings(settings: Settings) -> None:
    """Validate configured QBO OAuth settings and fail closed in production."""
    environment = settings.QBO_ENVIRONMENT.strip().lower()
    if environment not in {"sandbox", "production"}:
        raise ValueError("QBO_ENVIRONMENT must be 'sandbox' or 'production'")

    configured = any(
        value.strip()
        for value in (
            settings.QBO_CLIENT_ID,
            settings.QBO_CLIENT_SECRET,
            settings.QBO_REDIRECT_URI,
        )
    )
    if not configured and environment == "sandbox":
        return

    if not settings.QBO_CLIENT_ID.strip() or not settings.QBO_CLIENT_SECRET.strip():
        raise ValueError(
            "QBO_CLIENT_ID and QBO_CLIENT_SECRET are required when QBO is configured"
        )

    redirect_uri = settings.QBO_REDIRECT_URI.strip()
    parsed = urlsplit(redirect_uri)
    allowed_schemes = {"https"} if environment == "production" else {"http", "https"}
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        requirement = "HTTPS" if environment == "production" else "HTTP(S)"
        raise ValueError(f"QBO_REDIRECT_URI must be an absolute {requirement} URL")

    if environment == "production":
        expected = f"{settings.BACKEND_URL.rstrip('/')}/api/integrations/qbo/callback"
        if redirect_uri != expected:
            raise ValueError(
                "QBO_REDIRECT_URI must exactly match "
                "BACKEND_URL/api/integrations/qbo/callback in production"
            )


def validate_inbound_email_settings(settings: Settings) -> None:
    if not settings.INBOUND_EMAIL_ENABLED:
        return
    domain = settings.INBOUND_EMAIL_DOMAIN.strip().lower().rstrip(".")
    if not domain or "@" in domain or "." not in domain:
        raise ValueError("INBOUND_EMAIL_DOMAIN must be a valid email domain")
    if len(settings.INBOUND_EMAIL_WEBHOOK_SECRET) < 32:
        raise ValueError(
            "INBOUND_EMAIL_WEBHOOK_SECRET must be at least 32 characters when inbound email is enabled"
        )
    if _looks_like_placeholder(settings.INBOUND_EMAIL_WEBHOOK_SECRET):
        raise ValueError("INBOUND_EMAIL_WEBHOOK_SECRET is still a placeholder")
    if not 1024 <= settings.INBOUND_EMAIL_MAX_BYTES <= 50 * 1024 * 1024:
        raise ValueError("INBOUND_EMAIL_MAX_BYTES must be between 1 KiB and 50 MiB")
    if not 30 <= settings.INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS <= 900:
        raise ValueError(
            "INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS must be between 30 and 900"
        )


def validate_template_ocr_settings(settings: Settings) -> None:
    provider = settings.TEMPLATE_OCR_PROVIDER.strip().lower()
    if provider not in {"local", "azure"}:
        raise ValueError("TEMPLATE_OCR_PROVIDER must be 'local' or 'azure'")
    if not 1 <= settings.TEMPLATE_OCR_LOCAL_CONCURRENCY <= 4:
        raise ValueError("TEMPLATE_OCR_LOCAL_CONCURRENCY must be between 1 and 4")
    if provider == "azure":
        parsed = urlsplit(settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT.rstrip("/"))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT must be an HTTPS URL"
            )
        if not settings.AZURE_DOCUMENT_INTELLIGENCE_KEY:
            raise ValueError(
                "AZURE_DOCUMENT_INTELLIGENCE_KEY is required for Azure OCR"
            )
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}(?:-preview)?",
            settings.AZURE_DOCUMENT_INTELLIGENCE_API_VERSION,
        ):
            raise ValueError("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION is invalid")
    if not 5 <= settings.TEMPLATE_OCR_AZURE_TIMEOUT_SECONDS <= 120:
        raise ValueError("TEMPLATE_OCR_AZURE_TIMEOUT_SECONDS must be between 5 and 120")
    if not 10 <= settings.TEMPLATE_OCR_AZURE_MAX_POLL_SECONDS <= 180:
        raise ValueError(
            "TEMPLATE_OCR_AZURE_MAX_POLL_SECONDS must be between 10 and 180"
        )
    if not 1 <= settings.TEMPLATE_OCR_AZURE_MAX_POLL_INTERVAL_SECONDS <= 10:
        raise ValueError(
            "TEMPLATE_OCR_AZURE_MAX_POLL_INTERVAL_SECONDS must be between 1 and 10"
        )


def validate_worker_settings(settings: Settings) -> None:
    if not 1 <= settings.DURABLE_JOB_TENANT_CONCURRENCY <= 16:
        raise ValueError("DURABLE_JOB_TENANT_CONCURRENCY must be between 1 and 16")


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
    validate_qbo_settings(settings)
    validate_inbound_email_settings(settings)
    validate_template_ocr_settings(settings)
    validate_worker_settings(settings)
    validate_dev_mode_urls(settings)
    return settings
