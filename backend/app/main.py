import logging
import os
import shutil
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.routing import Route

from app.config import get_settings
from app.database import async_session_maker, engine, set_tenant_context
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.tenant import TenantMiddleware
from app.middleware.module_guard import ModuleGuardMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.access_log import ApiAccessLogMiddleware
from app.middleware.demo_quota import DemoQuotaMiddleware
from app.middleware.platform_audit import PlatformAuditMiddleware
from app.middleware.platform_key_auth import PlatformKeyAuthMiddleware
from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.chat_artifacts import router as chat_artifacts_router
from app.routers.documents import router as documents_router
from app.routers.admin import router as admin_router
from app.routers.billing import router as billing_router
from app.routers.mcp import router as mcp_router
from app.routers.workspace_mcp_oauth import router as workspace_mcp_oauth_router
from app.routers.research_mcp_oauth import router as research_mcp_oauth_router
from app.routers.platform import router as platform_router
from app.routers.platform_compliance import router as platform_compliance_router
from app.routers.platform_infrastructure import router as platform_infrastructure_router
from app.routers.plugins import router as plugins_router
from app.routers.scheduler import router as scheduler_router
from app.routers.dev import router as dev_router
from app.routers.integrations import router as integrations_router
from app.routers.teams import router as teams_router
from app.routers.email_agent import router as email_router
from app.routers.document_sync import router as document_sync_router
from app.routers.user_sync import router as user_sync_router
from app.routers.qbo import router as qbo_router
from app.routers.billing_extended import router as billing_extended_router
from app.routers.trust_accounting import router as trust_accounting_router
from app.routers.firm import router as firm_branding_router
from app.routers.contacts import router as contacts_router
from app.routers.conflict_checks import router as conflict_checks_router
from app.routers.clients import router as clients_router
from app.routers.tasks import router as tasks_router
from app.routers.communications import (
    communication_context_cache,
    router as communications_router,
)
from app.routers.intake_dashboard import router as intake_dashboard_router
from app.routers.plan import router as plan_router
from app.routers.intake import router as intake_router
from app.routers.intake_assistant import router as intake_assistant_router
from app.routers.conversion_loop import router as conversion_loop_router
from app.routers.engagement_packets import router as engagement_packets_router
from app.routers.matter_parties import router as matter_parties_router
from app.routers.matter_documents import router as matter_documents_router
from app.routers.matters_correspondence import (
    router as matters_correspondence_router,
)
from app.routers.reports import router as reports_router
from app.routers.calendar import router as calendar_router
from app.routers.document_templates import router as document_templates_router
from app.routers.matters import matter_context_cache_manager, router as matters_router
from app.routers.estates import router as estates_router
from app.routers.domestic import router as domestic_router
from app.routers.mediation import router as mediation_router
from app.routers.mediation_portal import router as mediation_portal_router
from app.routers.client_portal import router as client_portal_router
from app.routers.client_portal import firm_router as client_portal_firm_router
from app.routers.esignature import router as esignature_router
from app.routers.esignature import portal_router as esignature_portal_router
from app.routers.onboarding import router as onboarding_router
from app.routers.compliance import router as compliance_router
from app.routers.operating_contract import router as operating_contract_router
from app.routers.operating_trust import router as operating_trust_router
from app.routers.licensing import router as licensing_router
from app.services.mcp_protocol import protocol_endpoint, protocol_lifespan
from app.services.workspace_mcp_protocol import (
    workspace_protocol_endpoint,
    workspace_protocol_lifespan,
)
from app.services.scheduler import LegalScheduler
from app.services.matter_file_store import MatterFileStoragePolicyError
from app.services.host_disk_status import HostDiskStatusError, read_host_disk_status
from app.services.backup_status import BackupStatusError, read_backup_status
from app.release_notes import build_release_catalog
from app.routers.chat import cache_manager
from app.routers.plugins import plugin_cache_manager
from app.routers.prompt_admin import router as prompt_admin_router
from app.routers.cloud_admin import router as cloud_admin_router
from app.routers.smb import router as smb_router
from app.routers.portfolio import router as portfolio_router
from app.routers.users import router as users_router
from app.routers.platform_llm import router as platform_llm_router
from app.routers.platform_assistant import router as platform_assistant_router
from app.routers.external_imports import router as external_imports_router
from app.routers.roles import router as roles_router  # noqa: E402
from app.routers.office_assistant import router as office_assistant_router
from app.routers.marketing import router as marketing_router
from app.routers.matter_document_revisions import (
    router as matter_document_revisions_router,
)
from app.routers.brief_checks import router as brief_checks_router
from app.routers.research_workspaces import router as research_workspaces_router
from app.routers.demo import router as demo_router

settings = get_settings()
logger = logging.getLogger(__name__)

# Module-level flag tracking LiteLLM gateway reachability.
# Set during lifespan startup; read by /health/llm endpoint.
_litellm_healthy: bool = False


def _build_metadata() -> dict[str, str]:
    version = settings.APP_VERSION or "dev"
    commit = settings.APP_COMMIT or ""
    short_commit = commit[:12] if commit else version
    return {
        "version": version,
        "commit": commit,
        "short_commit": short_commit,
        "build_time": settings.APP_BUILD_TIME or "",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    global _litellm_healthy

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info(f"Upload directory ensured: {settings.UPLOAD_DIR}")

    # Redis client for rate limiting AND security-critical revocation (JWT jti
    # blacklist, OAuth CSRF state, password-reset tokens, portal-session
    # revocation). When Redis is unreachable these fall back to a per-worker
    # in-memory dict, which is NOT a reliable guarantee across multiple
    # uvicorn workers — a token revoked on worker A stays valid on worker B.
    # That fallback is acceptable for local dev (DEV_MODE=true) but must not
    # run silently in production: fail closed at startup instead.
    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
        await redis_client.ping()
        app.state.redis = redis_client
        logger.info("Redis connected")
    except Exception as exc:
        if settings.DEV_MODE:
            logger.warning(f"Redis unavailable — rate limiting disabled: {exc}")
            app.state.redis = None
        else:
            raise RuntimeError(
                f"Redis is required in production (DEV_MODE=false) for reliable "
                f"cross-worker token revocation and rate limiting, but is "
                f"unreachable: {exc}"
            ) from exc

    app.state.jti_blacklist: dict[str, float] = {}

    # Database connection test + least-privilege role assertion.
    # RLS is only enforced when the runtime role is NOT a superuser and does NOT
    # have BYPASSRLS. Connecting as the owner/superuser silently bypasses ALL
    # policies, defeating the entire tenant isolation model.
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successful")
        async with engine.connect() as conn:
            row = await conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles"
                    " WHERE rolname = current_user"
                )
            )
            role_row = row.fetchone()
            if role_row and (role_row[0] or role_row[1]):
                message = (
                    "SECURITY: database role is superuser=%s bypassrls=%s. RLS "
                    "tenant isolation is NOT enforced — every tenant's data is "
                    "visible to every other tenant. Connect as the least-privilege "
                    "'clarity_app' role (see scripts/provision_app_role.sql)."
                ) % (role_row[0], role_row[1])
                if settings.DEV_MODE:
                    logger.error(message)
                else:
                    # Fail closed: refuse to serve traffic with tenant isolation
                    # disabled rather than log-and-continue in production.
                    raise RuntimeError(message)
            else:
                logger.info("DB role check passed: RLS is enforced for this connection")
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error(f"Database connection failed: {exc}")
        if not settings.DEV_MODE:
            raise RuntimeError(f"Database connectivity probe failed: {exc}") from exc

    # LiteLLM gateway reachability check
    if not settings.LITELLM_ENABLED:
        logger.warning(
            "LITELLM_ENABLED is False — AI features are disabled. "
            "Set LITELLM_ENABLED=true in production."
        )
        _litellm_healthy = False
        app.state.litellm_healthy = False
    else:
        try:
            import httpx as _httpx

            async with _httpx.AsyncClient(timeout=5.0) as _c:
                _r = await _c.get(f"{settings.LITELLM_BASE_URL}/health/liveliness")
                _r.raise_for_status()
            logger.info("LiteLLM gateway reachable")
            _litellm_healthy = True
            app.state.litellm_healthy = True
        except Exception as exc:
            logger.error(
                "LiteLLM gateway unreachable at startup — AI features will fail "
                f"until gateway is available: {exc}"
            )
            _litellm_healthy = False
            app.state.litellm_healthy = False

    # Start APScheduler — ONLY in the single process designated by RUN_SCHEDULER.
    # Under `uvicorn --workers N` the lifespan runs in every worker, so starting
    # the scheduler unconditionally would fire each cron job N times (duplicate
    # invoices / emails). In prod the API workers set RUN_SCHEDULER=false and a
    # dedicated single-process `scheduler` service sets it to true. Jobs also take
    # a Postgres advisory lock as a backstop against any stray second runner.
    if settings.RUN_SCHEDULER:
        try:
            scheduler = LegalScheduler()
            scheduler.redis = redis_client
            scheduler.start()
            app.state.scheduler = scheduler
            logger.info("Scheduler started (RUN_SCHEDULER=true)")
        except Exception as exc:
            logger.error(f"Scheduler failed to start: {exc}")
            app.state.scheduler = None
    else:
        app.state.scheduler = None
        logger.info("Scheduler disabled in this process (RUN_SCHEDULER=false)")

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

    for name, context_cache in (
        ("matter context", matter_context_cache_manager),
        ("communication context", communication_context_cache),
    ):
        try:
            await context_cache.init()
            logger.info("%s cache manager initialized", name.title())
        except Exception as exc:
            logger.warning(
                "%s cache manager initialization failed: %s", name.title(), exc
            )

    # The official MCP SDK owns JSON-RPC lifecycle and Streamable HTTP
    # semantics. Its public endpoint remains fail-closed unless
    # MCP_PRODUCT_ENABLED is explicitly enabled.
    async with protocol_lifespan(), workspace_protocol_lifespan():
        yield

    # Shutdown
    if getattr(app.state, "scheduler", None):
        app.state.scheduler.shutdown()
    if getattr(app.state, "redis", None):
        await app.state.redis.aclose()
    await cache_manager.close()
    await plugin_cache_manager.close()
    await matter_context_cache_manager.close()
    await communication_context_cache.close()
    await engine.dispose()
    logger.info("Shutdown complete")


