"""Shared authorization fences for task surfaces that can carry SMS content."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskAutomationRun
from app.models.user import User
from app.services.matter_access import can_access_matter, matter_access_predicate
from app.services.rbac_service import get_user_capabilities


def task_is_sms_expression(*, tenant_id: uuid.UUID):
    """Identify current or historical SMS tasks without trusting mutable JSON alone."""
    historical_sms = (
        select(TaskAutomationRun.id)
        .where(
            TaskAutomationRun.tenant_id == tenant_id,
            TaskAutomationRun.task_id == Task.id,
            TaskAutomationRun.action_type == "sms_client",
        )
        .correlate(Task)
        .exists()
    )
    return or_(
        func.coalesce(Task.pending_action["type"].as_string(), "") == "sms_client",
        historical_sms,
    )


def sms_task_visibility_predicate(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    is_admin: bool,
    has_manage_matters: bool,
):
    """Hide SMS-bearing tasks unless the actor has live matter authorization."""
    is_sms = task_is_sms_expression(tenant_id=tenant_id)
    if not has_manage_matters:
        return ~is_sms
    matter_access = matter_access_predicate(
        tenant_id=tenant_id,
        user_id=user_id,
        is_admin=is_admin,
        matter_id_column=Task.matter_id,
    )
    return or_(
        ~is_sms,
        and_(Task.matter_id.is_not(None), matter_access),
    )


async def task_contains_sms(db: AsyncSession, task: Task) -> bool:
    if str((task.pending_action or {}).get("type") or "") == "sms_client":
        return True
    return bool(
        await db.scalar(
            select(TaskAutomationRun.id)
            .where(
                TaskAutomationRun.tenant_id == task.tenant_id,
                TaskAutomationRun.task_id == task.id,
                TaskAutomationRun.action_type == "sms_client",
            )
            .limit(1)
        )
    )


async def user_can_receive_sms_task(
    db: AsyncSession,
    *,
    task: Task,
    user_id: uuid.UUID,
) -> bool:
    """Recheck a notification/assignment recipient against current RBAC and matter access."""
    if not await task_contains_sms(db, task):
        return True
    if task.matter_id is None:
        return False
    user = await db.scalar(
        select(User).where(
            User.id == user_id,
            User.tenant_id == task.tenant_id,
            User.is_active.is_(True),
        )
    )
    if user is None:
        return False
    capabilities = await get_user_capabilities(db, user.id)
    return "manage_matters" in capabilities and await can_access_matter(
        db,
        tenant_id=task.tenant_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        matter_id=task.matter_id,
    )
