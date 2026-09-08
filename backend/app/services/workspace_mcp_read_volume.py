"""Atomic infrastructure egress budgets, never AI credits or inference pricing."""

from types import SimpleNamespace

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from app.config import get_settings
from app.services.mcp_transport_security import _minute_window
from app.services.automation_capabilities import CapabilityError

settings = get_settings()

# Test both principals before incrementing either. Budget refusals consume no
# egress reservation. Token rotation cannot reset either principal's budget.
READ_VOLUME_SCRIPT = """
local amount = tonumber(ARGV[1])
local grant = tonumber(redis.call('GET', KEYS[1]) or '0')
local tenant = tonumber(redis.call('GET', KEYS[2]) or '0')
if grant + amount > tonumber(ARGV[2]) then return 1 end
if tenant + amount > tonumber(ARGV[3]) then return 2 end
redis.call('INCRBY', KEYS[1], amount)
redis.call('INCRBY', KEYS[2], amount)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
return 0
"""


async def enforce_read_volume(request, identity, size):
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("Read byte count must be nonnegative")
    bucket, ttl = _minute_window()
    redis = getattr(getattr(request.scope.get("app"), "state", None), "redis", None)
    # Only an explicitly database-free development server can omit Redis.
    # If a configured Redis fails, every environment fails closed.
    if redis is None and settings.DEV_MODE:
        return
    try:
        if redis is None:
            raise RedisError("Shared read budget unavailable")
        status = int(
            await redis.eval(
                READ_VOLUME_SCRIPT,
                2,
                f"volume:mcp:workspace:grant:{identity.tenant_id}:{identity.grant_id}:{bucket}",
                f"volume:mcp:workspace:tenant:{identity.tenant_id}:{bucket}",
                size,
                settings.WORKSPACE_MCP_GRANT_READ_BYTES_PER_MINUTE,
                settings.WORKSPACE_MCP_TENANT_READ_BYTES_PER_MINUTE,
                ttl,
            )
        )
        if status not in (0, 1, 2):
            raise ValueError("Invalid budget response")
    except (RedisError, TypeError, ValueError) as error:
        raise HTTPException(503, "Workspace MCP read budget is unavailable") from error
    if status:
        principal = "grant" if status == 1 else "tenant"
        raise HTTPException(
            429,
            f"Workspace MCP {principal} read volume budget exceeded",
            headers={"Retry-After": str(ttl)},
        )


async def enforce_runtime_read_volume(run, size):
    if run.origin_channel != "workspace_mcp":
        return
    async with Redis.from_url(settings.REDIS_URL) as redis:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/mcp/workspace",
                "headers": [],
                "app": SimpleNamespace(state=SimpleNamespace(redis=redis)),
            }
        )
        try:
            await enforce_read_volume(request, run, size)
        except HTTPException as error:
            raise CapabilityError(
                "workflow_rate_limited"
                if error.status_code == 429
                else "workflow_budget_unavailable",
                "The originating grant's read budget is unavailable; continue later",
            ) from error
