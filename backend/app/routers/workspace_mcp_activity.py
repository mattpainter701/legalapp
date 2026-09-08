"""Firm activity from existing consent, call, run and review evidence."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.database import get_db, set_tenant_context
from app.models.user import User
from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.models.workspace_mcp_audit import WorkspaceMCPAuditEvent
from app.models.generated_artifact import GeneratedArtifact
from app.models.work_artifact_review import WorkArtifactApproval
from app.models.workflow_run import WorkflowRun, WorkflowRunStep
from app.models.task import Task
from app.services.access_control import require_capabilities

router = APIRouter(prefix="/api/workspace-mcp/activity", tags=["workspace-mcp"])
view = require_capabilities("admin_settings", "manage_matters", "manage_documents")


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _time(value):
    return value.isoformat() if value else None


def evidence_metadata(metadata):
    """Do not forward arbitrary historical audit metadata or private content."""
    result = {}
    for key in ("task_id", "artifact_id", "artifact_revision_id", "run_id"):
        parsed = _uuid((metadata or {}).get(key))
        if parsed:
            result[key] = str(parsed)
    for key in ("effect", "failure_reason", "run_status"):
        value = (metadata or {}).get(key)
        if isinstance(value, str):
            result[key] = value[:80]
    for key in ("result_bytes", "result_count", "duration_ms"):
        value = (metadata or {}).get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    return result


@router.get("/grants")
async def active_grants(
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    user=Depends(view),
):
    await set_tenant_context(db, str(user.tenant_id))
    rows = (
        await db.execute(
            select(WorkspaceMCPGrant, User.full_name)
            .outerjoin(
                User,
                (User.id == WorkspaceMCPGrant.user_id)
                & (User.tenant_id == user.tenant_id),
            )
            .where(
                WorkspaceMCPGrant.tenant_id == user.tenant_id,
                WorkspaceMCPGrant.client_id.not_like("research.%"),
                WorkspaceMCPGrant.status == "active",
                WorkspaceMCPGrant.revoked_at.is_(None),
                WorkspaceMCPGrant.expires_at > datetime.now(timezone.utc),
            )
            .order_by(WorkspaceMCPGrant.created_at.desc(), WorkspaceMCPGrant.id)
            .offset(offset)
            .limit(51)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(grant.id),
                "user_id": str(grant.user_id),
                "user_name": name,
                "client_name": grant.client_name,
                "client_id": grant.client_id,
                "scopes": sorted(grant.scope_set),
                "expires_at": _time(grant.expires_at),
                "last_used_at": _time(grant.last_used_at),
            }
            for grant, name in rows[:50]
        ],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


@router.get("")
async def activity(
    before: int | None = Query(None, ge=1),
    grant_id: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=50),
    db=Depends(get_db),
    user=Depends(view),
):
    # History remains inspectable after revocation or disabling rollout.
    await set_tenant_context(db, str(user.tenant_id))
    statement = (
        select(WorkspaceMCPAuditEvent, User.full_name)
        .outerjoin(
            User,
            (User.id == WorkspaceMCPAuditEvent.user_id)
            & (User.tenant_id == user.tenant_id),
        )
        .where(
            WorkspaceMCPAuditEvent.tenant_id == user.tenant_id,
            WorkspaceMCPAuditEvent.client_id.not_like("research.%"),
        )
    )
    if before is not None:
        statement = statement.where(WorkspaceMCPAuditEvent.chain_position < before)
    if grant_id is not None:
        statement = statement.where(WorkspaceMCPAuditEvent.grant_id == grant_id)
    rows = (
        await db.execute(
            statement.order_by(WorkspaceMCPAuditEvent.chain_position.desc()).limit(
                limit + 1
            )
        )
    ).all()
    items = []
    for event, name in rows[:limit]:
        metadata = evidence_metadata(event.metadata_json)
        items.append(
            {
                "id": str(event.id),
                "user_id": str(event.user_id) if event.user_id else None,
                "user_name": name,
                "grant_id": str(event.grant_id) if event.grant_id else None,
                "client_id": event.client_id,
                "event_type": event.event_type,
                "tool_name": event.tool_name,
                "outcome": event.outcome,
                "created_at": _time(event.created_at),
                "chain_position": event.chain_position,
                "event_hash": event.event_hash,
                "previous_event_hash": event.prev_event_hash,
                "metadata": metadata,
            }
        )
    # Batch-enrich only IDs actually recorded by successful calls. Every join
    # reasserts tenant ownership; malformed/legacy metadata never casts in SQL.
    run_ids = {_uuid(i["metadata"].get("run_id")) for i in items} - {None}
    runs = (
        {
            str(r.id): r
            for r in (
                await db.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.tenant_id == user.tenant_id,
                        WorkflowRun.id.in_(run_ids),
                    )
                )
            ).all()
        }
        if run_ids
        else {}
    )
    steps = (
        (
            await db.scalars(
                select(WorkflowRunStep)
                .where(
                    WorkflowRunStep.tenant_id == user.tenant_id,
                    WorkflowRunStep.run_id.in_(run_ids),
                )
                .order_by(WorkflowRunStep.position)
            )
        ).all()
        if run_ids
        else []
    )
    artifact_ids = {_uuid(i["metadata"].get("artifact_id")) for i in items} | {
        s.artifact_id for s in steps
    }
    artifact_ids.discard(None)
    artifacts = (
        {
            str(a.id): a
            for a in (
                await db.scalars(
                    select(GeneratedArtifact).where(
                        GeneratedArtifact.tenant_id == user.tenant_id,
                        GeneratedArtifact.id.in_(artifact_ids),
                    )
                )
            ).all()
        }
        if artifact_ids
        else {}
    )
    # Historical decisions remain bound to their exact revision, separately
    # from the artifact's current status. Report any page-level truncation.
    approvals = (
        (
            await db.scalars(
                select(WorkArtifactApproval)
                .join(
                    GeneratedArtifact,
                    (GeneratedArtifact.id == WorkArtifactApproval.artifact_id)
                    & (GeneratedArtifact.tenant_id == WorkArtifactApproval.tenant_id),
                )
                .where(
                    WorkArtifactApproval.tenant_id == user.tenant_id,
                    WorkArtifactApproval.artifact_id.in_(artifact_ids),
                )
                .order_by(WorkArtifactApproval.created_at.desc())
                .limit(1001)
            )
        ).all()
        if artifact_ids
        else []
    )
    task_ids = (
        {_uuid(i["metadata"].get("task_id")) for i in items}
        | {a.task_id for a in artifacts.values()}
        | {s.task_id for s in steps}
    )
    task_ids.discard(None)
    tasks = (
        {
            str(t.id): t
            for t in (
                await db.scalars(
                    select(Task).where(
                        Task.tenant_id == user.tenant_id,
                        Task.id.in_(task_ids),
                    )
                )
            ).all()
        }
        if task_ids
        else {}
    )
    for item in items:
        metadata = item["metadata"]
        run = runs.get(metadata.get("run_id"))
        own_steps = [s for s in steps if run and s.run_id == run.id]
        own_artifacts = {metadata.get("artifact_id")} | {
            str(s.artifact_id) for s in own_steps if s.artifact_id
        }
        own_tasks = {metadata.get("task_id")} | {
            str(s.task_id) for s in own_steps if s.task_id
        }
        own_tasks |= {
            str(a.task_id)
            for key, a in artifacts.items()
            if key in own_artifacts and a.task_id
        }
        item["run"] = (
            {"id": str(run.id), "status": run.status, "matter_id": str(run.matter_id)}
            if run
            else None
        )
        item["tasks"] = [
            {
                "id": key,
                "status": task.status,
                "matter_id": str(task.matter_id) if task.matter_id else None,
            }
            for key, task in tasks.items()
            if key in own_tasks
        ]
        item["artifacts"] = [
            {
                "id": key,
                "status": artifact.status,
                "revision_no": artifact.current_revision_no,
            }
            for key, artifact in artifacts.items()
            if key in own_artifacts
        ]
        item["reviews"] = [
            {
                "id": str(a.id),
                "artifact_id": str(a.artifact_id),
                "revision_id": str(a.revision_id),
                "reviewer_user_id": str(a.reviewer_user_id),
                "decision": a.decision,
                "content_sha256": a.content_sha256,
                "document_sha256": a.document_sha256,
                "created_at": _time(a.created_at),
            }
            for a in approvals[:1000]
            if str(a.artifact_id) in own_artifacts
        ]
    return {
        "items": items,
        "reviews_truncated": len(approvals) > 1000,
        "next_before": items[-1]["chain_position"] if len(rows) > limit else None,
    }
