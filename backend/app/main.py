import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.middleware.tenant import TenantMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.access_log import ApiAccessLogMiddleware
from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.documents import router as documents_router
from app.routers.admin import router as admin_router
from app.routers.billing import router as billing_router
from app.routers.mcp import router as mcp_router
from app.routers.platform import router as platform_router
from app.routers.plugins import router as plugins_router
from app.routers.scheduler import router as scheduler_router
from app.routers.dev import router as dev_router
from app.routers.integrations import router as integrations_router
from app.routers.email_agent import router as email_router
from app.routers.document_sync import router as document_sync_router
from app.routers.user_sync import router as user_sync_router
from app.routers.qbo import router as qbo_router
from app.routers.billing_extended import router as billing_extended_router
from app.routers.trust_accounting import router as trust_accounting_router
from app.routers.contacts import router as contacts_router
from app.routers.tasks import router as tasks_router
from app.routers.communications import router as communications_router
from app.routers.intake import router as intake_router
from app.routers.matter_parties import router as matter_parties_router
from app.routers.matter_documents import router as matter_documents_router
from app.routers.reports import router as reports_router
from app.routers.calendar import router as calendar_router
from app.routers.document_templates import router as document_templates_router
from app.routers.matters import router as matters_router
from app.routers.estates import router as estates_router
from app.routers.mediation import router as mediation_router
from app.routers.mediation_portal import router as mediation_portal_router
from app.routers.client_portal import router as client_portal_router
from app.routers.client_portal import firm_router as client_portal_firm_router
from app.routers.onboarding import router as onboarding_router
from app.routers.licensing import router as licensing_router
from app.services.scheduler import LegalScheduler
from app.routers.chat import cache_manager
from app.routers.plugins import plugin_cache_manager
from app.routers.prompt_admin import router as prompt_admin_router
from app.routers.cloud_admin import router as cloud_admin_router
from app.routers.smb import router as smb_router
from app.routers.portfolio import router as portfolio_router
from app.routers.users import router as users_router

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info(f"Upload directory ensured: {settings.UPLOAD_DIR}")

    # Redis client for rate limiting
    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
        await redis_client.ping()
        app.state.redis = redis_client
        logger.info("Redis connected")
    except Exception as exc:
        logger.warning(f"Redis unavailable — rate limiting disabled: {exc}")
        app.state.redis = None

    app.state.jti_blacklist: dict[str, float] = {}

    # Database connection test
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successful")
    except Exception as exc:
        logger.error(f"Database connection failed: {exc}")

    # LiteLLM gateway reachability check (warn-only)
    if settings.LITELLM_ENABLED:
        try:
            import httpx as _httpx

            async with _httpx.AsyncClient(timeout=5.0) as _c:
                _r = await _c.get(f"{settings.LITELLM_BASE_URL}/health/liveliness")
                _r.raise_for_status()
            logger.info("LiteLLM gateway reachable")
        except Exception as exc:
            logger.warning(f"LiteLLM gateway unreachable at startup: {exc}")

    # Start APScheduler
    try:
        scheduler = LegalScheduler()
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Scheduler started")
    except Exception as exc:
        logger.error(f"Scheduler failed to start: {exc}")
        app.state.scheduler = None

    # Initialize cache managers
    try:
        await cache_manager.init()
        logger.info("Cache manager initialized")
    except Exception as exc:
        logger.warning(f"Cache manager initialization failed: {exc}")

    try:
        await plugin_cache_manager.init()
        logger.info("Plugin cache manager initialized")
    except Exception as exc:
        logger.warning(f"Plugin cache manager initialization failed: {exc}")

    yield

    # Shutdown
    if getattr(app.state, "scheduler", None):
        app.state.scheduler.shutdown()
    if getattr(app.state, "redis", None):
        await app.state.redis.aclose()
    await cache_manager.close()
    await plugin_cache_manager.close()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Clarity Legal API",
    version="1.0.0",
    description="Multi-tenant legal AI SaaS backend",
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────────────
origins = list(
    {
        settings.FRONTEND_URL,
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://localhost:3000",
    }
)

