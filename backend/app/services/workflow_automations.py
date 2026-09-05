"""Bounded trigger/action dispatch for approved matter workflow templates.

A firm activates a rule; a matching matter event then plans exactly the run the
manual preview endpoint would have planned. Nothing here applies a run, creates
a task, changes a stage, or sends anything outward: the reviewed
``approve_legal_work`` apply path remains the only way automation reaches a
matter.

Dispatch is called after the caller has committed the change that produced the
event, and it never raises into that caller. A rule that cannot plan records an
immutable ``blocked`` outcome instead, so a firm can see why its automation did
nothing rather than discovering silence.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.configurable_workflow import (
    MatterWorkflowRun,
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.models.plugin import Matter
from app.models.workflow_automation import (
    MatterWorkflowAutomationEvent,
    MatterWorkflowAutomationRule,
)
from app.services.configurable_workflows import (
    acquire_workflow_config_lock,
    append_run_event,
    build_preview,
    digest_payload,
)

logger = logging.getLogger(__name__)

TRIGGER_EVENTS = ("matter_created", "matter_stage_changed")


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip().lower()
    return clean or None


def rule_definition_payload(rule: MatterWorkflowAutomationRule) -> dict[str, Any]:
    """The reviewable definition. The name is a label, not part of the rule."""
    return {
        "trigger_event": rule.trigger_event,
        "trigger_stage": rule.trigger_stage,
        "match_matter_type": rule.match_matter_type,
        "match_practice_area": rule.match_practice_area,
        "template_id": str(rule.template_id),
    }


def rule_definition_sha256(rule: MatterWorkflowAutomationRule) -> str:
    return digest_payload(rule_definition_payload(rule))


def rule_matches(
    rule: MatterWorkflowAutomationRule,
    matter: Matter,
    *,
    trigger_event: str,
) -> bool:
    """Every declared condition must match; there is no expression language."""
    if rule.trigger_event != trigger_event:
        return False
    if trigger_event == "matter_stage_changed":
        if _normalized(matter.stage) != _normalized(rule.trigger_stage):
            return False
    if rule.match_matter_type is not None and _normalized(
        matter.matter_type
    ) != _normalized(rule.match_matter_type):
        return False
    if rule.match_practice_area is not None and _normalized(
        matter.practice_area
    ) != _normalized(rule.match_practice_area):
        return False
    return True


def dedupe_key(
    rule: MatterWorkflowAutomationRule,
    matter: Matter,
    *,
    trigger_event: str,
) -> str:
    """One plan per rule, matter, and triggering condition — ever.

    Re-entering a stage a firm has already automated does not plan a second
    run. A person can always create another preview by hand.
    """
    return digest_payload(
        {
            "matter_id": str(matter.id),
            "trigger_event": trigger_event,
            "trigger_stage": _normalized(rule.trigger_stage),
        }
    )


def automation_event_response(event: MatterWorkflowAutomationEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "rule_id": str(event.rule_id),
        "matter_id": str(event.matter_id),
        "trigger_event": event.trigger_event,
        "outcome": event.outcome,
        "run_id": str(event.run_id) if event.run_id else None,
        "rule_sha256": event.rule_sha256,
        "detail": event.detail_json,
        "evidence_sha256": event.evidence_sha256,
        "created_at": event.created_at,
    }


def rule_response(
    rule: MatterWorkflowAutomationRule,
    *,
    template_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "trigger_stage": rule.trigger_stage,
        "match_matter_type": rule.match_matter_type,
        "match_practice_area": rule.match_practice_area,
        "template_id": str(rule.template_id),
        "template_name": template_name,
        "status": rule.status,
        "definition_sha256": rule.definition_sha256,
        "activated_by_user_id": (
            str(rule.activated_by_user_id) if rule.activated_by_user_id else None
        ),
        "activated_at": rule.activated_at,
        "archived_at": rule.archived_at,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


async def latest_approved_version_id(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> uuid.UUID | None:
    """Resolve the template's newest approved version at dispatch time."""
    template = await db.scalar(
        select(MatterWorkflowTemplate).where(
            MatterWorkflowTemplate.tenant_id == tenant_id,
            MatterWorkflowTemplate.id == template_id,
        )
    )
    if template is None or not template.active:
        return None
    return await db.scalar(
        select(MatterWorkflowTemplateVersion.id)
        .where(
            MatterWorkflowTemplateVersion.tenant_id == tenant_id,
            MatterWorkflowTemplateVersion.template_id == template_id,
            MatterWorkflowTemplateVersion.status == "approved",
        )
        .order_by(MatterWorkflowTemplateVersion.version.desc())
        .limit(1)
    )


