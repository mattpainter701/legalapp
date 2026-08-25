"""LawHand MCP gateway and compatibility management routes.

The official SDK-backed Streamable HTTP transport is ``/api/mcp``. External
traffic uses scoped ``X-MCP-API-Key`` product credentials; application JWTs are
accepted only by the compatibility REST adapter. Legacy ``X-API-Key`` tenant
credentials are rejected and cannot be issued.
"""

import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker, get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.middleware.rate_limit import _client_ip as _trusted_client_ip
from app.models.tenant import Tenant
from app.models.user import User
from app.services.embeddings import EmbeddingService
from app.services.mcp_product import (
    DEFAULT_ALLOWED_TOOLS,
    create_product_key,
    enforce_product_key_burst_limit,
    enforce_product_key_quota,
    ensure_tool_allowed,
    list_product_keys,
    mask_key,
    metering_outbox_summary,
    record_mcp_usage,
    resolve_product_key,
    revoke_product_key,
    usage_summary,
)
from app.services.mcp_platform_tools import (
    PLATFORM_TOOL_NAMES,
    execute_platform_tool,
)
from app.services.rag import full_rag_query

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])
_embedding_service = EmbeddingService()
PLATFORM_TOOL_SET = frozenset(PLATFORM_TOOL_NAMES)


def _mcp_proxy_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _upstream_auth_headers() -> dict[str, str]:
    """Authenticate only as the backend service, never as the end user."""
    if not settings.MCP_UPSTREAM_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Private CourtListener service authentication is not configured",
        )
    return {"X-Clarity-Internal-Key": settings.MCP_UPSTREAM_API_KEY}


async def _proxy_get(path: str, request: Request):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            _mcp_proxy_url(settings.MCP_SERVER_URL, path),
            headers=_upstream_auth_headers(),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def _proxy_post(path: str, request: Request, payload: dict):
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            _mcp_proxy_url(settings.MCP_SERVER_URL, path),
            json=payload,
            headers=_upstream_auth_headers(),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def _proxied_tool_names(request: Request) -> list[str]:
    if not settings.MCP_SERVER_URL:
        return list(DEFAULT_ALLOWED_TOOLS)
    try:
        manifest = await _proxy_get("/api/mcp", request)
    except Exception:
        return list(DEFAULT_ALLOWED_TOOLS)
    tools = manifest.get("tools") if isinstance(manifest, dict) else None
    if not isinstance(tools, list):
        return list(DEFAULT_ALLOWED_TOOLS)
    return [
        tool.get("name")
        for tool in tools
        if isinstance(tool, dict) and tool.get("name")
    ]


def _tool_json_payload(response: dict) -> dict:
    content = response.get("content") if isinstance(response, dict) else None
    if not isinstance(content, list):
        return {}
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("json"), dict):
            return item["json"]
    return {}


# ── Auth helper (JWT or API key) ──────────────────────────────────────────────


def _public_manifest(tools: list[dict]) -> dict:
    mcp_url = settings.research_mcp_endpoint
    return {
        "protocolVersion": "2025-06-18",
        "serverInfo": {
            "name": "clarity-legal",
            "version": "1.0.0",
            "description": "LawHand MCP gateway for legal research and practice tools",
        },
        "capabilities": {"tools": {}},
        "tools": tools,
        "transports": {"streamable_http": mcp_url},
        "auth": {"header": "X-MCP-API-Key"},
    }


async def _get_user_and_tenant(
    request: Request,
    db: AsyncSession,
) -> tuple[User, Tenant]:
    """Authenticate application users with the normal JWT/cookie flow only."""
    if request.headers.get("X-API-Key"):
        raise HTTPException(
            status_code=401,
            detail="Legacy tenant API keys are disabled; use a scoped MCP product key",
        )
    user = await get_current_user(request, db)
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not tenant.is_active:
        raise HTTPException(status_code=403, detail="Tenant is inactive")
    return user, tenant


async def _require_mcp_identity(
    request: Request, db: AsyncSession
) -> tuple[User, Tenant]:
    user, tenant = await _get_user_and_tenant(request, db)
    await set_tenant_context(db, str(tenant.id))
    return user, tenant


