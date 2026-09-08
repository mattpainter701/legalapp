"""Firm control plane for named unattended preparation services."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import get_db, set_tenant_context
from app.models.automation_service import AutomationServiceIdentity, AutomationServiceRule
from app.services.access_control import require_capabilities
from app.services.automation_capabilities import CapabilityError
from app.services.automation_service_contract import ServiceIdentityInput, ServiceRuleInput
from app.services.automation_services import (
    approve_rule,
    create_identity,
    create_rule,
    set_rule_status,
)


router = APIRouter(prefix="/api/automation-services", tags=["automation-services"])
manage = require_capabilities("manage_workflows", "manage_matters")
review = require_capabilities("approve_legal_work", "manage_matters")


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class StatusRequest(VersionRequest):
    status: str


def _error(error):
    return HTTPException(status_code=404 if error.code.endswith("not_found") else 409,
                         detail={"code": error.code, "message": error.message})


def identity_response(row):
    return {"id": str(row.id), "name": row.name, "capabilities": row.capabilities,
            "status": row.status, "version": row.version, "created_at": row.created_at}


def rule_response(row):
    # Product routes intentionally expose reviewable schedule and hashes, not
    # encrypted literal inputs or source contents.
    return {"id": str(row.id), "name": row.name, "identity_id": str(row.identity_id),
            "matter_id": str(row.matter_id), "source_run_id": str(row.source_run_id),
            "schedule": row.schedule, "status": row.status, "version": row.version,
            "definition_sha256": row.definition_sha256, "plan_sha256": row.plan_sha256,
            "approved_by_user_id": str(row.approved_by_user_id) if row.approved_by_user_id else None,
            "approved_at": row.approved_at, "created_at": row.created_at}


@router.get("")
async def list_services(db=Depends(get_db), user=Depends(manage)):
    await set_tenant_context(db, str(user.tenant_id))
    identities = (await db.scalars(select(AutomationServiceIdentity).where(
        AutomationServiceIdentity.tenant_id == user.tenant_id
    ).order_by(AutomationServiceIdentity.created_at.desc()))).all()
    rules = (await db.scalars(select(AutomationServiceRule).where(
        AutomationServiceRule.tenant_id == user.tenant_id
    ).order_by(AutomationServiceRule.created_at.desc()))).all()
    return {"identities": [identity_response(row) for row in identities],
            "rules": [rule_response(row) for row in rules]}


@router.post("/identities", status_code=201)
async def add_identity(body: ServiceIdentityInput, db=Depends(get_db), user=Depends(manage)):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        row = await create_identity(db, tenant_id=user.tenant_id, actor=user, body=body)
        await db.commit()
        return identity_response(row)
    except CapabilityError as error:
        raise _error(error) from error


@router.post("/rules", status_code=201)
async def add_rule(body: ServiceRuleInput, db=Depends(get_db), user=Depends(manage)):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        row = await create_rule(db, tenant_id=user.tenant_id, actor=user, body=body)
        await db.commit()
        return rule_response(row)
    except CapabilityError as error:
        raise _error(error) from error


@router.post("/rules/{rule_id}/approve")
async def approve(rule_id: uuid.UUID, body: VersionRequest, db=Depends(get_db), user=Depends(review)):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        row = await approve_rule(db, tenant_id=user.tenant_id, actor=user,
                                 rule_id=rule_id, expected_version=body.expected_version)
        await db.commit()
        return rule_response(row)
    except CapabilityError as error:
        raise _error(error) from error


@router.post("/rules/{rule_id}/status")
async def status(rule_id: uuid.UUID, body: StatusRequest, db=Depends(get_db), user=Depends(manage)):
    await set_tenant_context(db, str(user.tenant_id))
    try:
        row = await set_rule_status(db, tenant_id=user.tenant_id, actor=user, rule_id=rule_id,
                                    expected_version=body.expected_version, status=body.status)
        await db.commit()
        return rule_response(row)
    except CapabilityError as error:
        raise _error(error) from error
