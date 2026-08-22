"""Official MCP Streamable HTTP transport for the public product gateway.

The REST endpoints in :mod:`app.routers.mcp` remain compatibility and
administration surfaces. This module owns the protocol endpoint itself and
delegates lifecycle, version negotiation, JSON-RPC validation, notifications,
and Streamable HTTP semantics to the official MCP Python SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import jsonschema
import mcp.types as mcp_types
from fastapi import HTTPException
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.config import get_settings
from app.database import async_session_maker
from app.services.mcp_product import DEFAULT_ALLOWED_TOOLS, resolve_product_key
from app.services.mcp_transport_security import (
    MCPRequestBodyTooLarge,
    buffer_bounded_request,
    enforce_research_request_limit,
)

settings = get_settings()
logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_ENDPOINT_PATH = "/api/mcp"
TOOL_CATALOG_TTL_SECONDS = 300.0
_CONTENT_ADAPTER = TypeAdapter(mcp_types.ContentBlock)
_tool_catalog_cache: tuple[float, str, tuple[mcp_types.Tool, ...]] | None = None
_tool_catalog_lock = asyncio.Lock()


@dataclass(frozen=True)
class MCPProductIdentity:
    """Minimum authenticated identity carried through one protocol request."""

    product_key_id: str
    tenant_id: str
    allowed_tools: frozenset[str]


def _origin_and_host(raw_url: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None, None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, None
    return f"{parsed.scheme}://{parsed.netloc}", parsed.netloc


def _transport_security() -> TransportSecuritySettings:
    """Build an explicit Host/Origin allow-list for SDK DNS-rebinding checks."""

    allowed_hosts: set[str] = set()
    allowed_origins: set[str] = set()
    configured_urls = [
        settings.BACKEND_URL,
        settings.FRONTEND_URL,
        settings.research_mcp_endpoint,
    ]
    configured_urls.extend(
        value.strip()
        for value in settings.EXTRA_CORS_ORIGINS.split(",")
        if value.strip()
    )
    for raw_url in configured_urls:
        origin, host = _origin_and_host(raw_url)
        if origin:
            allowed_origins.add(origin)
        if host:
            allowed_hosts.add(host)

    # Local wildcard ports are useful for the Inspector and test clients, but
    # must not be accepted by a production service with a public hostname.
    if settings.DEV_MODE:
        allowed_hosts.update({"localhost:*", "127.0.0.1:*", "[::1]:*"})
        allowed_origins.update(
            {
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "https://localhost:3000",
            }
        )

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )


protocol_server: Server[None, Request] = Server(
    "clarity-legal",
    version=settings.APP_VERSION or "1.0.0",
    instructions=(
        "Read-only legal research tools. Verify cited authority independently; "
        "tool output is not legal advice."
    ),
)


def _validate_upstream_catalog(payload: Any) -> tuple[mcp_types.Tool, ...]:
    """Validate the complete private-service contract before advertising it."""

    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        raise ValueError("upstream manifest does not contain a tools array")
    annotations = mcp_types.ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    tools: list[mcp_types.Tool] = []
    names: set[str] = set()
    for item in payload["tools"]:
        if not isinstance(item, dict):
            raise ValueError("upstream manifest contains a non-object tool")
        name = item.get("name")
        description = item.get("description")
        schema = item.get("inputSchema")
        if name not in DEFAULT_ALLOWED_TOOLS:
            continue
        if name in names:
            raise ValueError(f"upstream manifest contains duplicate tool {name}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"upstream tool {name} has no description")
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
        ):
            raise ValueError(f"upstream tool {name} has an invalid input schema")
        tools.append(
            mcp_types.Tool(
                name=name,
                description=description.strip(),
                inputSchema=schema,
                annotations=annotations,
            )
        )
        names.add(name)

    missing = set(DEFAULT_ALLOWED_TOOLS) - names
    if missing:
        raise ValueError(
            "upstream manifest is missing product tools: " + ", ".join(sorted(missing))
        )
    return tuple(tools)


async def _load_upstream_tool_catalog(request: Request) -> tuple[mcp_types.Tool, ...]:
    """Fetch the private manifest with the dedicated service credential."""

    if not settings.MCP_SERVER_URL:
        raise HTTPException(
            status_code=503, detail="Private MCP tool service is not configured"
        )
    try:
        # The router proxy injects only MCP_UPSTREAM_API_KEY; it never forwards
        # the customer's product key or application JWT to the private service.
        from app.routers.mcp import _proxy_get

        payload = await _proxy_get("/api/mcp", request)
        return _validate_upstream_catalog(payload)
    except HTTPException as exc:
        raise HTTPException(
            status_code=503, detail="MCP tool catalog is unavailable"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="MCP tool catalog is invalid"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="MCP tool catalog is unavailable"
        ) from exc


async def get_tool_catalog(request: Request) -> tuple[mcp_types.Tool, ...]:
    """Return a fresh, validated catalog with a bounded in-process TTL."""

    global _tool_catalog_cache

    now = time.monotonic()
    source = settings.MCP_SERVER_URL.rstrip("/")
    cached = _tool_catalog_cache
    if cached and cached[0] > now and cached[1] == source:
        return cached[2]

    async with _tool_catalog_lock:
        now = time.monotonic()
        cached = _tool_catalog_cache
        if cached and cached[0] > now and cached[1] == source:
            return cached[2]
        tools = await _load_upstream_tool_catalog(request)
        _tool_catalog_cache = (
            now + TOOL_CATALOG_TTL_SECONDS,
            source,
            tools,
        )
        return tools


def clear_tool_catalog_cache() -> None:
    """Invalidate the process-local catalog cache (primarily for tests)."""

    global _tool_catalog_cache
    _tool_catalog_cache = None


def _request_and_identity() -> tuple[Request, MCPProductIdentity]:
    request = protocol_server.request_context.request
    if not isinstance(request, Request):
        raise RuntimeError("MCP HTTP request context is unavailable")
    identity = request.scope.get("mcp_product_identity")
    if not isinstance(identity, MCPProductIdentity):
        raise RuntimeError("MCP product identity is unavailable")
    return request, identity


@protocol_server.list_tools()
async def list_protocol_tools() -> list[mcp_types.Tool]:
    """Discover only the tools allowed by the authenticated product key."""

    request, identity = _request_and_identity()
    catalog = await get_tool_catalog(request)
    return [tool for tool in catalog if tool.name in identity.allowed_tools]


def _normalize_tool_result(payload: dict[str, Any]) -> mcp_types.CallToolResult:
    content: list[mcp_types.ContentBlock] = []
    structured: dict[str, Any] | None = None

    for raw_item in payload.get("content") or []:
        if not isinstance(raw_item, dict):
            continue
        if raw_item.get("type") == "json":
            candidate = raw_item.get("json")
            if isinstance(candidate, dict):
                structured = candidate
            content.append(
                mcp_types.TextContent(
                    type="text",
                    text=json.dumps(candidate, ensure_ascii=False, default=str),
                )
            )
            continue
        try:
            content.append(_CONTENT_ADAPTER.validate_python(raw_item))
        except ValidationError:
            content.append(
                mcp_types.TextContent(
                    type="text",
                    text=json.dumps(raw_item, ensure_ascii=False, default=str),
                )
            )

    if not content:
        content.append(
            mcp_types.TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False, default=str),
            )
        )

    return mcp_types.CallToolResult(
        content=content,
        structuredContent=structured,
        isError=bool(payload.get("isError", False)),
    )


def _protocol_tool_error(message: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        isError=True,
    )


async def execute_product_tool(
    *, name: str, arguments: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Run a protocol tool through the same policy/metering path as REST."""

    # Imports are lazy to keep the protocol module independent of router import
    # order during FastAPI startup.
    from app.routers.mcp import ToolCallRequest, _call_tool_with_product_key

    async with async_session_maker() as db:
        return await _call_tool_with_product_key(
            ToolCallRequest(name=name, arguments=arguments),
            request,
            db,
            transport="streamable_http",
        )