# ── Manifest ──────────────────────────────────────────────────────────────────


@router.get("/manifest")
async def mcp_manifest(request: Request):
    """Return public metadata backed by the validated private tool catalog."""
    if not settings.MCP_PRODUCT_ENABLED:
        raise HTTPException(status_code=404, detail="MCP product access is disabled")
    # Import lazily to avoid a router/service cycle during application startup.
    from app.services.mcp_protocol import get_tool_catalog

    tools = await get_tool_catalog(request)
    return _public_manifest(
        [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in tools
        ]
    )


@router.get("/source-health")
async def source_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return sanitized public-authority freshness for authenticated app users."""
    await _require_mcp_identity(request, db)
    if not settings.MCP_SERVER_URL:
        return {
            "available": False,
            "status": "unconfigured",
            "sources": [],
            "partitions": [],
        }
    try:
        response = await _proxy_post(
            "/api/mcp/tools/call",
            request,
            {"name": "sync_status", "arguments": {}},
        )
    except Exception:
        return {
            "available": False,
            "status": "unavailable",
            "sources": [],
            "partitions": [],
        }

    payload = _tool_json_payload(response)
    sources = []
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            continue
        sources.append(
            {
                key: source.get(key)
                for key in (
                    "source_key",
                    "publisher",
                    "source_type",
                    "jurisdiction",
                    "canonical_url",
                    "coverage_start",
                    "coverage_end",
                    "coverage_kind",
                    "last_successful_sync_at",
                    "item_count",
                    "chunk_count",
                    "embedded_chunk_count",
                    "embedding_model",
                    "embedding_version",
                )
            }
            | {"status": "attention" if source.get("current_error") else "healthy"}
        )
    partitions = []
    for partition in payload.get("source_partitions") or []:
        if not isinstance(partition, dict):
            continue
        partitions.append(
            {
                key: partition.get(key)
                for key in (
                    "source_key",
                    "partition_key",
                    "checkpoint_at",
                    "status",
                    "last_attempted_at",
                    "last_successful_sync_at",
                    "rows_processed",
                    "chunks_created",
                )
            }
        )
    has_attention = any(source["status"] == "attention" for source in sources) or any(
        partition.get("status") == "failed" for partition in partitions
    )
    return {
        "available": bool(sources),
        "status": "attention" if has_attention else ("healthy" if sources else "empty"),
        "sources": sources,
        "partitions": partitions,
    }


# ── Tool invocation ───────────────────────────────────────────────────────────


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


class ProductKeyCreateRequest(BaseModel):
    name: str = "MCP API key"
    monthly_call_limit: int | None = None
    burst_limit_per_minute: int | None = None
    allowed_tools: list[str] | None = None


def _request_ip(request: Request) -> str | None:
    return _trusted_client_ip(request)


def _result_count(payload: dict) -> int:
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list):
        return 0
    for item in content:
        if not isinstance(item, dict):
            continue
        body = item.get("json")
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            return len(body["results"])
    return len(content)


def parse_mcp_message_body(body: dict) -> ToolCallRequest:
    """Accept JSON-RPC-style MCP messages and the existing REST body shape."""
    if body.get("method") == "tools/call":
        params = body.get("params") or {}
        return ToolCallRequest(
            name=params.get("name", ""),
            arguments=params.get("arguments") or {},
        )
    return ToolCallRequest.model_validate(body)


async def _record_failed_tool_call(
    body: "ToolCallRequest",
    request: Request,
    *,
    product_key,
    tenant,
    transport: str,
    started: float,
    exc: BaseException,
) -> None:
    """Persist a failed tool call on a session of its own.

    The request session cannot carry this write. A tool that fails with a
    database error leaves that session in a failed transaction, so the usage
    write raises ``InFailedSQLTransactionError`` instead of recording anything
    -- and masks the original exception on its way out. Even when the session is
    healthy, ``get_db`` rolls it back as the exception propagates, so a row
    merely flushed there would not survive the request that produced it.

    Metering is evidence about a failure, never a reason to convert one into a
    different failure, so this swallows its own errors after logging them.
    """
    try:
        async with async_session_maker() as usage_db:
            await set_tenant_context(usage_db, str(tenant.id))
            await record_mcp_usage(
                db=usage_db,
                tenant_id=tenant.id,
                product_key_id=product_key.id,
                auth_type="product_key",
                transport=transport,
                tool_name=body.name,
                status_code=exc.status_code if isinstance(exc, HTTPException) else 500,
                latency_ms=int((time.perf_counter() - started) * 1000),
                ip_address=_request_ip(request),
                user_agent=request.headers.get("User-Agent"),
                error_class=type(exc).__name__,
                query_text=str((body.arguments or {}).get("query") or "")[:2000]
                or None,
            )
            await usage_db.commit()
    except Exception:
        logger.exception(
            "MCP usage metering failed for tool %s after %s",
            body.name,
            type(exc).__name__,
        )


async def _call_platform_tool_metered(
    body: "ToolCallRequest",
    request: Request,
    db: AsyncSession,
    *,
    product_key,
    tenant,
    transport: str,
    started: float,
):
    """Execute a platform-native tool with the same metering as research tools."""
    try:
        response = await execute_platform_tool(
            body.name,
            body.arguments or {},
            db=db,
            tenant_id=tenant.id,
        )
    except Exception as exc:
        # Meter every failure, not only HTTPException: an unexpected error is
        # exactly the one worth having in the usage record. Deliberately not
        # BaseException -- a cancelled request must not trigger another write.
        await _record_failed_tool_call(
            body,
            request,
            product_key=product_key,
            tenant=tenant,
            transport=transport,
            started=started,
            exc=exc,
        )
        raise
    await record_mcp_usage(
        db=db,
        tenant_id=tenant.id,
        product_key_id=product_key.id,
        auth_type="product_key",
        transport=transport,
        tool_name=body.name,
        status_code=200,
        result_count=_result_count(response),
        latency_ms=int((time.perf_counter() - started) * 1000),
        ip_address=_request_ip(request),
        user_agent=request.headers.get("User-Agent"),
        query_text=str((body.arguments or {}).get("query") or "")[:2000] or None,
    )
    return response


async def _call_tool_with_product_key(
    body: ToolCallRequest,
    request: Request,
    db: AsyncSession,
    *,
    transport: str = "rest",
):
    started = time.perf_counter()
    product_key, tenant = await resolve_product_key(
        db,
        request.headers.get("X-MCP-API-Key", ""),
    )
    await set_tenant_context(db, str(tenant.id))
    ensure_tool_allowed(product_key, body.name)
    app = getattr(request, "app", None)
    redis = getattr(getattr(app, "state", None), "redis", None)
    await enforce_product_key_burst_limit(redis, product_key)
    await enforce_product_key_quota(db, product_key)

    # Platform-native tools execute in the backend against tenant data;
    # research tools proxy to the private CourtListener sidecar.
    if body.name in PLATFORM_TOOL_SET:
        return await _call_platform_tool_metered(
            body,
            request,
            db,
            product_key=product_key,
            tenant=tenant,
            transport=transport,
            started=started,
        )

    try:
        if settings.MCP_SERVER_URL:
            response = await _proxy_post(
                "/api/mcp/tools/call",
                request,
                body.model_dump(),
            )
        else:
            raise HTTPException(
                status_code=503,
                detail="External MCP product keys require the CourtListener MCP server",
            )
    except Exception as exc:
        # Meter every failure, not only HTTPException: an unexpected error is
        # exactly the one worth having in the usage record. Deliberately not
        # BaseException -- a cancelled request must not trigger another write.
        await _record_failed_tool_call(
            body,
            request,
            product_key=product_key,
            tenant=tenant,
            transport=transport,
            started=started,
            exc=exc,
        )
        raise
    await record_mcp_usage(
        db=db,
        tenant_id=tenant.id,
        product_key_id=product_key.id,
        auth_type="product_key",
        transport=transport,
        tool_name=body.name,
        status_code=200,
        result_count=_result_count(response),
        latency_ms=int((time.perf_counter() - started) * 1000),
        ip_address=_request_ip(request),
        user_agent=request.headers.get("User-Agent"),
        query_text=str((body.arguments or {}).get("query") or "")[:2000] or None,
    )
    return response


@router.post("/tools/call")
async def call_tool(
    body: ToolCallRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute an MCP tool call."""
    if request.headers.get("X-MCP-API-Key"):
        return await _call_tool_with_product_key(body, request, db)

    user, tenant = await _require_mcp_identity(request, db)
    if settings.MCP_SERVER_URL:
        started = time.perf_counter()
        response = await _proxy_post(
            "/api/mcp/tools/call",
            request,
            body.model_dump(),
        )
        await record_mcp_usage(
            db=db,
            tenant_id=tenant.id,
            user_id=user.id,
            product_key_id=None,
            auth_type="jwt",
            transport="rest",
            tool_name=body.name,
            status_code=200,
            result_count=_result_count(response),
            latency_ms=int((time.perf_counter() - started) * 1000),
            ip_address=_request_ip(request),
            user_agent=request.headers.get("User-Agent"),
            query_text=str((body.arguments or {}).get("query") or "")[:2000] or None,
        )
        return response

    if body.name == "search_caselaw":
        query = body.arguments.get("query", "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query is required")
        top_k = min(max(int(body.arguments.get("top_k", 8)), 1), 20)

        _, chunks = await full_rag_query(
            db=db,
            embedding_service=_embedding_service,
            question=query,
            tenant_id=str(tenant.id),
            user_id=str(user.id),
            include_public=True,
        )
        # Trim to requested top_k (full_rag_query uses settings.RAG_TOP_K)
        chunks = chunks[:top_k]

        return {
            "content": [
                {
                    "type": "text",
                    "text": _format_chunk(i + 1, c),
                }
                for i, c in enumerate(chunks)
            ],
            "isError": False,
        }

    elif body.name == "get_chunk":
        chunk_id = body.arguments.get("chunk_id", "").strip()
        if not chunk_id:
            raise HTTPException(status_code=400, detail="chunk_id is required")

        from sqlalchemy import text as sa_text

        result = await db.execute(
            sa_text("""
                SELECT id::text, content, case_name, citation, court, decision_date
                FROM chunks
                WHERE id = :chunk_id
                  AND (tenant_id = CAST(:tenant_id AS uuid) OR tenant_id = '00000000-0000-0000-0000-000000000001'::uuid)
            """),
            {"chunk_id": chunk_id, "tenant_id": str(tenant.id)},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Case: {row.case_name or 'Unknown'}\n"
                        f"Citation: {row.citation or 'N/A'}\n"
                        f"Court: {row.court or 'N/A'}\n"
                        f"Date: {row.decision_date or 'N/A'}\n\n"
                        f"{row.content}"
                    ),
                }
            ],
            "isError": False,
        }

    else:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {body.name}")


