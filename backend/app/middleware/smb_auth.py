"""SMB agent API key authentication middleware."""

import hashlib
import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.smb_agent import SmbAgent

logger = logging.getLogger(__name__)

_SMB_RATE_LIMIT_WINDOW = 60  # seconds
_SMB_RATE_LIMIT_MAX = 30  # requests per window


async def _check_smb_rate_limit(request: Request) -> None:
    """Simple rate limiter for SMB agent API key auth attempts."""
    redis = getattr(request.app.state, "redis", None)
    if not redis:
        return

    client_ip = request.client.host if request.client else "unknown"
    key = f"rate_smb_auth:{client_ip}"
    current = await redis.get(key)

    if current:
        count = int(current)
        if count >= _SMB_RATE_LIMIT_MAX:
            raise HTTPException(
                status_code=429,
                detail="Too many authentication attempts. Try again later.",
            )
        await redis.incr(key)
    else:
        await redis.set(key, 1, ex=_SMB_RATE_LIMIT_WINDOW)


async def get_smb_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SmbAgent:
    """FastAPI dependency that authenticates SMB relay agents via API key.

    Reads the X-Agent-API-Key header, hashes it with SHA-256, and compares
    against the stored api_key_hash on smb_agents. Sets
    request.state.smb_agent_id and request.state.smb_tenant_id on success.

    Raises 401 if key is missing or invalid, 403 if agent is not active.
    """
    api_key = request.headers.get("X-Agent-API-Key")
    if not api_key:
        await _check_smb_rate_limit(request)
        raise HTTPException(status_code=401, detail="Missing X-Agent-API-Key header")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    result = await db.execute(select(SmbAgent).where(SmbAgent.api_key_hash == key_hash))
    agent = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if agent.status != "active":
        raise HTTPException(
            status_code=403,
            detail=f"Agent is {agent.status}",
        )

    tenant_id = str(agent.tenant_id)
    await set_tenant_context(db, tenant_id)

    request.state.smb_agent_id = str(agent.id)
    request.state.smb_tenant_id = tenant_id

    return agent