# Add extra CORS origins from environment variable (comma-separated)
if settings.EXTRA_CORS_ORIGINS:
    extra_origins = [
        o.strip() for o in settings.EXTRA_CORS_ORIGINS.split(",") if o.strip()
    ]
    origins.extend(extra_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────
# Tenant middleware (must come after CORS)
# ─────────────────────────────────────────────────────
app.add_middleware(TenantMiddleware)

app.add_middleware(RateLimitMiddleware)  # reads app.state.redis at request time

app.add_middleware(ApiAccessLogMiddleware)

# ─────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(mcp_router, prefix="/api")
app.include_router(platform_router, prefix="/api")
# Dedicated plugin-subpath routers MUST be registered before the generic
# plugins_router, whose greedy ``POST /{plugin}/{skill}`` skill-execution route
# would otherwise shadow specific paths like ``/api/plugins/mediation/cases``.
app.include_router(estates_router)
app.include_router(mediation_router)
app.include_router(mediation_portal_router)
app.include_router(plugins_router, prefix="/api")
app.include_router(prompt_admin_router, prefix="/api")
app.include_router(cloud_admin_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
app.include_router(dev_router, prefix="/api")
app.include_router(integrations_router)
app.include_router(email_router)
app.include_router(document_sync_router)
app.include_router(user_sync_router)
app.include_router(qbo_router)
app.include_router(billing_extended_router)
app.include_router(trust_accounting_router)
app.include_router(contacts_router)
app.include_router(tasks_router)
app.include_router(communications_router)
app.include_router(intake_router)
app.include_router(matter_parties_router)
app.include_router(matter_documents_router)
app.include_router(reports_router)
app.include_router(calendar_router)
app.include_router(matters_router)
app.include_router(client_portal_router)
app.include_router(client_portal_firm_router)
app.include_router(document_templates_router)
app.include_router(onboarding_router)
app.include_router(licensing_router)
app.include_router(smb_router)
app.include_router(portfolio_router)
app.include_router(users_router)


# ─────────────────────────────────────────────────────
# Health endpoint
# ─────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    """Simple health check endpoint."""
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unavailable",
        "version": "1.0.0",
    }


# ─────────────────────────────────────────────────────
# Exception handlers
# ─────────────────────────────────────────────────────
async def _capture_exception_to_errorlog(
    request: Request,
    exc: Exception,
    status_code: int,
    error_type: str = "api_error",
):
    """Persist exception to ErrorLog table if database is available."""
    try:
        import uuid as _uuid

        from app.database import async_session_maker
        from app.services.error_tracker import capture_error

        # Try to extract user/tenant from request state (set by TenantMiddleware)
        user_id_str = getattr(request.state, "user_id", None)
        tenant_id_str = getattr(request.state, "tenant_id", None)

        user_id = _uuid.UUID(user_id_str) if user_id_str else None
        tenant_id = _uuid.UUID(tenant_id_str) if tenant_id_str else None

        async with async_session_maker() as session:
            await capture_error(
                db=session,
                error_type=error_type,
                severity="error" if status_code >= 500 else "warning",
                message=str(exc),
                request=request,
                status_code=status_code,
                user_id=user_id,
                tenant_id=tenant_id,
            )
    except Exception:
        pass  # Error tracking must never cascade


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Capture 4xx/5xx HTTP exceptions
    if exc.status_code >= 400:
        await _capture_exception_to_errorlog(
            request,
            exc,
            exc.status_code,
            error_type="validation_error"
            if exc.status_code in (400, 422)
            else "api_error",
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    # Classify error type based on exception class
    error_type = exc.__class__.__name__.lower()
    if "validation" in error_type:
        error_type = "validation_error"
    elif "timeout" in error_type:
        error_type = "timeout_error"
    elif "database" in error_type or "sqlalchemy" in error_type.lower():
        error_type = "database_error"
    else:
        error_type = "api_error"

    await _capture_exception_to_errorlog(
        request,
        exc,
        500,
        error_type=error_type,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
