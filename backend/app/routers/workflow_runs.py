"""Firm-visible durable runs; writes use the same handlers as Chat and MCP."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import get_db, set_tenant_context
from app.models.workflow_run import WorkflowRun
from app.services.access_control import require_capabilities
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.matter_access import matter_access_predicate
from app.services.workflow_run_contract import WorkflowRunInput, ResumeRunInput
from app.services.workflow_run_ledger import (
    submit_run,
    resume_run,
    cancel_run,
    describe_run,
)

router = APIRouter(prefix="/api/workflow-runs", tags=["workflow-runs"])
manage = require_capabilities("manage_matters")


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CloudReconciliationRequest(VersionRequest):
    provider_object_id: str = Field(
        min_length=1, max_length=500, pattern=r"^[A-Za-z0-9_!.-]+$"
    )
    reason: str = Field(min_length=1, max_length=1000)


def _error(error):
    return HTTPException(
        status_code=404 if error.code == "run_not_found" else 409,
        detail={"code": error.code, "message": error.message},
    )


@router.get("")
async def list_runs(
    matter_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    user=Depends(manage),
):
    await set_tenant_context(db, str(user.tenant_id))
    statement = select(WorkflowRun).where(
        WorkflowRun.tenant_id == user.tenant_id,
        matter_access_predicate(
            tenant_id=user.tenant_id,
            user_id=user.id,
            is_admin=user.role == "admin",
            matter_id_column=WorkflowRun.matter_id,
        ),
    )
    if matter_id:
        statement = statement.where(WorkflowRun.matter_id == matter_id)
    rows = (
        await db.scalars(
            statement.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id)
            .offset(offset)
            .limit(21)
        )
    ).all()
    return {
        "items": [
            dict(await describe_run(db, row), can_continue=row.actor_user_id == user.id)
            for row in rows[:20]
        ],
        "next_offset": offset + 20 if len(rows) > 20 else None,
    }


@router.post("", status_code=202)
async def create_run(body: WorkflowRunInput, db=Depends(get_db), user=Depends(manage)):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        result = await submit_run(CapabilityContext(db=db, user=user), body)
        await db.commit()
        return result
    except CapabilityError as error:
        raise _error(error) from error


@router.post("/{run_id}/resume", status_code=202)
async def continue_run(
    run_id: uuid.UUID, body: ResumeRunInput, db=Depends(get_db), user=Depends(manage)
):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        result = await resume_run(CapabilityContext(db=db, user=user), run_id, body)
        await db.commit()
        return result
    except CapabilityError as error:
        raise _error(error) from error


@router.post("/{run_id}/cancel")
async def stop_run(
    run_id: uuid.UUID, body: VersionRequest, db=Depends(get_db), user=Depends(manage)
):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        result = await cancel_run(
            CapabilityContext(db=db, user=user), run_id, body.expected_version
        )
        await db.commit()
        return result
    except CapabilityError as error:
        raise _error(error) from error


@router.post("/{run_id}/reconcile-cloud", status_code=202)
async def reconcile_cloud(
    run_id: uuid.UUID,
    body: CloudReconciliationRequest,
    db=Depends(get_db),
    user=Depends(require_capabilities("manage_matters", "manage_documents")),
):
    from app.services.workflow_run_reconciliation import reconcile_run

    await set_tenant_context(db, str(user.tenant_id))
    try:
        result = await reconcile_run(CapabilityContext(db=db, user=user), run_id, body)
        await db.commit()
        return result
    except CapabilityError as error:
        raise _error(error) from error