@router.post("/messages")
async def mcp_messages(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retired pseudo-transport; use the official Streamable HTTP endpoint."""
    raise HTTPException(
        status_code=410,
        detail="The legacy messages adapter is retired; connect to /api/mcp",
    )


@router.get("/sse")
async def mcp_sse_endpoint(request: Request):
    """Retired one-event SSE shim; use the official Streamable HTTP endpoint."""
    raise HTTPException(
        status_code=410,
        detail="The legacy SSE adapter is retired; connect to /api/mcp",
    )


def _format_chunk(index: int, chunk: dict) -> str:
    parts = [
        f"[{index}] {chunk.get('case_name', 'Unknown Case')}",
        f"Citation: {chunk.get('citation', 'N/A')}",
    ]
    if chunk.get("court"):
        parts.append(f"Court: {chunk['court']}")
    if chunk.get("decision_date"):
        parts.append(f"Date: {chunk['decision_date']}")
    parts.append(f"Relevance: {chunk.get('similarity', 0):.1%}")
    parts.append(f"\n{chunk.get('content', '')}")
    return "\n".join(parts)


# ── API key management ────────────────────────────────────────────────────────


@router.post("/api-key")
async def regenerate_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """The unscoped tenant-key credential class has been retired."""
    await get_current_user(request, db)
    raise HTTPException(
        status_code=410,
        detail="Legacy MCP API key issuance is retired; use scoped product keys",
    )


@router.get("/api-key")
async def get_api_key_info(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """The unscoped tenant-key credential class has been retired."""
    await get_current_user(request, db)
    raise HTTPException(
        status_code=410,
        detail="Legacy MCP API keys are retired; use scoped product keys",
    )


@router.get("/product-keys")
async def list_mcp_product_keys(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    keys = await list_product_keys(db, user.tenant_id)
    summary = await usage_summary(db, user.tenant_id)
    usage_by_key = {
        row["product_key_id"]: row
        for row in summary.get("by_key", [])
        if row.get("product_key_id")
    }
    mcp_url = settings.research_mcp_endpoint
    return {
        "keys": [
            {
                "id": str(key.id),
                "name": key.name,
                "api_key_masked": mask_key(key.key_prefix, key.key_hash[-4:]),
                "allowed_tools": key.allowed_tools or DEFAULT_ALLOWED_TOOLS,
                "monthly_call_limit": key.monthly_call_limit,
                "burst_limit_per_minute": key.burst_limit_per_minute,
                "is_active": key.is_active,
                "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                "last_used_at": key.last_used_at.isoformat()
                if key.last_used_at
                else None,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "usage": usage_by_key.get(str(key.id), {"calls": 0, "results": 0}),
            }
            for key in keys
        ],
        "usage": summary,
        "metering_outbox": await metering_outbox_summary(db, user.tenant_id),
        "product_enabled": settings.MCP_PRODUCT_ENABLED,
        "entitlement_status": tenant.mcp_entitlement_status,
        "billing_status": tenant.mcp_billing_status,
        "tools": await _proxied_tool_names(request)
        if settings.MCP_PRODUCT_ENABLED
        else [],
        "mcp_server_url": mcp_url if settings.MCP_PRODUCT_ENABLED else None,
        "shorthand": settings.research_mcp_shorthand,
        "transports": (
            {
                "streamable_http": mcp_url,
                "rest_compatibility": f"{mcp_url}/tools/call",
            }
            if settings.MCP_PRODUCT_ENABLED
            else {}
        ),
        "auth_header": "X-MCP-API-Key",
        "billing": {
            "mode": "metered_usage",
            "meter": "mcp_product_key_calls",
            "line_item": "MCP usage",
        },
    }


@router.post("/product-keys")
async def create_mcp_product_key(
    body: ProductKeyCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if body.monthly_call_limit is not None and body.monthly_call_limit < 1:
        raise HTTPException(
            status_code=400, detail="monthly_call_limit must be positive"
        )
    if body.burst_limit_per_minute is not None and body.burst_limit_per_minute < 1:
        raise HTTPException(
            status_code=400, detail="burst_limit_per_minute must be positive"
        )
    key, raw_key = await create_product_key(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        name=body.name,
        monthly_call_limit=body.monthly_call_limit,
        burst_limit_per_minute=body.burst_limit_per_minute,
        allowed_tools=body.allowed_tools or None,
    )
    return {
        "id": str(key.id),
        "api_key": raw_key,
        "api_key_masked": mask_key(raw_key),
        "name": key.name,
        "allowed_tools": key.allowed_tools or DEFAULT_ALLOWED_TOOLS,
        "monthly_call_limit": key.monthly_call_limit,
        "burst_limit_per_minute": key.burst_limit_per_minute,
        "is_active": key.is_active,
    }


@router.delete("/product-keys/{key_id}")
async def revoke_mcp_product_key(
    key_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    revoked = await revoke_product_key(db, tenant_id=user.tenant_id, key_id=key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="MCP API key not found")
    return {"revoked": True}


@router.get("/usage")
async def get_mcp_usage(
    request: Request,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return await usage_summary(db, user.tenant_id, days=days)