# Interactive API docs expose the entire route/schema surface to anyone who can
# reach the server. Only serve them when DEV_MODE is on; production deployments
# must set DEV_MODE=false, which also gates the /dev/* router below.
_docs_kwargs = (
    {}
    if settings.DEV_MODE
    else {"docs_url": None, "redoc_url": None, "openapi_url": None}
)

app = FastAPI(
    title="LawHand API",
    version="1.0.0",
    description="Multi-tenant legal AI SaaS backend",
    lifespan=lifespan,
    **_docs_kwargs,
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

# Origins are an explicit allow-list (never "*") because credentials are enabled —
# a wildcard origin with credentials is both invalid and unsafe. Methods/headers
# are pinned to what the SPA + platform key actually use rather than "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Platform-Key",
        "X-MCP-API-Key",
        "Mcp-Protocol-Version",
        "Mcp-Session-Id",
        "Last-Event-ID",
        "X-Idempotency-Key",
    ],
    expose_headers=[
        "Mcp-Session-Id",
        "X-Clarity-Preview-ID",
        "X-Clarity-Preview-Purpose",
    ],
)

# Tenant middleware (must come after CORS)
app.add_middleware(TenantMiddleware)

app.add_middleware(ModuleGuardMiddleware)  # fail-closed plan/module enforcement

app.add_middleware(RateLimitMiddleware)  # reads app.state.redis at request time

app.add_middleware(PlatformAuditMiddleware)

# Registered after the audit middleware so it runs *before* it: a minted key is
# resolved to a named operator on the way in, and the audit trail records who
# made the call even when the call is then refused for insufficient scope.
app.add_middleware(PlatformKeyAuthMiddleware)

app.add_middleware(ApiAccessLogMiddleware)
app.add_middleware(DemoQuotaMiddleware)

# Request-id middleware is registered last so it is outermost in Starlette's
# middleware stack and can stamp even middleware-generated responses.
app.add_middleware(RequestIdMiddleware)

# ─────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────
# Register the exact protocol URL before the compatibility router. A Starlette
# Route (rather than a Mount) prevents it from swallowing
# /api/mcp/product-keys and the REST compatibility subpaths.
app.router.routes.append(
    Route(
        "/api/mcp",
        endpoint=protocol_endpoint,
        methods=["GET", "POST", "DELETE"],
        name="mcp_streamable_http",
    )
)
app.router.routes.append(
    Route(
        "/api/mcp/workspace",
        endpoint=workspace_protocol_endpoint,
        methods=["GET", "POST", "DELETE"],
        name="workspace_mcp_streamable_http",
    )
)
app.include_router(workspace_mcp_oauth_router)
app.include_router(research_mcp_oauth_router)
app.include_router(auth_router, prefix="/api")
app.include_router(demo_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(chat_artifacts_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(mcp_router, prefix="/api")
app.include_router(platform_router, prefix="/api")
app.include_router(platform_compliance_router, prefix="/api")
app.include_router(platform_infrastructure_router, prefix="/api")
app.include_router(platform_llm_router, prefix="/api")
app.include_router(platform_assistant_router, prefix="/api")
# Dedicated plugin-subpath routers MUST be registered before the generic
# plugins_router, whose greedy ``POST /{plugin}/{skill}`` skill-execution route
# would otherwise shadow specific paths like ``/api/plugins/mediation/cases``.
app.include_router(estates_router)
app.include_router(domestic_router)
app.include_router(mediation_router)
app.include_router(mediation_portal_router)
app.include_router(plugins_router, prefix="/api")
app.include_router(prompt_admin_router, prefix="/api")
app.include_router(cloud_admin_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
# /dev/* endpoints (email-only login, tokens for every user) are only mounted
# when DEV_MODE is on — not merely 404'd inside each handler — so the routes
# don't exist in the OpenAPI schema or the routing table in production.
if settings.DEV_MODE:
    app.include_router(dev_router, prefix="/api")
app.include_router(integrations_router)
app.include_router(teams_router)
app.include_router(email_router)
app.include_router(document_sync_router)
app.include_router(user_sync_router)
app.include_router(qbo_router)
app.include_router(billing_extended_router)
app.include_router(trust_accounting_router)
app.include_router(firm_branding_router)
app.include_router(contacts_router)
app.include_router(conflict_checks_router)
app.include_router(clients_router)
app.include_router(tasks_router)
app.include_router(communications_router)
app.include_router(intake_dashboard_router)
app.include_router(plan_router, prefix="/api")
app.include_router(conversion_loop_router)
app.include_router(intake_router)
app.include_router(intake_assistant_router)
app.include_router(engagement_packets_router)
app.include_router(matter_parties_router)
app.include_router(matter_documents_router)
app.include_router(matters_correspondence_router)
app.include_router(reports_router)
app.include_router(calendar_router)
app.include_router(matters_router)
app.include_router(client_portal_router)
app.include_router(client_portal_firm_router)
app.include_router(esignature_router)
app.include_router(esignature_portal_router)
app.include_router(document_templates_router)
app.include_router(onboarding_router)
app.include_router(compliance_router)
app.include_router(operating_contract_router)
app.include_router(operating_trust_router)
app.include_router(licensing_router)
app.include_router(smb_router)
app.include_router(portfolio_router)
app.include_router(users_router)
app.include_router(external_imports_router)
app.include_router(roles_router)
app.include_router(office_assistant_router)
app.include_router(marketing_router)
app.include_router(matter_document_revisions_router)
app.include_router(brief_checks_router)
app.include_router(research_workspaces_router)


# ─────────────────────────────────────────────────────
# Health endpoints
# ─────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint.

    Returns HTTP 200 only when the database is reachable. When the database
    is down the endpoint returns HTTP 503 so that orchestrators (Docker
    healthcheck, k8s liveness probe, load-balancer health check) can detect
    the failure and restart or route around the instance.
    """
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        # /health is unauthenticated — log the real error server-side but never
        # return raw exception text (can contain host/user/connection details).
        logger.error(f"Health check DB connection failed: {exc}")

    if not db_ok:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "database": "unavailable",
                **_build_metadata(),
            },
        )

    return {
        "status": "ok",
        "database": "connected",
        **_build_metadata(),
    }


@app.get("/health/readiness", tags=["health"])
async def health_readiness(request: Request):
    """Non-sensitive production readiness used by off-host monitoring.

    The response intentionally exposes only component states. Detailed errors,
    tenant identifiers, queue contents, and infrastructure addresses remain in
    server logs and the authenticated operator check.
    """
    states = {
        "disk": "unavailable",
        "database": "unavailable",
        "redis": "unavailable",
        "scheduler": "stale",
        "queue": "stale",
    }
    if settings.HOST_DISK_STATUS_FILE:
        states["host_disks"] = "unavailable"
    if settings.BACKUP_STATUS_FILE:
        states["backups"] = "unavailable"

    try:
        usage = shutil.disk_usage(settings.UPLOAD_DIR)
        used_percent = (usage.used / usage.total * 100) if usage.total else 100
        states["disk"] = (
            "ok" if used_percent < settings.HEALTH_DISK_MAX_PERCENT else "full"
        )
    except Exception:
        logger.exception("Readiness disk probe failed")

    if settings.HOST_DISK_STATUS_FILE:
        try:
            states["host_disks"] = read_host_disk_status(
                settings.HOST_DISK_STATUS_FILE,
                max_age_seconds=settings.HEALTH_HOST_DISK_MAX_AGE_SECONDS,
            )
        except HostDiskStatusError as exc:
            states["host_disks"] = exc.state
            logger.error("Host disk readiness probe failed: %s", exc)

    if settings.BACKUP_STATUS_FILE:
        try:
            states["backups"] = read_backup_status(
                settings.BACKUP_STATUS_FILE,
                max_age_seconds=settings.HEALTH_BACKUP_MAX_AGE_SECONDS,
            )
        except BackupStatusError as exc:
            states["backups"] = exc.state
            logger.error("Off-site backup readiness probe failed: %s", exc)

    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            await redis_client.ping()
            states["redis"] = "ok"
        except Exception:
            logger.exception("Readiness Redis probe failed")

    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
            states["database"] = "ok"
            tenant_ids = list(
                (
                    await session.execute(
                        text(
                            "SELECT id FROM tenants "
                            "WHERE is_active AND billing_tier <> 'demo' ORDER BY id"
                        )
                    )
                ).scalars()
            )
            scheduler_ok = True
            queue_ok = True
            for tenant_id in tenant_ids:
                await set_tenant_context(session, str(tenant_id))
                heartbeat = await session.scalar(
                    text(
                        """
                        SELECT 1
                        FROM scheduler_logs
                        WHERE agent_name = 'scheduler-heartbeat'
                          AND status = 'completed'
                          AND tenant_id = :tenant_id
                          AND run_at >= now() - make_interval(mins => :age)
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "age": settings.HEALTH_SCHEDULER_MAX_AGE_MINUTES,
                    },
                )
                if not heartbeat:
                    scheduler_ok = False
                stale = await session.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM durable_jobs
                        WHERE
                          (status = 'pending' AND available_at < now() - make_interval(mins => :age))
                          OR (status = 'running' AND (leased_at IS NULL OR leased_at < now() - interval '15 minutes'))
                          OR (status = 'failed' AND attempts >= max_attempts)
                        """
                    ),
                    {"age": settings.HEALTH_QUEUE_MAX_AGE_MINUTES},
                )
                if stale:
                    queue_ok = False
            states["scheduler"] = "ok" if scheduler_ok else "stale"
            states["queue"] = "ok" if queue_ok else "stale"
    except Exception:
        logger.exception("Readiness database/scheduler/queue probe failed")

    healthy = all(value == "ok" for value in states.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "unhealthy",
            "components": states,
            **_build_metadata(),
        },
    )


@app.get("/api/version", tags=["health"])
async def app_version():
    return {
        "status": "ok",
        **_build_metadata(),
        **build_release_catalog(),
    }


@app.get("/health/llm", tags=["health"])
async def health_check_llm():
    """LiteLLM gateway liveness endpoint.

    Always returns HTTP 200 so load balancers do not flip on gateway hiccups.
    Consumers should inspect the ``status`` field:
    - ``"disabled"``  — LITELLM_ENABLED is False
    - ``"ok"``        — gateway is reachable
    - ``"degraded"``  — gateway ping failed; details are available in server logs
    """
    if not settings.LITELLM_ENABLED:
        return {"status": "disabled"}

    try:
        import httpx as _httpx

        async with _httpx.AsyncClient(timeout=5.0) as _c:
            _r = await _c.get(f"{settings.LITELLM_BASE_URL}/health/liveliness")
            _r.raise_for_status()
        return {"status": "ok"}
    except Exception:
        # This endpoint is unauthenticated. Keep provider URLs, credentials,
        # network details, and exception text out of the public response while
        # retaining the full traceback for server-side troubleshooting.
        logger.exception("LLM health check failed")
        return {"status": "degraded"}


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
        request_id = getattr(request.state, "request_id", None)

        async with async_session_maker() as session:
            return await capture_error(
                db=session,
                error_type=error_type,
                severity="error" if status_code >= 500 else "warning",
                message=str(exc),
                request=request,
                status_code=status_code,
                user_id=user_id,
                tenant_id=tenant_id,
                request_id=request_id,
            )
    except Exception:
        return None  # Error tracking must never cascade


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Capture 4xx/5xx HTTP exceptions
    request_id = getattr(request.state, "request_id", None)
    error_id = None
    if exc.status_code >= 400:
        error_id = await _capture_exception_to_errorlog(
            request,
            exc,
            exc.status_code,
            error_type="validation_error"
            if exc.status_code in (400, 422)
            else "api_error",
        )
    content = {"detail": exc.detail}
    if request_id:
        content["request_id"] = request_id
    if error_id and exc.status_code >= 500:
        content["error_id"] = str(error_id)
    response = JSONResponse(status_code=exc.status_code, content=content)
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(MatterFileStoragePolicyError)
async def matter_storage_policy_exception_handler(
    request: Request, exc: MatterFileStoragePolicyError
):
    """Report a retryable customer-storage outage without exposing provider detail."""
    request_id = getattr(request.state, "request_id", None)
    error_id = await _capture_exception_to_errorlog(
        request,
        exc,
        503,
        error_type="storage_policy_error",
    )
    content = {"detail": str(exc)}
    if request_id:
        content["request_id"] = request_id
    if error_id:
        content["error_id"] = str(error_id)
    response = JSONResponse(status_code=503, content=content)
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


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

    request_id = getattr(request.state, "request_id", None)
    error_id = await _capture_exception_to_errorlog(
        request,
        exc,
        500,
        error_type=error_type,
    )
    response = {"detail": "Something went wrong"}
    if request_id:
        response["request_id"] = request_id
    if error_id:
        response["error_id"] = str(error_id)
    payload = JSONResponse(status_code=500, content=response)
    if request_id:
        payload.headers["X-Request-ID"] = request_id
    return payload
