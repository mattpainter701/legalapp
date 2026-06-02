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
from app.services.scheduler import LegalScheduler
from app.routers.chat import cache_manager

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

    # Start APScheduler
    try:
        scheduler = LegalScheduler()
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Scheduler started")
    except Exception as exc:
        logger.error(f"Scheduler failed to start: {exc}")
        app.state.scheduler = None

    # Initialize cache manager
    try:
        await cache_manager.init()
        logger.info("Cache manager initialized")
    except Exception as exc:
        logger.warning(f"Cache manager initialization failed: {exc}")

    yield

    # Shutdown
    if getattr(app.state, "scheduler", None):
        app.state.scheduler.shutdown()
    if getattr(app.state, "redis", None):
        await app.state.redis.aclose()
    await cache_manager.close()
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
        "https://172.16.16.202",
        "http://172.16.16.202:3000",
    }
)

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
app.include_router(plugins_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
app.include_router(dev_router, prefix="/api")
app.include_router(integrations_router)
app.include_router(email_router)
app.include_router(document_sync_router)
app.include_router(user_sync_router)


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
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
