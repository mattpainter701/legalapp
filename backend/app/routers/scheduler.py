"""
Scheduler management endpoints — admin only.
Allows viewing agent run history and manually triggering agents.
"""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.scheduler import SchedulerLog
from app.services.durable_jobs import enqueue_job

router = APIRouter(prefix="/scheduler", tags=["scheduler"])

AGENT_REGISTRY = [
    {
        "name": "scheduler-heartbeat",
        "display_name": "Scheduler Heartbeat",
        "description": "Confirms tenant-scoped scheduler processing for production monitoring.",
        "schedule": "Every minute",
    },
    {
        "name": "renewal-watcher",
        "display_name": "Contract Renewal Watcher",
        "description": "Scans renewals due within 90 days and emails tenant admins with urgency-grouped alerts.",
        "schedule": "Every Monday at 8:00 AM ET",
    },
    {
        "name": "reg-monitor",
        "display_name": "Regulatory Feed Monitor",
        "description": "Fetches Federal Register RSS, filters by each tenant's watched agencies, emails digest.",
        "schedule": "Every Monday at 8:00 AM ET",
    },
    {
        "name": "docket-watcher",
        "display_name": "Matter Docket Watcher",
        "description": "Flags active litigation matters with key dates within 14 days.",
        "schedule": "Every Monday at 8:00 AM ET",
    },
    {
        "name": "oc-status",
        "display_name": "Portfolio Status Report",
        "description": "Weekly matter portfolio summary email for each tenant's admin users.",
        "schedule": "Every Monday at 9:00 AM ET",
    },
    {
        "name": "user-sync",
        "display_name": "Directory User Sync",
        "description": "Pulls directory users from connected Google/Microsoft tenants; new users land on the free tier.",
        "schedule": "Daily at 2:00 AM ET",
    },
]


class AgentInfo(BaseModel):
    name: str
    display_name: str
    description: str
    schedule: str
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_run_summary: str | None = None


class SchedulerLogResponse(BaseModel):
    id: str
    agent_name: str
    run_at: datetime
    status: str
    summary: str | None
    error_message: str | None


@router.get("/agents", response_model=List[AgentInfo])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    current_user: object = Depends(require_admin),
):
    """List all scheduled agents with their last run info."""
    result = await db.execute(
        select(SchedulerLog)
        .where(SchedulerLog.tenant_id == current_user.tenant_id)
        .order_by(desc(SchedulerLog.run_at))
        .limit(100)
    )
    logs = result.scalars().all()

    # Build last-run map per agent
    last_run: dict[str, SchedulerLog] = {}
    for log in logs:
        if log.agent_name not in last_run:
            last_run[log.agent_name] = log

    agents = []
    for entry in AGENT_REGISTRY:
        log = last_run.get(entry["name"])
        agents.append(
            AgentInfo(
                name=entry["name"],
                display_name=entry["display_name"],
                description=entry["description"],
                schedule=entry["schedule"],
                last_run_at=log.run_at if log else None,
                last_run_status=log.status if log else None,
                last_run_summary=log.summary if log else None,
            )
        )
    return agents


@router.get("/logs", response_model=List[SchedulerLogResponse])
async def get_logs(
    db: AsyncSession = Depends(get_db),
    current_user: object = Depends(require_admin),
):
    """Return the 50 most recent scheduler run logs."""
    result = await db.execute(
        select(SchedulerLog)
        .where(SchedulerLog.tenant_id == current_user.tenant_id)
        .order_by(desc(SchedulerLog.run_at))
        .limit(50)
    )
    logs = result.scalars().all()
    return [
        SchedulerLogResponse(
            id=str(log.id),
            agent_name=log.agent_name,
            run_at=log.run_at,
            status=log.status,
            summary=log.summary,
            error_message=log.error_message,
        )
        for log in logs
    ]


@router.post("/agents/{agent_name}/run", status_code=202)
async def trigger_agent(
    agent_name: str,
    current_user: object = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Durably queue the supported tenant-scoped manual operation."""
    if agent_name != "user-sync":
        raise HTTPException(
            status_code=410,
            detail="Manual cross-tenant agent triggers are retired",
        )
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    minute = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    job = await enqueue_job(
        db,
        tenant_id=tenant_id,
        kind="user_sync",
        idempotency_key=f"manual-user-sync:{minute}",
        payload={"requested_by_user_id": str(current_user.id)},
    )
    await db.commit()
    return {
        "accepted": True,
        "agent": agent_name,
        "job_id": str(job.id),
        "message": "Tenant directory sync queued.",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }
