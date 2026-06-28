"""
MCP (Model Context Protocol) server endpoint.

Exposes Clarity Legal as an MCP tool provider so Claude Desktop and
other MCP-compatible clients can query case law and run legal skills.

Auth: Bearer JWT (same as API) OR X-API-Key header matching tenant.api_key.

Endpoints:
  GET  /api/mcp              — server manifest listing available tools
  POST /api/mcp/tools/call   — invoke a tool by name
  POST /api/mcp/api-key      — regenerate the tenant's API key (admin only)
"""

import hashlib
import secrets
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.tenant import Tenant
from app.models.user import User
from app.services.embeddings import EmbeddingService
from app.services.mcp_product import (
    DEFAULT_ALLOWED_TOOLS,
    create_product_key,
    enforce_product_key_quota,
    ensure_tool_allowed,
    list_product_keys,
    mask_key,
    record_mcp_usage,
    resolve_product_key,
    revoke_product_key,
    usage_summary,
)
from app.services.rag import full_rag_query

settings = get_settings()
router = APIRouter(prefix="/mcp", tags=["mcp"])
_embedding_service = EmbeddingService()

_TOOLS = [
    {
        "name": "search_caselaw",
        "description": (
            "Search the Clarity Legal case law database for relevant opinions "
            "using semantic similarity. Returns the top matching excerpts with "
            "case names, citations, courts, and decision dates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Legal question or research query to search for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (1–20, default 8).",
                    "default": 8,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_chunk",
        "description": "Retrieve the full text of a specific case law chunk by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "UUID of the chunk (returned by search_caselaw).",
                }
            },
            "required": ["chunk_id"],
        },
    },
]


def _mcp_proxy_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _forward_auth_headers(request: Request) -> dict[str, str]:
    headers = {}
    authorization = request.headers.get("Authorization")
    api_key = request.headers.get("X-API-Key")
    if authorization:
        headers["Authorization"] = authorization
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


async def _proxy_get(path: str, request: Request):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            _mcp_proxy_url(settings.MCP_SERVER_URL, path),
            headers=_forward_auth_headers(request),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def _proxy_post(path: str, request: Request, payload: dict):
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            _mcp_proxy_url(settings.MCP_SERVER_URL, path),
            json=payload,
            headers=_forward_auth_headers(request),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def _proxied_tool_names(request: Request) -> list[str]:
    if not settings.MCP_SERVER_URL:
        return [t["name"] for t in _TOOLS]
    try:
        manifest = await _proxy_get("/api/mcp", request)
    except Exception:
        return [t["name"] for t in _TOOLS]
    tools = manifest.get("tools") if isinstance(manifest, dict) else None
    if not isinstance(tools, list):
        return [t["name"] for t in _TOOLS]
    return [tool.get("name") for tool in tools if isinstance(tool, dict) and tool.get("name")]


# ── Auth helper (JWT or API key) ──────────────────────────────────────────────