async def active_rules_for(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    trigger_event: str,
) -> list[MatterWorkflowAutomationRule]:
    rows = (
        await db.execute(
            select(MatterWorkflowAutomationRule)
            .where(
                MatterWorkflowAutomationRule.tenant_id == tenant_id,
                MatterWorkflowAutomationRule.trigger_event == trigger_event,
                MatterWorkflowAutomationRule.status == "active",
            )
            .order_by(
                MatterWorkflowAutomationRule.created_at,
                MatterWorkflowAutomationRule.id,
            )
        )
    ).scalars()
    return list(rows)


async def _existing_dispatch(
    db: AsyncSession,
    rule: MatterWorkflowAutomationRule,
    key: str,
) -> MatterWorkflowAutomationEvent | None:
    return await db.scalar(
        select(MatterWorkflowAutomationEvent).where(
            MatterWorkflowAutomationEvent.tenant_id == rule.tenant_id,
            MatterWorkflowAutomationEvent.rule_id == rule.id,
            MatterWorkflowAutomationEvent.dedupe_key == key,
        )
    )


def _record_dispatch(
    db: AsyncSession,
    rule: MatterWorkflowAutomationRule,
    *,
    matter: Matter,
    trigger_event: str,
    key: str,
    outcome: str,
    run_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
    detail: dict[str, Any],
) -> MatterWorkflowAutomationEvent:
    payload = {
        "rule_id": str(rule.id),
        "matter_id": str(matter.id),
        "trigger_event": trigger_event,
        "dedupe_key": key,
        "outcome": outcome,
        "run_id": str(run_id) if run_id else None,
        "rule_sha256": rule.definition_sha256,
        "actor_user_id": str(actor_user_id),
        "detail": detail,
    }
    event = MatterWorkflowAutomationEvent(
        tenant_id=rule.tenant_id,
        rule_id=rule.id,
        matter_id=matter.id,
        trigger_event=trigger_event,
        dedupe_key=key,
        outcome=outcome,
        run_id=run_id,
        rule_sha256=rule.definition_sha256,
        actor_user_id=actor_user_id,
        detail_json=detail,
        evidence_sha256=digest_payload(payload),
    )
    db.add(event)
    return event


async def _plan_for_rule(
    db: AsyncSession,
    rule: MatterWorkflowAutomationRule,
    *,
    matter: Matter,
    trigger_event: str,
    actor_user_id: uuid.UUID,
    as_of: date,
) -> MatterWorkflowAutomationEvent | None:
    key = dedupe_key(rule, matter, trigger_event=trigger_event)
    existing = await _existing_dispatch(db, rule, key)
    if existing is not None:
        return None

    version_id = await latest_approved_version_id(db, rule.tenant_id, rule.template_id)
    if version_id is None:
        return _record_dispatch(
            db,
            rule,
            matter=matter,
            trigger_event=trigger_event,
            key=key,
            outcome="blocked",
            run_id=None,
            actor_user_id=actor_user_id,
            detail={
                "failure_code": "template_not_approved",
                "message": (
                    "The rule's template has no active, approved version to plan."
                ),
            },
        )

    try:
        preview, preview_sha256, template_sha256, matter_sha256 = await build_preview(
            db,
            matter=matter,
            version_id=version_id,
            as_of=as_of,
            lock_dependencies=True,
        )
    except HTTPException as exc:
        return _record_dispatch(
            db,
            rule,
            matter=matter,
            trigger_event=trigger_event,
            key=key,
            outcome="blocked",
            run_id=None,
            actor_user_id=actor_user_id,
            detail={
                "failure_code": "preview_rejected",
                "status_code": exc.status_code,
                "message": str(exc.detail),
            },
        )

    request_payload = {
        "automation_rule_id": str(rule.id),
        "matter_id": str(matter.id),
        "template_version_id": str(version_id),
        "trigger_event": trigger_event,
    }
    run = MatterWorkflowRun(
        tenant_id=rule.tenant_id,
        matter_id=matter.id,
        template_version_id=version_id,
        idempotency_key=f"automation:{rule.id}:{key}",
        request_sha256=digest_payload(request_payload),
        template_sha256=template_sha256,
        matter_sha256=matter_sha256,
        preview_sha256=preview_sha256,
        preview_json=preview,
        prior_stage=matter.stage,
        planned_by_user_id=actor_user_id,
    )
    db.add(run)
    await db.flush()
    await append_run_event(
        db,
        run,
        event_type="previewed",
        actor_user_id=actor_user_id,
        detail={
            "preview_sha256": preview_sha256,
            "can_apply": preview["can_apply"],
            "missing_required_field_count": len(preview["missing_required_fields"]),
            "missing_assignee_count": len(preview["missing_assignees"]),
            "automation_rule_id": str(rule.id),
            "automation_trigger_event": trigger_event,
        },
    )
    return _record_dispatch(
        db,
        rule,
        matter=matter,
        trigger_event=trigger_event,
        key=key,
        outcome="planned",
        run_id=run.id,
        actor_user_id=actor_user_id,
        detail={
            "template_version_id": str(version_id),
            "preview_sha256": preview_sha256,
            "can_apply": preview["can_apply"],
            "missing_required_field_count": len(preview["missing_required_fields"]),
            "missing_assignee_count": len(preview["missing_assignees"]),
        },
    )


