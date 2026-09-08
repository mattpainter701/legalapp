"""Human-visible artifact review history and firm review policy."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.generated_artifact import GeneratedArtifact
from app.models.task import Task
from app.models.tenant import Tenant, TenantSettings
from app.models.operator_audit import OperatorAuditLog
from app.models.work_artifact_review import (
    WorkArtifactApproval,
    WorkArtifactDelivery,
    WorkArtifactReviewRequirement,
)
from app.services.access_control import require_capabilities, require_any_capability
from app.services.rbac_service import get_user_capabilities
from app.services.task_workflow import require_review_actor, TaskWorkflowError
from app.services.work_artifact_reviews import artifact_review_policy

router = APIRouter(prefix="/api/artifact-reviews", tags=["artifact-reviews"])


class PolicyUpdate(BaseModel):
    policy: Literal["staff_then_attorney", "attorney_only"]
    expected_policy: Literal["staff_then_attorney", "attorney_only"]


@router.get("/policy")
async def get_policy(
    user=Depends(require_any_capability("manage_matters", "approve_legal_work")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(user.tenant_id))
    caps = await get_user_capabilities(db, user.id)
    return {
        "policy": await artifact_review_policy(db, user.tenant_id),
        "can_edit": {"manage_users", "approve_legal_work"}.issubset(caps),
    }


@router.put("/policy")
async def update_policy(
    payload: PolicyUpdate,
    user=Depends(require_capabilities("manage_users", "approve_legal_work")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(user.tenant_id))
    await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
    current = await artifact_review_policy(db, user.tenant_id)
    if current != payload.expected_policy:
        raise HTTPException(409, "Review policy changed; refresh before saving")
    settings = await db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    if settings is None:
        settings = TenantSettings(tenant_id=user.tenant_id)
        db.add(settings)
    settings.artifact_review_policy = payload.policy
    db.add(
        OperatorAuditLog(
            action="artifact_review.policy_changed",
            actor_type="user",
            actor_id=str(user.id),
            resource_type="tenant",
            resource_id=str(user.tenant_id),
            metadata_json={
                "tenant_id": str(user.tenant_id),
                "previous_policy": current,
                "policy": payload.policy,
            },
        )
    )
    await db.commit()
    return {"policy": payload.policy, "can_edit": True}


@router.get("/{artifact_id}")
async def review_history(
    artifact_id: uuid.UUID,
    user=Depends(require_any_capability("manage_matters", "approve_legal_work")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(user.tenant_id))
    artifact = await db.scalar(
        select(GeneratedArtifact).where(
            GeneratedArtifact.tenant_id == user.tenant_id,
            GeneratedArtifact.id == artifact_id,
        )
    )
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    task = await db.scalar(
        select(Task).where(
            Task.tenant_id == user.tenant_id, Task.id == artifact.task_id
        )
    )
    actions = []
    if task is not None:
        for stage, override in (
            ("staff", False),
            ("attorney", False),
            ("attorney", True),
        ):
            try:
                await require_review_actor(
                    db, task, user, stage=stage, override=override
                )
                actions.append("override" if override else stage)
            except TaskWorkflowError:
                pass
    results = {}
    for label, model in (
        ("requirements", WorkArtifactReviewRequirement),
        ("approvals", WorkArtifactApproval),
        ("deliveries", WorkArtifactDelivery),
    ):
        rows = (
            await db.scalars(
                select(model)
                .where(
                    model.tenant_id == user.tenant_id, model.artifact_id == artifact_id
                )
                .order_by(model.created_at.desc(), model.id.desc())
                .limit(200)
            )
        ).all()
        # Explicit metadata/evidence columns only: no document body or provider URL.
        results[label] = [
            {
                column.name: getattr(row, column.name)
                for column in model.__table__.columns
                if column.name != "tenant_id"
            }
            for row in rows
        ]
    return {
        "artifact_id": artifact.id,
        "current_revision_no": artifact.current_revision_no,
        "task_id": artifact.task_id,
        "task_version": task.version if task else None,
        "policy": task.review_policy if task else None,
        "available_actions": actions,
        **results,
    }
