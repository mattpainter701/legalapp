"""Dispatch coverage: what an active rule does, and what it never does."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.configurable_workflow import MatterWorkflowRun, MatterWorkflowRunEvent
from app.models.plugin import Matter
from app.models.task import Task
from app.models.workflow_automation import (
    MatterWorkflowAutomationEvent,
    MatterWorkflowAutomationRule,
)
from app.services import workflow_automations
from tests.test_workflow_automation_rules import approved_template, grant


async def active_rule(client, db_session, role, template, **definition):
    body = {
        "name": definition.pop("name", f"Rule {uuid.uuid4().hex[:8]}"),
        "trigger_event": definition.pop("trigger_event", "matter_created"),
        "template_id": template["id"],
        **definition,
    }
    created = await client.post("/api/workflow-config/automations", json=body)
    assert created.status_code == 201, created.text
    rule = created.json()
    activated = await client.post(
        f"/api/workflow-config/automations/{rule['id']}/activate",
        json={
            "definition_sha256": rule["definition_sha256"],
            "confirm_activate": True,
        },
    )
    assert activated.status_code == 200, activated.text
    return activated.json()


async def both_capabilities(db_session, test_tenant, test_user):
    return await grant(
        db_session,
        test_tenant,
        test_user,
        ["manage_workflows", "approve_legal_work", "manage_matters"],
    )


async def restore(role, db_session):
    role.capabilities = ["manage_workflows", "approve_legal_work", "manage_matters"]
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_new_matter_is_planned_but_never_applied(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    rule = await active_rule(client, db_session, role, template, name="Open matters")

    created = await client.post(
        "/api/matters",
        json={"matter_name": "Vega v. Ash", "matter_type": "Litigation"},
    )
    assert created.status_code == 201, created.text
    matter_id = uuid.UUID(created.json()["id"])

    run = await db_session.scalar(
        select(MatterWorkflowRun).where(MatterWorkflowRun.matter_id == matter_id)
    )
    assert run is not None
    assert run.status == "planned"
    assert run.planned_by_user_id == test_user.id
    assert run.idempotency_key.startswith(f"automation:{rule['id']}:")
    assert run.preview_json["can_apply"] is True

    # Planning is not doing: no task exists and the matter stage is untouched.
    assert (
        await db_session.scalar(
            select(func.count(Task.id)).where(Task.matter_id == matter_id)
        )
        == 0
    )
    matter = await db_session.get(Matter, matter_id)
    await db_session.refresh(matter)
    assert matter.stage is None

    event = await db_session.scalar(
        select(MatterWorkflowAutomationEvent).where(
            MatterWorkflowAutomationEvent.matter_id == matter_id
        )
    )
    assert event.outcome == "planned"
    assert event.run_id == run.id
    assert event.rule_sha256 == rule["definition_sha256"]

    run_event = await db_session.scalar(
        select(MatterWorkflowRunEvent).where(MatterWorkflowRunEvent.run_id == run.id)
    )
    assert run_event.event_type == "previewed"
    assert run_event.detail_json["automation_rule_id"] == rule["id"]

    listed = await client.get(f"/api/matters/{matter_id}/workflow-automation-events")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["rule_name"] == "Open matters"
    assert item["outcome"] == "planned"
    assert item["run_id"] == str(run.id)

    rule_events = await client.get(
        f"/api/workflow-config/automations/{rule['id']}/events"
    )
    assert [entry["id"] for entry in rule_events.json()["items"]] == [str(event.id)]


@pytest.mark.asyncio
async def test_conditions_and_draft_status_keep_a_rule_quiet(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    await active_rule(
        client,
        db_session,
        role,
        template,
        name="Estate openings only",
        match_matter_type="Estate",
        match_practice_area="Probate",
    )
    draft_template = await approved_template(
        client, role, db_session, name="Second template"
    )
    await restore(role, db_session)
    unapproved = await client.post(
        "/api/workflow-config/automations",
        json={
            "name": "Never activated",
            "trigger_event": "matter_created",
            "template_id": draft_template["id"],
        },
    )
    assert unapproved.json()["status"] == "draft"

    wrong_type = await client.post(
        "/api/matters",
        json={
            "matter_name": "Litigation file",
            "matter_type": "Litigation",
            "practice_area": "Probate",
        },
    )
    assert wrong_type.status_code == 201
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0

    # Case differences are a formatting detail, not a different practice area.
    matching = await client.post(
        "/api/matters",
        json={
            "matter_name": "Estate of Roe",
            "matter_type": "estate",
            "practice_area": "PROBATE",
        },
    )
    assert matching.status_code == 201
    events = (
        (await db_session.execute(select(MatterWorkflowAutomationEvent)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].matter_id == uuid.UUID(matching.json()["id"])


@pytest.mark.asyncio
async def test_a_stage_change_plans_once_and_only_for_the_named_stage(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    await active_rule(
        client,
        db_session,
        role,
        template,
        name="Discovery opens the checklist",
        trigger_event="matter_stage_changed",
        trigger_stage="Discovery",
    )
    matter_id = (
        await client.post("/api/matters", json={"matter_name": "Stage matter"})
    ).json()["id"]

    unrelated = await client.patch(
        f"/api/matters/{matter_id}", json={"stage": "Pleadings"}
    )
    assert unrelated.status_code == 200
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0

    entered = await client.patch(
        f"/api/matters/{matter_id}", json={"stage": "Discovery"}
    )
    assert entered.status_code == 200
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1

    # Leaving and re-entering the same stage does not plan a second run.
    await client.patch(f"/api/matters/{matter_id}", json={"stage": "Pleadings"})
    await client.patch(f"/api/matters/{matter_id}", json={"stage": "Discovery"})
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1
    assert (
        await db_session.scalar(select(func.count(MatterWorkflowAutomationEvent.id)))
        == 1
    )

    # An edit that does not touch the stage is not a stage change.
    await client.patch(f"/api/matters/{matter_id}", json={"description": "Updated"})
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1


@pytest.mark.asyncio
async def test_an_unplannable_rule_records_why_instead_of_going_silent(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    rule = await active_rule(client, db_session, role, template, name="Opening rule")
    archived = await client.post(
        f"/api/workflow-config/templates/{template['id']}/archive"
    )
    assert archived.status_code == 200

    created = await client.post("/api/matters", json={"matter_name": "Orphaned rule"})
    assert created.status_code == 201

    event = await db_session.scalar(select(MatterWorkflowAutomationEvent))
    assert event.outcome == "blocked"
    assert event.run_id is None
    assert event.detail_json["failure_code"] == "template_not_approved"
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0

    listed = await client.get(f"/api/workflow-config/automations/{rule['id']}/events")
    assert listed.json()["items"][0]["outcome"] == "blocked"


@pytest.mark.asyncio
async def test_a_second_dispatch_of_the_same_event_adds_no_second_plan(
    client, db_session, test_tenant, test_user
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    await active_rule(client, db_session, role, template, name="Opening rule")
    matter_id = uuid.UUID(
        (
            await client.post("/api/matters", json={"matter_name": "Replayed matter"})
        ).json()["id"]
    )
    matter = await db_session.get(Matter, matter_id)

    replayed = await workflow_automations.dispatch_matter_event_safely(
        db_session,
        matter=matter,
        trigger_event="matter_created",
        actor_user_id=test_user.id,
    )
    assert replayed == []
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 1
    assert (
        await db_session.scalar(select(func.count(MatterWorkflowAutomationEvent.id)))
        == 1
    )


@pytest.mark.asyncio
async def test_dispatch_failures_never_reach_the_caller(
    client, db_session, test_tenant, test_user, monkeypatch
):
    role = await both_capabilities(db_session, test_tenant, test_user)
    template = await approved_template(client, role, db_session)
    await restore(role, db_session)
    await active_rule(client, db_session, role, template, name="Opening rule")

    async def explode(*args, **kwargs):
        raise RuntimeError("dispatch is broken")

    monkeypatch.setattr(workflow_automations, "dispatch_matter_event", explode)
    created = await client.post(
        "/api/matters", json={"matter_name": "Saved despite automation"}
    )
    assert created.status_code == 201
    assert await db_session.scalar(select(func.count(MatterWorkflowRun.id))) == 0
    assert (
        await db_session.scalar(select(func.count(MatterWorkflowAutomationEvent.id)))
        == 0
    )


@pytest.mark.asyncio
async def test_dispatch_rejects_an_unsupported_trigger_event(
    db_session, test_tenant, test_user
):
    matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"unsupported-{uuid.uuid4().hex}",
        matter_name="Unsupported trigger",
    )
    db_session.add(matter)
    await db_session.commit()

    with pytest.raises(ValueError, match="unsupported automation trigger event"):
        await workflow_automations.dispatch_matter_event(
            db_session,
            matter=matter,
            trigger_event="invoice_paid",
            actor_user_id=test_user.id,
        )


@pytest.mark.asyncio
async def test_rule_matching_ignores_case_and_requires_every_condition(
    db_session, test_tenant, test_user
):
    matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"match-{uuid.uuid4().hex}",
        matter_name="Matching matter",
        matter_type="Estate",
        practice_area="Probate",
        stage="Discovery",
    )
    rule = MatterWorkflowAutomationRule(
        tenant_id=test_tenant.id,
        name="Matcher",
        trigger_event="matter_stage_changed",
        trigger_stage=" discovery ",
        match_matter_type="estate",
        match_practice_area="probate",
        template_id=uuid.uuid4(),
        status="active",
        definition_sha256="c" * 64,
        created_by_user_id=test_user.id,
    )

    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_stage_changed"
    )
    assert not workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )

    rule.match_practice_area = "Family"
    assert not workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_stage_changed"
    )
    rule.match_practice_area = None
    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_stage_changed"
    )

    matter.stage = "Trial"
    assert not workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_stage_changed"
    )