async def _get_user_and_tenant(
    request: Request,
    db: AsyncSession,
) -> tuple[User, Tenant]:
    """Authenticate via JWT Bearer or X-API-Key header."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        result = await db.execute(
            select(Tenant).where(Tenant.api_key_hash == key_hash)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid API key")
        await set_tenant_context(db, str(tenant.id))
        # Return a synthetic user-like object for downstream use
        user_result = await db.execute(
            select(User)
            .where(User.tenant_id == tenant.id, User.role == "admin")
            .limit(1)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=403, detail="No admin user found for this tenant"
            )
        return user, tenant

    user = await get_current_user(request, db)
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return user, tenant


async def _require_mcp_identity(request: Request, db: AsyncSession) -> tuple[User, Tenant]:
    user, tenant = await _get_user_and_tenant(request, db)
    await set_tenant_context(db, str(tenant.id))
    return user, tenant


# ── Manifest ──────────────────────────────────────────────────────────────────


@router.get("")
async def mcp_manifest(request: Request):
    """Return the MCP server manifest with available tools."""
    if settings.MCP_SERVER_URL:
        return await _proxy_get("/api/mcp", request)
    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": "clarity-legal",
            "version": "1.0.0",
            "description": "Clarity Legal — AI legal research and practice tools",
        },
        "capabilities": {"tools": {}},
        "tools": _TOOLS,
    }


# ── Tool invocation ───────────────────────────────────────────────────────────


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


class ProductKeyCreateRequest(BaseModel):
    name: str = "MCP API key"
    monthly_call_limit: int | None = None
    allowed_tools: list[str] | None = None


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


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
    await enforce_product_key_quota(db, product_key)
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
    except HTTPException as exc:
        await record_mcp_usage(
            db=db,
            tenant_id=tenant.id,
            product_key_id=product_key.id,
            auth_type="product_key",
            transport=transport,
            tool_name=body.name,
            status_code=exc.status_code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            ip_address=_request_ip(request),
            user_agent=request.headers.get("User-Agent"),
            error_class="HTTPException",
            query_text=str((body.arguments or {}).get("query") or "")[:2000] or None,
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
            auth_type="legacy_tenant_key" if request.headers.get("X-API-Key") else "jwt",
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
    """Streamable-HTTP compatible JSON-RPC tools/call adapter."""
    call = parse_mcp_message_body(body)
    result = await (
        _call_tool_with_product_key(call, request, db, transport="messages")
        if request.headers.get("X-MCP-API-Key")
        else call_tool(call, request, db)
    )
    if body.get("jsonrpc"):
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": result}
    return result


@router.get("/sse")
async def mcp_sse_endpoint(request: Request):
    """Minimal SSE discovery endpoint for clients that expect an MCP event stream."""
    base_url = str(request.base_url).rstrip("/")
    endpoint = f"{base_url}/api/mcp/messages"

    async def events():
        yield f"event: endpoint\ndata: {endpoint}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


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
    """Generate (or regenerate) the tenant's MCP API key. Admin only."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    raw_key = secrets.token_hex(32)
    tenant.api_key = None
    tenant.api_key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    tenant.api_key_prefix = raw_key[:8]
    await db.commit()

    return {"api_key": raw_key}


@router.get("/api-key")
async def get_api_key_info(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return masked API key and MCP server URL. Admin only."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()

    has_key = bool(tenant and tenant.api_key_hash)
    masked = (tenant.api_key_prefix + "..." + tenant.api_key_hash[-4:]) if has_key else None

    return {
        "has_api_key": has_key,
        "api_key_masked": masked,
        "mcp_server_url": f"{settings.BACKEND_URL}/api/mcp",
        "tools": await _proxied_tool_names(request),
    }


@router.get("/product-keys")
async def list_mcp_product_keys(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    keys = await list_product_keys(db, user.tenant_id)
    summary = await usage_summary(db, user.tenant_id)
    usage_by_key = {
        row["product_key_id"]: row
        for row in summary.get("by_key", [])
        if row.get("product_key_id")
    }
    return {
        "keys": [
            {
                "id": str(key.id),
                "name": key.name,
                "api_key_masked": mask_key(key.key_prefix, key.key_hash[-4:]),
                "allowed_tools": key.allowed_tools or DEFAULT_ALLOWED_TOOLS,
                "monthly_call_limit": key.monthly_call_limit,
                "is_active": key.is_active,
                "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "usage": usage_by_key.get(str(key.id), {"calls": 0, "results": 0}),
            }
            for key in keys
        ],
        "usage": summary,
        "tools": await _proxied_tool_names(request),
        "mcp_server_url": f"{settings.BACKEND_URL}/api/mcp",
        "transports": {
            "rest": f"{settings.BACKEND_URL}/api/mcp/tools/call",
            "messages": f"{settings.BACKEND_URL}/api/mcp/messages",
            "sse": f"{settings.BACKEND_URL}/api/mcp/sse",
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
        raise HTTPException(status_code=400, detail="monthly_call_limit must be positive")
    key, raw_key = await create_product_key(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        name=body.name,
        monthly_call_limit=body.monthly_call_limit,
        allowed_tools=body.allowed_tools,
    )
    return {
        "id": str(key.id),
        "api_key": raw_key,
        "api_key_masked": mask_key(raw_key),
        "name": key.name,
        "allowed_tools": key.allowed_tools or DEFAULT_ALLOWED_TOOLS,
        "monthly_call_limit": key.monthly_call_limit,
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
