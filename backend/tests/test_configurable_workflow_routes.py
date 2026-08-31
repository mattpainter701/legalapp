import uuid

import pytest
from sqlalchemy import func, select

from app.models.configurable_workflow import (
    CustomFieldDefinition,
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.models.rbac import Role, UserRole
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User


DEFINITION = {
    "initial_stage_key": "initial",
    "stages": [{"stage_key": "initial", "label": "Initial"}],
    "checklist": [
        {
            "item_key": "review",
            "stage_key": "initial",
            "title": "Review file",
            "task_type": "review",
            "priority": "medium",
            "due_offset_days": 2,
            "assignee_role": "matter_owner",
        }
    ],
    "required_field_definition_ids": [],
}


async def _grant(db_session, test_tenant, test_user, capabilities):
    role = Role(
        tenant_id=test_tenant.id,
        name=f"Workflow role {uuid.uuid4()}",
        capabilities=list(capabilities),
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            role_id=role.id,
            source="manual",
        )
    )
    await db_session.commit()
    return role


@pytest.mark.asyncio
async def test_workflow_authoring_and_approval_are_independently_enforced(
    client, db_session, test_tenant, test_user
):
    denied = await client.post(
        "/api/workflow-config/fields",
        json={
            "entity_type": "matter",
            "field_key": "court",
            "label": "Court",
            "field_type": "text",
        },
    )
    assert denied.status_code == 403
    assert await db_session.scalar(select(func.count(CustomFieldDefinition.id))) == 0

    role = await _grant(
        db_session, test_tenant, test_user, capabilities=["manage_workflows"]
    )
    created = await client.post(
        "/api/workflow-config/templates",
        json={"name": "Opening", "description": "Bounded opening", **DEFINITION},
    )
    assert created.status_code == 201, created.text
    template = created.json()

    denied_approval = await client.post(
        f"/api/workflow-config/templates/{template['id']}/versions/"
        f"{template['version_id']}/approve"
    )
    assert denied_approval.status_code == 403
    version = await db_session.get(
        MatterWorkflowTemplateVersion, uuid.UUID(template["version_id"])
    )
    await db_session.refresh(version)
    assert version.status == "draft"
    assert version.approved_by_user_id is None

    role.capabilities = ["approve_legal_work"]
    await db_session.commit()
    approval_review = await client.get("/api/workflow-config/templates")
    assert approval_review.status_code == 200, approval_review.text
    review = approval_review.json()["items"][0]
    assert review["version_id"] == template["version_id"]
    assert review["template_description"] == "Bounded opening"
    assert review["initial_stage_key"] == "initial"
    assert review["stages"] == [{"stage_key": "initial", "label": "Initial"}]
    assert review["checklist"][0] == {
        "item_key": "review",
        "stage_key": "initial",
        "title": "Review file",
        "description": None,
        "task_type": "review",
        "priority": "medium",
        "due_offset_days": 2,
        "assignee_role": "matter_owner",
    }
    assert review["required_fields"] == []
    denied_authoring = await client.post(
        "/api/workflow-config/fields",
        json={
            "entity_type": "matter",
            "field_key": "judge",
            "label": "Judge",
            "field_type": "text",
        },
    )
    assert denied_authoring.status_code == 403
    assert await db_session.scalar(select(func.count(CustomFieldDefinition.id))) == 0

    approved = await client.post(
        f"/api/workflow-config/templates/{template['id']}/versions/"
        f"{template['version_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"workflow-auth-{uuid.uuid4()}",
        matter_name="Workflow authorization matter",
        stage="New",
    )
    db_session.add(matter)
    role.capabilities = ["approve_legal_work", "manage_matters"]
    await db_session.commit()
    preview = await client.post(
        f"/api/matters/{matter.id}/workflow-runs/preview",
        params={"template_version_id": template["version_id"]},
        headers={"Idempotency-Key": "route-auth-preview"},
    )
    assert preview.status_code == 201, preview.text
    preview_body = preview.json()

    role.capabilities = ["approve_legal_work"]
    await db_session.commit()
    denied_apply = await client.post(
        f"/api/matters/{matter.id}/workflow-runs/{preview_body['run_id']}/apply",
        json={
            "preview_sha256": preview_body["preview_sha256"],
            "confirm_apply": True,
        },
    )
    assert denied_apply.status_code == 403
    await db_session.refresh(matter)
    assert matter.stage == "New"
    assert await db_session.scalar(select(func.count(Task.id))) == 0

    role.capabilities = ["approve_legal_work", "manage_matters"]
    await db_session.commit()
    applied = await client.post(
        f"/api/matters/{matter.id}/workflow-runs/{preview_body['run_id']}/apply",
        json={
            "preview_sha256": preview_body["preview_sha256"],
            "confirm_apply": True,
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert await db_session.scalar(select(func.count(Task.id))) == 1

    role.capabilities = ["approve_legal_work"]
    await db_session.commit()
    denied_rollback = await client.post(
        f"/api/matters/{matter.id}/workflow-runs/{preview_body['run_id']}/rollback",
        json={"reason": "Authorization rehearsal"},
        headers={"Idempotency-Key": "route-auth-rollback"},
    )
    assert denied_rollback.status_code == 403

    role.capabilities = ["approve_legal_work", "manage_matters"]
    await db_session.commit()
    rolled_back = await client.post(
        f"/api/matters/{matter.id}/workflow-runs/{preview_body['run_id']}/rollback",
        json={"reason": "Authorization rehearsal"},
        headers={"Idempotency-Key": "route-auth-rollback"},
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_workflow_approval_hides_foreign_tenant_ids(
    client, db_session, test_tenant, test_user
):
    await _grant(
        db_session, test_tenant, test_user, capabilities=["approve_legal_work"]
    )
    foreign_tenant = Tenant(
        id=uuid.uuid4(),
        name="Foreign workflow firm",
        domain=f"foreign-workflow-{uuid.uuid4()}.invalid",
    )
    foreign_user = User(
        id=uuid.uuid4(),
        tenant_id=foreign_tenant.id,
        email=f"foreign-workflow-{uuid.uuid4()}@example.invalid",
        role="user",
    )
    foreign_template = MatterWorkflowTemplate(
        id=uuid.uuid4(),
        tenant_id=foreign_tenant.id,
        name="Private foreign workflow",
        created_by_user_id=foreign_user.id,
    )
    foreign_version = MatterWorkflowTemplateVersion(
        id=uuid.uuid4(),
        tenant_id=foreign_tenant.id,
        template_id=foreign_template.id,
        version=1,
        status="draft",
        initial_stage_key="initial",
        definition_sha256="a" * 64,
        created_by_user_id=foreign_user.id,
    )
    # These security fixtures intentionally use scalar composite FK identifiers
    # rather than ORM relationships. Persist each parent before its child so
    # PostgreSQL—not incidental unit-of-work insertion order—defines the setup.
    db_session.add(foreign_tenant)
    await db_session.flush()
    db_session.add(foreign_user)
    await db_session.flush()
    db_session.add(foreign_template)
    await db_session.flush()
    db_session.add(foreign_version)
    await db_session.commit()

    response = await client.post(
        f"/api/workflow-config/templates/{foreign_template.id}/versions/"
        f"{foreign_version.id}/approve"
    )
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "Workflow template not found"
    assert uuid.UUID(body["request_id"])
    await db_session.refresh(foreign_version)
    assert foreign_version.status == "draft"
