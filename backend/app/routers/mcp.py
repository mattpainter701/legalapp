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

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.tenant import Tenant
from app.models.user import User
from app.services.embeddings import EmbeddingService
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


# ── Auth helper (JWT or API key) ──────────────────────────────────────────────


async def _get_user_and_tenant(
    request: Request,
    db: AsyncSession,
) -> tuple[User, Tenant]:
    """Authenticate via JWT Bearer or X-API-Key header."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        result = await db.execute(select(Tenant).where(Tenant.api_key == api_key))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid API key")
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


# ── Manifest ──────────────────────────────────────────────────────────────────


@router.get("")
async def mcp_manifest():
    """Return the MCP server manifest with available tools."""
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


@router.post("/tools/call")
async def call_tool(
    body: ToolCallRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute an MCP tool call."""
    user, tenant = await _get_user_and_tenant(request, db)
    await set_tenant_context(db, str(tenant.id))

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
                  AND (tenant_id = :tenant_id::uuid OR tenant_id = '00000000-0000-0000-0000-000000000001'::uuid)
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

    tenant.api_key = secrets.token_hex(32)
    await db.commit()

    return {"api_key": tenant.api_key}


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

    api_key = tenant.api_key if tenant else None
    masked = (api_key[:8] + "..." + api_key[-4:]) if api_key else None

    return {
        "has_api_key": api_key is not None,
        "api_key_masked": masked,
        "mcp_server_url": f"{settings.BACKEND_URL}/api/mcp",
        "tools": [t["name"] for t in _TOOLS],
    }
