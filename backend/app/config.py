from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # Token encryption key for OAuth tokens at rest (Fernet symmetric)
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

    FRONTEND_URL: str = "http://localhost:3000"
    # OAuth callbacks must point to the backend, not the frontend.
    # In prod behind nginx both URLs share the same domain so set this to
    # https://yourdomain.com. In dev set to http://localhost:8000.
    BACKEND_URL: str = "http://localhost:8000"
    UPLOAD_DIR: str = "/app/uploads"
    MAX_FILE_SIZE_MB: int = 50

    RAG_TOP_K: int = 8
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536

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

    model_config = SettingsConfigDict(env_file=".env")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