async def dispatch_matter_event(
    db: AsyncSession,
    *,
    matter: Matter,
    trigger_event: str,
    actor_user_id: uuid.UUID,
    as_of: date | None = None,
) -> list[MatterWorkflowAutomationEvent]:
    """Plan a run for every active rule that matches this matter event.

    The caller owns the surrounding transaction. Each rule is attempted inside
    its own savepoint so one unplannable rule cannot cost the firm the rest.
    """
    if trigger_event not in TRIGGER_EVENTS:
        raise ValueError(f"unsupported automation trigger event: {trigger_event}")
    rules = await active_rules_for(db, matter.tenant_id, trigger_event=trigger_event)
    matched = [
        rule
        for rule in rules
        if rule_matches(rule, matter, trigger_event=trigger_event)
    ]
    if not matched:
        return []

    await acquire_workflow_config_lock(db, matter.tenant_id, shared=True)
    recorded: list[MatterWorkflowAutomationEvent] = []
    for rule in matched:
        try:
            async with db.begin_nested():
                event = await _plan_for_rule(
                    db,
                    rule,
                    matter=matter,
                    trigger_event=trigger_event,
                    actor_user_id=actor_user_id,
                    as_of=as_of or date.today(),
                )
        except IntegrityError:
            # A concurrent request for the same matter event won the dedupe or
            # run-idempotency constraint. Its evidence is the record.
            continue
        except SQLAlchemyError:
            logger.warning(
                "Workflow automation rule %s failed to plan for matter %s",
                rule.id,
                matter.id,
                exc_info=True,
            )
            continue
        if event is not None:
            recorded.append(event)
    return recorded


async def dispatch_matter_event_safely(
    db: AsyncSession,
    *,
    matter: Matter,
    trigger_event: str,
    actor_user_id: uuid.UUID,
) -> list[MatterWorkflowAutomationEvent]:
    """Dispatch in its own transaction, after the caller's change committed.

    Automation must never be the reason a matter fails to save, so every
    failure here is contained and logged rather than raised.
    """
    try:
        await set_tenant_context(db, str(matter.tenant_id))
        recorded = await dispatch_matter_event(
            db,
            matter=matter,
            trigger_event=trigger_event,
            actor_user_id=actor_user_id,
        )
        # Commit unconditionally: dispatch takes a transaction-scoped shared
        # configuration lock even when every matching rule was already
        # dispatched, and the caller must not inherit it.
        await db.commit()
        return recorded
    except Exception:  # pragma: no cover - defensive, exercised via monkeypatch
        logger.warning(
            "Workflow automation dispatch failed for matter %s on %s",
            getattr(matter, "id", None),
            trigger_event,
            exc_info=True,
        )
        try:
            await db.rollback()
        except SQLAlchemyError:
            logger.warning("Automation dispatch rollback failed", exc_info=True)
        return []
    finally:
        try:
            await set_tenant_context(db, str(matter.tenant_id))
        except SQLAlchemyError:  # pragma: no cover - session already unusable
            logger.warning("Automation tenant context restore failed", exc_info=True)


async def count_rules(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Bounded firm configuration: rules are a small, reviewable set."""
    return int(
        await db.scalar(
            select(func.count(MatterWorkflowAutomationRule.id)).where(
                MatterWorkflowAutomationRule.tenant_id == tenant_id,
                MatterWorkflowAutomationRule.status != "archived",
            )
        )
        or 0
    )