@protocol_server.call_tool(validate_input=False)
async def call_protocol_tool(
    name: str, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """Invoke an authenticated, scoped tool and return canonical MCP content."""

    request, identity = _request_and_identity()
    if not isinstance(arguments, dict):
        return _protocol_tool_error("Tool arguments must be an object")

    if name not in identity.allowed_tools:
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text", text="MCP key is not allowed to call this tool"
                )
            ],
            isError=True,
        )

    catalog = await get_tool_catalog(request)
    tool = next((item for item in catalog if item.name == name), None)
    if tool is None:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True,
        )
    try:
        jsonschema.validate(instance=arguments, schema=tool.inputSchema)
    except jsonschema.ValidationError as exc:
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text", text=f"Input validation error: {exc.message}"
                )
            ],
            isError=True,
        )
    except (jsonschema.SchemaError, TypeError):
        return _protocol_tool_error("Tool input validation is unavailable")

    try:
        result = await execute_product_tool(
            name=name, arguments=arguments, request=request
        )
    except HTTPException as exc:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(exc.detail))],
            isError=True,
        )
    return _normalize_tool_result(result)


protocol_session_manager = StreamableHTTPSessionManager(
    app=protocol_server,
    event_store=None,
    json_response=True,
    stateless=True,
    security_settings=_transport_security(),
)


