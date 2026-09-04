"""Edge coverage: validation, blocked previews, and contained rule failures."""

import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.models.configurable_workflow import (
    MatterWorkflowRun,
    MatterWorkflowTemplateVersion,
)
from app.models.plugin import Matter
from app.models.workflow_automation import MatterWorkflowAutomationEvent
from app.routers import workflow_automations as router_module
from app.schemas.workflow_automation import (
    WorkflowAutomationActivateRequest,
    WorkflowAutomationRuleInput,
)
from app.services import workflow_automations
from tests.test_workflow_automation_dispatch import (
    active_rule,
    both_capabilities,
    restore,
)
from tests.test_workflow_automation_rules import approved_template


def test_rule_input_trims_optional_conditions_to_nothing():
    body = WorkflowAutomationRuleInput(
        name="  Opening rule  ",
        trigger_event="matter_created",
        match_matter_type="   ",
        match_practice_area=None,
        template_id=uuid.uuid4(),
    )

    assert body.name == "Opening rule"
    assert body.match_matter_type is None
    assert body.match_practice_area is None
    assert body.trigger_stage is None


def test_rule_input_rejects_a_blank_name():
    with pytest.raises(ValidationError, match="name is required"):
        WorkflowAutomationRuleInput(
            name="   ",
            trigger_event="matter_created",
            template_id=uuid.uuid4(),
        )


def test_activation_request_normalizes_and_rejects_a_non_digest():
    request = WorkflowAutomationActivateRequest(
        definition_sha256="A" * 64, confirm_activate=True
    )
    assert request.definition_sha256 == "a" * 64

    with pytest.raises(ValidationError, match="sha-256 hex digest"):
        WorkflowAutomationActivateRequest(
            definition_sha256="z" * 64, confirm_activate=True
        )


@pytest.mark.asyncio
async def test_a_rejected_preview_is_recorded_as_blocked_not_planned(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    await active_rule(client, db_session, role, template, name="Opening rule")

    # A definition hash that no longer matches its approval is exactly what
    # build_preview refuses. The rule must record why, not fail the matter.
    version = await db_session.scalar(
        select(MatterWorkflowTemplateVersion).where(
            MatterWorkflowTemplateVersion.tenant_id == test_tenant.id
        )
    )
    version.definition_sha256 = "d" * 64
    await db_session.commit()

    created = await client.post("/api/matters", json={"matter_name": "Stale template"})
    assert created.status_code == 201

    event = await db_session.scalar(select(MatterWorkflowAutomationEvent))
    assert event.outcome == "blocked"
    assert event.detail_json["failure_code"] == "preview_rejected"
    assert event.detail_json["status_code"] == 409
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0


@pytest.mark.asyncio
async def test_one_failing_rule_does_not_cost_the_firm_the_others(
    client, db_session, test_tenant, test_user, monkeypatch
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    first_template = await approved_template(client, role, db_session, name="First")
    await restore(role, db_session)
    second_template = await approved_template(client, role, db_session, name="Second")
    await restore(role, db_session)
    await active_rule(client, db_session, role, first_template, name="Failing rule")
    await active_rule(client, db_session, role, second_template, name="Working rule")

    original = workflow_automations._plan_for_rule
    seen: list[uuid.UUID] = []

    async def flaky(db, rule, **kwargs):
        seen.append(rule.id)
        if rule.name == "Failing rule":
            raise SQLAlchemyError("planning blew up")
        return await original(db, rule, **kwargs)

    monkeypatch.setattr(workflow_automations, "_plan_for_rule", flaky)
    created = await client.post("/api/matters", json={"matter_name": "Two rules"})
    assert created.status_code == 201
    assert len(seen) == 2

    events = (
        (await db_session.execute(select(MatterWorkflowAutomationEvent)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1


@pytest.mark.asyncio
async def test_the_rule_budget_is_enforced_before_anything_is_written(
    client, db_session, test_tenant, test_user, monkeypatch
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    first = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Only rule",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    assert first.status_code == 201

    monkeypatch.setattr(router_module, "MAX_RULES_PER_TENANT", 1)
    refused = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "One too many",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    assert refused.status_code == 409
    assert "at most 1 automation rules" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_renaming_a_rule_onto_a_taken_name_is_a_conflict(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Opening rule",
            "trigger_event": "matter_created",
            "template_id": template["id"],
        },
    )
    second = (
        await client.post(
            "/api/workflow-config/automations",
            json={
                "name": "Closeout rule",
                "trigger_event": "matter_stage_changed",
                "trigger_stage": "Closing",
                "template_id": template["id"],
            },
        )
    ).json()

    collision = await client.patch(
        f"/api/workflow-config/automations/{second['id']}",
        json={
            "name": "Opening rule",
            "trigger_event": "matter_stage_changed",
            "trigger_stage": "Closing",
            "template_id": template["id"],
        },
    )
    assert collision.status_code == 409
    assert "already uses that name" in collision.json()["detail"]


def test_an_unrecognized_integrity_error_is_still_a_conflict():
    conflict = router_module._conflict(RuntimeError("some other constraint"))

    assert isinstance(conflict, HTTPException)
    assert conflict.status_code == 409
    assert conflict.detail == "Automation rule conflict"


@pytest.mark.asyncio
async def test_a_matters_automation_history_is_matter_scoped(
    client, db_session, test_tenant, test_user
):
    await both_capabilities(db_session, test_tenant, test_user)
    unknown = await client.get(
        f"/api/matters/{uuid.uuid4()}/workflow-automation-events"
    )
    assert unknown.status_code == 404

    matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"quiet-{uuid.uuid4().hex}",
        matter_name="No automation yet",
    )
    db_session.add(matter)
    await db_session.commit()

    empty = await client.get(f"/api/matters/{matter.id}/workflow-automation-events")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
