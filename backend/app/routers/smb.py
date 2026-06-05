"""SMB file share relay agent API endpoints."""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.smb_auth import get_smb_agent
from app.middleware.tenant import get_current_user, require_admin
from app.models.smb_agent import SmbAgent
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_share import SmbShare
from app.schemas.smb import (
    AgentHeartbeatRequest,
    AgentInfo,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentStatusUpdate,
    ContentFetchResult,
    MatterSmbShareCreate,
    MatterSmbShareInfo,
    PairingCodeResponse,
    ShareCreate,
    ShareInfo,
    ShareUpdate,
    SmbAccessLogEntry,
    SmbSearchResult,
    SyncRequest,
    SyncResponse,
)
from app.services.smb import smb_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/smb", tags=["smb"])


# ═══════════════════════════════════════════════════════════════════════
#  Agent-facing endpoints (API key auth)
# ═══════════════════════════════════════════════════════════════════════


@router.post("/agents/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Validate pairing code and register a new agent. Returns API key (one time)."""
    try:
        result = await smb_service.register_agent(db, body.pairing_code, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()
    return result


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: str,
    body: AgentHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Update heartbeat timestamp for an authenticated agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    await smb_service.record_heartbeat(db, agent_id, body.model_dump(exclude_none=True))
    await db.commit()
    return {"status": "ok"}


@router.post("/agents/{agent_id}/sync", response_model=SyncResponse)
async def agent_sync(
    agent_id: str,
    share_id: str = Query(..., description="Share ID being synced"),
    body: SyncRequest = Body(...),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Bulk file metadata sync from agent. Upserts files, marks deletions."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    tenant_id = str(agent.tenant_id)
    result = await smb_service.sync_files(db, agent_id, tenant_id, share_id, body)
    await db.commit()
    return result


@router.get("/agents/{agent_id}/tasks")
async def get_agent_tasks(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
    limit: int = Query(10, ge=1, le=50),
):
    """Get pending content fetch tasks for this agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    redis = getattr(request.app.state, "redis", None)
    tasks = await smb_service.get_pending_tasks(db, agent_id, redis=redis, limit=limit)
    return tasks


@router.post("/agents/{agent_id}/tasks/{task_id}/result")
async def submit_task_result(
    agent_id: str,
    task_id: str,
    body: ContentFetchResult,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Submit content fetch result from agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    redis = getattr(request.app.state, "redis", None)
    await smb_service.submit_task_result(db, agent_id, task_id, body, redis=redis)
    return {"status": "ok"}


@router.get("/agents/{agent_id}/shares", response_model=list[ShareInfo])
async def list_agent_shares(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """List shares assigned to this agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    tenant_id = str(agent.tenant_id)
    shares = await smb_service.list_shares(db, agent_id, tenant_id)
    return [ShareInfo.model_validate(s) for s in shares]


# ═══════════════════════════════════════════════════════════════════════
#  User-facing endpoints (JWT auth)
# ═══════════════════════════════════════════════════════════════════════


@router.get("/files/search", response_model=list[SmbSearchResult])
async def search_files(
    q: str = Query(..., min_length=1),
    matter_id: str | None = Query(None),
    file_extensions: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Full-text search across indexed SMB files."""
    tenant_id = str(user.tenant_id)
    ext_list = file_extensions.split(",") if file_extensions else None

    results = await smb_service.search_files(
        db, tenant_id, q, matter_id, ext_list, limit
    )
    return results


@router.post("/files/{file_id}/fetch-content")
async def request_content_fetch(
    file_id: str,
    reason: str = Query("search_result"),
    conversation_id: str | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Request on-demand content fetch from agent. Returns task_id for polling."""
    tenant_id = str(user.tenant_id)
    user_id = str(user.id)
    redis = getattr(request.app.state, "redis", None) if request else None

    try:
        task_id, agent_id = await smb_service.request_content_fetch(
            db,
            tenant_id,
            user_id,
            file_id,
            conversation_id,
            reason,
            redis=redis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await db.commit()
    return {"task_id": task_id, "agent_id": agent_id}


@router.get("/files/{file_id}/content-status")
async def get_content_status(
    file_id: str,
    task_id: str = Query(...),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Poll for content fetch result by task_id."""
    _result = await db.execute(
        select(SmbFileIndex).where(
            SmbFileIndex.id == file_id,
            SmbFileIndex.tenant_id == user.tenant_id,
            SmbFileIndex.is_deleted.is_(False),
        )
    )
    if _result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="File not found")

    redis = getattr(request.app.state, "redis", None) if request else None
    content = await smb_service.get_content_result(task_id, redis=redis)
    if content is None:
        return {"status": "pending"}
    return {"status": "ready", "content": content}


# ═══════════════════════════════════════════════════════════════════════
#  Admin endpoints (JWT + admin role)
# ═══════════════════════════════════════════════════════════════════════


@router.post("/pairing-code", response_model=PairingCodeResponse)
async def generate_pairing_code(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Generate a pairing code for agent registration."""
    tenant_id = str(admin.tenant_id)
    code, expires_at = await smb_service.generate_pairing_code(db, tenant_id)
    await db.commit()
    return PairingCodeResponse(pairing_code=code, expires_at=expires_at)


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """List all agents for this tenant."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(SmbAgent).where(SmbAgent.tenant_id == admin.tenant_id)
    )
    agents = result.scalars().all()
    return [AgentInfo.model_validate(a) for a in agents]


@router.patch("/agents/{agent_id}", response_model=AgentInfo)
async def update_agent_status(
    agent_id: str,
    body: AgentStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Update agent status (pause/resume/revoke)."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id,
            SmbAgent.tenant_id == admin.tenant_id,
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    valid_transitions = {
        "active": ["paused", "revoked"],
        "paused": ["active", "revoked"],
        "revoked": [],
    }
    allowed = valid_transitions.get(agent.status, [])
    if body.status not in allowed and body.status != agent.status:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from {agent.status} to {body.status}",
        )

    agent.status = body.status
    await db.flush()
    await db.commit()

    await db.refresh(agent)
    return AgentInfo.model_validate(agent)


@router.delete("/agents/{agent_id}")
async def revoke_agent(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Revoke agent (soft delete via status='revoked')."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id,
            SmbAgent.tenant_id == admin.tenant_id,
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.status = "revoked"
    await db.flush()
    await db.commit()
    return {"status": "revoked", "agent_id": agent_id}


@router.post("/shares", response_model=ShareInfo)
async def create_share(
    body: ShareCreate,
    agent_id: str = Query(..., description="Agent ID to assign this share to"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Create a new SMB share on an agent."""
    tenant_id = str(admin.tenant_id)

    agent_result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id,
            SmbAgent.tenant_id == admin.tenant_id,
        )
    )
    if agent_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    share = await smb_service.create_share(db, agent_id, tenant_id, body)
    await db.commit()

    await db.refresh(share)
    return ShareInfo.model_validate(share)


@router.get("/shares", response_model=list[ShareInfo])
async def list_shares(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
    agent_id: str | None = Query(None),
):
    """List all shares, optionally filtered by agent."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(SmbShare).where(SmbShare.tenant_id == admin.tenant_id)
    if agent_id:
        stmt = stmt.where(SmbShare.agent_id == agent_id)

    result = await db.execute(stmt)
    shares = result.scalars().all()
    return [ShareInfo.model_validate(s) for s in shares]


@router.patch("/shares/{share_id}", response_model=ShareInfo)
async def update_share(
    share_id: str,
    body: ShareUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Update SMB share configuration."""
    tenant_id = str(admin.tenant_id)

    try:
        share = await smb_service.update_share(db, share_id, tenant_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await db.commit()
    await db.refresh(share)
    return ShareInfo.model_validate(share)


@router.delete("/shares/{share_id}")
async def delete_share(
    share_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Delete an SMB share and its file index entries."""
    tenant_id = str(admin.tenant_id)
    await smb_service.delete_share(db, share_id, tenant_id)
    await db.commit()
    return {"status": "deleted", "share_id": share_id}


@router.get("/stats")
async def get_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Admin dashboard stats for SMB feature."""
    tenant_id = str(admin.tenant_id)
    stats = await smb_service.get_admin_stats(db, tenant_id)
    return stats


@router.get("/access-log", response_model=list[SmbAccessLogEntry])
async def get_access_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent content fetch access log for audit."""
    tenant_id = str(admin.tenant_id)
    from app.models.smb_access_log import SmbAccessLog as SmbAccessLogModel

    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(SmbAccessLogModel)
        .where(SmbAccessLogModel.tenant_id == admin.tenant_id)
        .order_by(SmbAccessLogModel.accessed_at.desc())
        .limit(limit)
    )
    entries = result.scalars().all()
    return [SmbAccessLogEntry.model_validate(e) for e in entries]


# ═══════════════════════════════════════════════════════════════════════
#  Matter binding endpoints (JWT auth)
# ═══════════════════════════════════════════════════════════════════════


@router.post("/matters/{matter_id}/smb-shares", response_model=MatterSmbShareInfo)
async def create_matter_binding(
    matter_id: str,
    body: MatterSmbShareCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Bind an SMB share/folder to a matter."""
    tenant_id = str(user.tenant_id)

    try:
        binding = await smb_service.create_matter_binding(
            db, matter_id, tenant_id, body
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()

    share_result = await db.execute(
        select(SmbShare).where(SmbShare.id == body.share_id)
    )
    share = share_result.scalar_one_or_none()

    info = MatterSmbShareInfo.model_validate(binding)
    info = info.model_copy(update={"share_path": share.share_path if share else None})
    return info


@router.get("/matters/{matter_id}/smb-shares", response_model=list[MatterSmbShareInfo])
async def list_matter_bindings(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List SMB bindings for a matter."""
    tenant_id = str(user.tenant_id)
    bindings = await smb_service.list_matter_bindings(db, matter_id, tenant_id)

    results = []
    for b in bindings:
        share_result = await db.execute(
            select(SmbShare).where(SmbShare.id == b.share_id)
        )
        share = share_result.scalar_one_or_none()
        info = MatterSmbShareInfo.model_validate(b)
        info = info.model_copy(
            update={"share_path": share.share_path if share else None}
        )
        results.append(info)
    return results


@router.delete("/matters/{matter_id}/smb-shares/{binding_id}")
async def delete_matter_binding(
    matter_id: str,
    binding_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Remove an SMB share binding from a matter."""
    tenant_id = str(user.tenant_id)
    await smb_service.delete_matter_binding(db, binding_id, tenant_id)
    await db.commit()
    return {"status": "deleted", "binding_id": binding_id}
