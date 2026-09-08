"""Grant-scoped infrastructure budgets, independent of AI pricing and balances."""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request

from app.config import get_settings
from app.services.automation_capabilities import CapabilityError
from app.services.mcp_transport_security import (
    _enforce_dual_principal_limit,
    _minute_window,
)

settings = get_settings()


async def enforce_workspace_grant_call_budget(request: Request, identity: Any) -> None:
    """Count actual tool calls across tokens and API workers for one grant."""
    bucket, ttl = _minute_window()
    await _enforce_dual_principal_limit(
        request,
        primary_key=(
            f"rate:mcp:workspace:grant:{identity.tenant_id}:{identity.grant_id}:{bucket}"
        ),
        tenant_key=f"rate:mcp:workspace:tool-tenant:{identity.tenant_id}:{bucket}",
        primary_limit=settings.WORKSPACE_MCP_GRANT_CALLS_PER_MINUTE,
        tenant_limit=settings.WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE,
        window_ttl=ttl,
        unavailable_detail="Workspace MCP grant budget is unavailable",
        primary_limit_detail="Workspace MCP grant call budget exceeded",
        tenant_limit_detail="Workspace MCP tenant tool budget exceeded",
    )


def bounded_workspace_read_result(result: dict[str, Any]) -> int:
    """Reject an oversized read without truncating evidence or returning content.

    Count both MCP representations (text and structuredContent), including JSON
    escaping of the nested text. Proposal results are not rejected after a
    handler has potentially materialized an artifact in tenant cloud storage.
    """
    payload = json.dumps(result, ensure_ascii=False, default=str)
    envelope = {
        "content": [{"type": "text", "text": payload}],
        "structuredContent": json.loads(payload),
        "isError": False,
    }
    size = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
    if size > settings.WORKSPACE_MCP_READ_RESULT_MAX_BYTES:
        raise CapabilityError(
            "result_size_exceeded",
            "Workspace read result exceeds the response budget; narrow the query "
            "or request fewer results.",
        )
    return size
