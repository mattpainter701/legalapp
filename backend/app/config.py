from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    # Short-lived access token; pair with rotating refresh tokens (see auth router).
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Refresh-token lifetime. Refresh tokens are rotating + single-use and are
    # tracked server-side (Redis) so they can be revoked across all workers.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # ── Auth cookies ─────────────────────────────────────────────────────────
    # When unset (None), Secure/SameSite are derived from BACKEND_URL scheme.
    COOKIE_SECURE: bool | None = None
    COOKIE_SAMESITE: str = "lax"  # lax | strict | none

    # ── Background scheduler ─────────────────────────────────────────────────
    # APScheduler must run in EXACTLY ONE process. In prod, API workers set this
    # to False and a single dedicated scheduler container sets it to True. Jobs
    # also take a Postgres advisory lock so a stray second runner cannot double-fire.
    RUN_SCHEDULER: bool = True

    # ── Reverse proxy / rate limiting ────────────────────────────────────────
    # Number of trusted proxy hops in front of the app (e.g. nginx = 1). The
    # client IP is taken as the Nth-from-rightmost X-Forwarded-For entry so a
    # client cannot spoof its rate-limit identity by sending its own XFF header.
    TRUSTED_PROXY_HOPS: int = 1

    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"

    # ── Microsoft Teams ──────────────────────────────────────────────────────
    # Master feature flag for Teams collaboration features (channel linking,
    # outbound Adaptive Card notifications). When False the Teams admin tab and
    # all /api/integrations/teams endpoints are gated off regardless of consent.
    TEAMS_FEATURE_ENABLED: bool = True
    # GUID of the published Clarity Legal Teams app (manifest "id"). Used to
    # build channel/tab deep links. Empty until the shared app is published.
    TEAMS_APP_ID: str = ""

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # ── Zoom OAuth / meeting provider ────────────────────────────────────────
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    ZOOM_REDIRECT_URI: str = ""  # e.g. https://yourdomain.com/api/integrations/zoom/callback
    ZOOM_WEBHOOK_SECRET_TOKEN: str = ""
    ZOOM_PHONE_REDIRECT_URI: str = ""  # e.g. https://yourdomain.com/api/integrations/zoom-phone/callback
    # Optional Zoom Phone Server-to-Server OAuth app. The account ID is
    # tenant-specific in multi-tenant installs; this default is for a single
    # customer deployment until the admin credential UI lands.
    ZOOM_PHONE_CLIENT_ID: str = ""
    ZOOM_PHONE_CLIENT_SECRET: str = ""
    ZOOM_PHONE_ACCOUNT_ID: str = ""
    ZOOM_PHONE_SCOPES: str = (
        "phone:read:list_call_logs:admin phone:read:call_log:admin "
        "phone:read:list_call_recordings:admin phone:read:call_recording:admin "
        "phone:read:recording_transcript:admin"
    )

    # Token encryption key for OAuth tokens at rest (Fernet symmetric)
    # Required: base64-encoded Fernet key (generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    TOKEN_ENCRYPTION_KEY: str = ""

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
    STRIPE_SUCCESS_URL: str = ""  # e.g. https://yourdomain.com/billing?success=1
    STRIPE_CANCEL_URL: str = ""  # e.g. https://yourdomain.com/billing?cancel=1

    # Super-admin platform key — set a long random token; never commit
    PLATFORM_SECRET_KEY: str = ""

    # Optional separate vectorDB for public CourtListener chunks (BGE embeddings)
    # If empty, public_chunks table lives in main DATABASE_URL
    VECTORDB_URL: str = ""
    MCP_SERVER_URL: str = ""

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
    EMAIL_ENABLED: bool = False  # Set True in prod; logs emails in dev
    EMAIL_HOST: str = "smtp.gmail.com"
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASS: str = ""
    EMAIL_FROM: str = "noreply@clarity.legal"
    SLACK_WEBHOOK_URL: str = ""  # Optional: Slack incoming webhook URL

    # Never set True in production — enables /dev/* endpoints
    DEV_MODE: bool = False

    # ── Cloud Search (Live RAG) ──────────────────────────────────────────────
    CLOUD_SEARCH_ENABLED: bool = True  # Master feature flag
    CLOUD_SEARCH_MAX_HITS: int = 10  # Cap results per source
    CLOUD_SEARCH_HIT_CONTENT_CHARS: int = 2000  # Max chars per fetched hit
    CLOUD_SEARCH_CACHE_TTL: int = 300  # 5 min for search results
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

        key = settings.TOKEN_ENCRYPTION_KEY
        if not key:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY is required but not set. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        # Validate it's a valid Fernet key
        Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as e:
        raise ValueError(f"TOKEN_ENCRYPTION_KEY must be a valid Fernet key: {e}") from e
    except Exception as e:
        raise ValueError(f"TOKEN_ENCRYPTION_KEY must be a valid Fernet key: {e}") from e


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    validate_token_encryption_key(settings)
    return settings
