"""Authoring, approval, and boundary coverage for automation rules."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.configurable_workflow import MatterWorkflowTemplate
from app.models.rbac import Role, UserRole
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow_automation import MatterWorkflowAutomationRule


DEFINITION = {
    "initial_stage_key": "opening",
    "stages": [{"stage_key": "opening", "label": "Opening"}],
    "checklist": [
        {
            "item_key": "conflicts",
            "stage_key": "opening",
            "title": "Run the conflicts check",
            "task_type": "review",
            "priority": "high",
            "due_offset_days": 1,
            "assignee_role": "matter_owner",
        }
    ],
    "required_field_definition_ids": [],
}


async def grant(db_session, tenant, user, capabilities):
    role = Role(
        tenant_id=tenant.id,
        name=f"Automation role {uuid.uuid4()}",
        capabilities=list(capabilities),
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=role.id,
            source="manual",
        )
    )
    await db_session.commit()
    return role


async def approved_template(client, role, db_session, name="Matter opening"):
    """Author and approve one template through the real capability boundary."""
    role.capabilities = ["manage_workflows"]
    await db_session.commit()
    created = await client.post(
        "/api/workflow-config/templates",
        json={"name": name, "description": "Bounded opening", **DEFINITION},
    )
    assert created.status_code == 201, created.text
    template = created.json()
    role.capabilities = ["approve_legal_work"]
    await db_session.commit()
    approved = await client.post(
        f"/api/workflow-config/templates/{template['id']}/versions/"
        f"{template['version_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    return template


@pytest.mark.asyncio
async def test_rule_authoring_needs_its_capability_and_never_self_activates(
    client, db_session, test_tenant, test_user
):
    denied = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Open every matter",
            "trigger_event": "matter_created",
            "template_id": str(uuid.uuid4()),
        },
    )
    assert denied.status_code == 403
    assert (
        await db_session.scalar(select(func.count(MatterWorkflowAutomationRule.id)))
        == 0
    )

    role = await grant(db_session, test_tenant, test_user, ["manage_workflows"])
    template = await approved_template(client, role, db_session)

    role.capabilities = ["manage_workflows"]
    await db_session.commit()
    missing_template = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Points nowhere",
            "trigger_event": "matter_created",
            "template_id": str(uuid.uuid4()),
        },
    )
    assert missing_template.status_code == 404

    created = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Open every matter",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["status"] == "draft"
    assert rule["activated_by_user_id"] is None
    assert rule["template_name"] == "Matter opening"

    # An author cannot approve their own automation into service.
    self_activation = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert self_activation.status_code == 403
    stored = await db_session.get(MatterWorkflowAutomationRule, uuid.UUID(rule["id"]))
    await db_session.refresh(stored)
    assert stored.status == "draft"


@pytest.mark.asyncio
async def test_stage_trigger_requires_a_stage_and_creation_trigger_forbids_one(
    client, db_session, test_tenant, test_user
):
    role = await grant(db_session, test_tenant, test_user, ["manage_workflows"])
    template = await approved_template(client, role, db_session)
    role.capabilities = ["manage_workflows"]
    await db_session.commit()

    missing_stage = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Stage rule",
            "trigger_event": "matter_stage_changed",
            "template_id": template["id"],
        },
    )
    assert missing_stage.status_code == 422

    stray_stage = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Creation rule",
            "trigger_event": "matter_created",
            "trigger_stage": "Discovery",
            "template_id": template["id"],
        },
    )
    assert stray_stage.status_code == 422
    assert (
        await db_session.scalar(select(func.count(MatterWorkflowAutomationRule.id)))
        == 0
    )


@pytest.mark.asyncio
async def test_activation_matches_the_reviewed_definition_and_an_approved_template(
    client, db_session, test_tenant, test_user
):
    role = await grant(db_session, test_tenant, test_user, ["manage_workflows"])
    template = await approved_template(client, role, db_session)
    role.capabilities = ["manage_workflows"]
    await db_session.commit()
    rule = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Open litigation matters",
                "trigger_event": "matter_created",
                "match_matter_type": "Litigation",
                "template_id": template["id"],
            },
        )
    ).json()

    role.capabilities = ["approve_legal_work"]
    await db_session.commit()
    stale = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={"definition_sha256": "a" * 64, "confirm_activate": True},
    )
    assert stale.status_code == 409
    assert "changed since it was reviewed" in stale.json()["detail"]

    unconfirmed = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": False,
        },
    )
    assert unconfirmed.status_code == 422

    activated = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    assert activated.json()["activated_by_user_id"] == str(test_user.id)

    repeat = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert repeat.status_code == 409


@pytest.mark.asyncio
async def test_activation_is_refused_while_the_template_has_no_approved_version(
    client, db_session, test_tenant, test_user
):
    role = await grant(db_session, test_tenant, test_user, ["manage_workflows"])
    draft_only = (
        await client.post(
            "/api/workflow-config/templates",
            json={"name": "Never approved", "description": "Draft", **DEFINITION},
        )
    ).json()
    rule = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Premature rule",
                "trigger_event": "matter_created",
                "template_id": draft_only["id"],
            },
        )
    ).json()

    role.capabilities = ["approve_legal_work"]
    await db_session.commit()
    refused = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert refused.status_code == 409
    assert "no active, approved version" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_editing_an_active_rule_returns_it_to_draft(
    client, db_session, test_tenant, test_user
):
    role = await grant(
        db_session,
        test_tenant,
        test_user,
        ["manage_workflows", "approve_legal_work"],
    )
    template = await approved_template(client, role, db_session)
    role.capabilities = ["manage_workflows", "approve_legal_work"]
    await db_session.commit()
    rule = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Opening rule",
                "trigger_event": "matter_created",
                "template_id": template["id"],
            },
        )
    ).json()
    activated = (
        await client.post(
            f"/api/workflow-config/automations/{rule['id']}/activate",
            json={
                "definition_sha256": rule["definition_sha256"],
                "confirm_activate": True,
            },
        )
    ).json()
    assert activated["status"] == "active"

    edited = await client.patch(
        f"/api/workflow-config/automations/{rule['id']}",
        json={
            "name": "Opening rule",
            "trigger_event": "matter_stage_changed",
            "trigger_stage": "Discovery",
            "template_id": template["id"],
        },
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["status"] == "draft"
    assert body["activated_by_user_id"] is None
    assert body["definition_sha256"] != rule["definition_sha256"]

    # The edited definition needs its own approval; the old hash is not it.
    reactivated = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert reactivated.status_code == 409

    # A rename is a label change, so it does not spend an approval.
    await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": body["definition_sha256"],
            "confirm_activate": True,
        },
    )
    renamed = await client.patch(
        f"/api/workflow-config/automations/{rule['id']}",
        json={
            "name": "Discovery rule",
            "trigger_event": "matter_stage_changed",
            "trigger_stage": "Discovery",
            "template_id": template["id"],
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Discovery rule"
    assert renamed.json()["status"] == "active"
    assert renamed.json()["definition_sha256"] == body["definition_sha256"]


@pytest.mark.asyncio
async def test_duplicate_names_and_duplicate_active_triggers_are_refused(
    client, db_session, test_tenant, test_user
):
    role = await grant(
        db_session,
        test_tenant,
        test_user,
        ["manage_workflows", "approve_legal_work"],
    )
    template = await approved_template(client, role, db_session)
    role.capabilities = ["manage_workflows", "approve_legal_work"]
    await db_session.commit()
    first = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Opening rule",
                "trigger_event": "matter_created",
                "template_id": template["id"],
            },
        )
    ).json()

    duplicate_name = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "  opening rule ",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    assert duplicate_name.status_code == 409
    assert "already uses that name" in duplicate_name.json()["detail"]

    await client.post(
        f"/api/workflow-config/automations/{first['id']}/activate",
        json={
            "definition_sha256": first["definition_sha256"],
            "confirm_activate": True,
        },
    )
    second = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Second opening rule",
                "trigger_event": "matter_created",
                "template_id": template["id"],
            },
        )
    ).json()
    duplicate_trigger = await client.post(
        f"/api/workflow-config/automations/{second['id']}/activate",
        json={
            "definition_sha256": second["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert duplicate_trigger.status_code == 409
    assert "already plans that template" in duplicate_trigger.json()["detail"]


@pytest.mark.asyncio
async def test_archive_is_terminal_and_listings_hide_it_by_default(
    client, db_session, test_tenant, test_user
):
    role = await grant(db_session, test_tenant, test_user, ["manage_workflows"])
    template = await approved_template(client, role, db_session)
    role.capabilities = ["manage_workflows"]
    await db_session.commit()
    rule = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Retiring rule",
                "trigger_event": "matter_created",
                "template_id": template["id"],
            },
        )
    ).json()

    archived = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/archive"
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None

    repeat = await client.post(f"/api/workflow-config/automations/{rule['id']}/archive")
    assert repeat.status_code == 200
    assert repeat.json()["status"] == "archived"

    edited = await client.patch(
        f"/api/workflow-config/automations/{rule['id']}",
        json={
            "name": "Retiring rule",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    assert edited.status_code == 409

    listed = await client.get("/api/workflow-config/automations")
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    with_archived = await client.get(
        "/api/workflow-config/automations?include_archived=true"
    )
    assert [item["id"] for item in with_archived.json()["items"]] == [rule["id"]]

    # The name is free again once the rule is retired.
    reused = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Retiring rule",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    assert reused.status_code == 201


@pytest.mark.asyncio
async def test_rules_and_their_events_are_tenant_bound(
    client, db_session, test_tenant, test_user
):
    await grant(db_session, test_tenant, test_user, ["manage_workflows"])
    other_tenant = Tenant(name="Other firm", domain=f"other-{uuid.uuid4().hex}.invalid")
    db_session.add(other_tenant)
    await db_session.flush()
    other_user = User(
        tenant_id=other_tenant.id,
        email=f"other-{uuid.uuid4().hex}@example.invalid",
        full_name="Other User",
    )
    db_session.add(other_user)
    await db_session.flush()
    other_template = MatterWorkflowTemplate(
        tenant_id=other_tenant.id,
        name="Foreign template",
        created_by_user_id=other_user.id,
    )
    db_session.add(other_template)
    await db_session.flush()
    foreign_rule = MatterWorkflowAutomationRule(
        tenant_id=other_tenant.id,
        name="Foreign rule",
        trigger_event="matter_created",
        template_id=other_template.id,
        status="draft",
        definition_sha256="b" * 64,
        created_by_user_id=other_user.id,
    )
    db_session.add(foreign_rule)
    await db_session.commit()

    listed = await client.get("/api/workflow-config/automations")
    assert listed.json()["items"] == []
    events = await client.get(
        f"/api/workflow-config/automations/{foreign_rule.id}/events"
    )
    assert events.status_code == 404
    archived = await client.post(
        f"/api/workflow-config/automations/{foreign_rule.id}/archive"
    )
    assert archived.status_code == 404
    edited = await client.patch(
        f"/api/workflow-config/automations/{foreign_rule.id}",
        json={
            "name": "Foreign rule",
            "trigger_event": "matter_created",
            "template_id": str(other_template.id),
        },
    )
    assert edited.status_code == 404