async def authenticate_product_request(scope: Scope) -> MCPProductIdentity:
    """Resolve the product key before any MCP protocol message is processed."""

    request = Request(scope)
    raw_key = request.headers.get("X-MCP-API-Key", "")
    if not raw_key:
        raise HTTPException(status_code=401, detail="MCP API key required")

    async with async_session_maker() as db:
        product_key, tenant = await resolve_product_key(db, raw_key)
    allowed_tools = frozenset(product_key.allowed_tools or DEFAULT_ALLOWED_TOOLS)
    identity = MCPProductIdentity(
        product_key_id=str(product_key.id),
        tenant_id=str(tenant.id),
        allowed_tools=allowed_tools,
    )
    await enforce_research_request_limit(request, identity)
    return identity


class MCPProtocolEndpoint:
    """Exact-path ASGI endpoint with fail-closed product authentication."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.MCP_PRODUCT_ENABLED:
            await JSONResponse(
                {"detail": "MCP product endpoint is disabled"}, status_code=404
            )(scope, receive, send)
            return

        if scope.get("method", "").upper() not in {"GET", "POST", "DELETE"}:
            await JSONResponse(
                {"detail": "Method not allowed"},
                status_code=405,
                headers={"Allow": "GET, POST, DELETE"},
            )(scope, receive, send)
            return

        try:
            identity = await authenticate_product_request(scope)
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )(scope, receive, send)
            return
        except Exception:
            logger.exception("Research MCP authentication failed")
            await JSONResponse(
                {"detail": "Research authentication is unavailable"},
                status_code=503,
            )(scope, receive, send)
            return

        authenticated_scope = dict(scope)
        try:
            bounded_receive = await buffer_bounded_request(
                scope,
                receive,
                maximum_bytes=settings.MCP_PROTOCOL_MAX_REQUEST_BYTES,
            )
        except MCPRequestBodyTooLarge:
            await JSONResponse(
                {"detail": "MCP request body exceeds the configured limit"},
                status_code=413,
            )(scope, receive, send)
            return

        authenticated_scope["mcp_product_identity"] = identity
        await protocol_session_manager.handle_request(
            authenticated_scope, bounded_receive, send
        )


protocol_endpoint = MCPProtocolEndpoint()


@asynccontextmanager
async def protocol_lifespan() -> AsyncIterator[None]:
    """Start the SDK session manager inside the parent FastAPI lifespan."""

    async with protocol_session_manager.run():
        yield
