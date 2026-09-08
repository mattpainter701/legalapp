"""Shared review evidence for chat, MCP and authenticated human review routes.

The caller locks the task and owns the transaction. Lock ordering is always task,
artifact, requirements. No provider write or final delivery occurs here.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.generated_artifact import GeneratedArtifact, GeneratedArtifactRevision
from app.models.matter_document import MatterDocument
from app.models.tenant import TenantSettings
from app.models.work_artifact_review import (
    WorkArtifactApproval,
    WorkArtifactDelivery,
    WorkArtifactReviewRequirement,
)
from app.services.task_workflow import TaskWorkflowError


async def artifact_review_policy(db, tenant_id):
    return (
        await db.scalar(
            select(TenantSettings.artifact_review_policy).where(
                TenantSettings.tenant_id == tenant_id
            )
        )
        or "staff_then_attorney"
    )


async def current_binding(db, task):
    payload = getattr(task, "pending_action", None) or {}
    if payload.get("type") != "matter_document_draft" or not payload.get("artifact_id"):
        return None
    try:
        artifact_id = uuid.UUID(str(payload["artifact_id"]))
        revision_id = uuid.UUID(str(payload["artifact_revision_id"]))
        document_id = uuid.UUID(str(payload["document_id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise TaskWorkflowError("Artifact review binding is incomplete", 409) from exc
    artifact = await db.scalar(
        select(GeneratedArtifact)
        .where(
            GeneratedArtifact.tenant_id == task.tenant_id,
            GeneratedArtifact.id == artifact_id,
            GeneratedArtifact.task_id == task.id,
            GeneratedArtifact.matter_id == task.matter_id,
        )
        .with_for_update()
    )
    revision = await db.scalar(
        select(GeneratedArtifactRevision).where(
            GeneratedArtifactRevision.tenant_id == task.tenant_id,
            GeneratedArtifactRevision.artifact_id == artifact_id,
            GeneratedArtifactRevision.id == revision_id,
        )
    )
    document = await db.scalar(
        select(MatterDocument).where(
            MatterDocument.tenant_id == task.tenant_id,
            MatterDocument.id == document_id,
            MatterDocument.generated_artifact_id == artifact_id,
            MatterDocument.generated_artifact_revision_id == revision_id,
        )
    )
    if (
        artifact is None
        or revision is None
        or document is None
        or artifact.current_revision_no != revision.revision_no
        or artifact.output_document_id != document.id
        or payload.get("artifact_sha256") != revision.content_sha256
        or payload.get("document_sha256") != document.document_sha256
        or document.storage_state != "verified"
        or artifact.status not in {"review", "approved"}
    ):
        raise TaskWorkflowError(
            "The artifact changed; review the current revision", 409
        )
    return artifact, revision, document


async def _requirements(db, artifact, revision):
    return list(
        (
            await db.scalars(
                select(WorkArtifactReviewRequirement)
                .where(
                    WorkArtifactReviewRequirement.tenant_id == artifact.tenant_id,
                    WorkArtifactReviewRequirement.artifact_id == artifact.id,
                    WorkArtifactReviewRequirement.revision_id == revision.id,
                    WorkArtifactReviewRequirement.superseded_at.is_(None),
                )
                .order_by(WorkArtifactReviewRequirement.sequence)
                .with_for_update()
            )
        ).all()
    )


async def _decision(
    db, requirement, revision, document, *, actor_id, decision, reason=None, stamp=None
):
    row = WorkArtifactApproval(
        tenant_id=requirement.tenant_id,
        artifact_id=requirement.artifact_id,
        revision_id=revision.id,
        requirement_id=requirement.id,
        reviewer_user_id=actor_id,
        document_id=document.id,
        decision=decision,
        reason=reason,
        content_sha256=revision.content_sha256,
        document_sha256=document.document_sha256,
        purpose="approve_document",
        stamp_metadata=stamp or {},
    )
    db.add(row)
    await db.flush()
    requirement.status = {
        "approve": "approved",
        "request_changes": "changes_requested",
        "override": "skipped",
    }[decision]
    await db.flush()
    return row


async def ensure_review_requirements(db, task, *, binding=None):
    binding = binding or await current_binding(db, task)
    if binding is None:
        return []
    artifact, revision, document = binding
    rows = await _requirements(db, artifact, revision)
    if rows:
        return rows
    if artifact.status != "review":
        raise TaskWorkflowError("This artifact is no longer awaiting review", 409)
    round_no = (
        await db.scalar(
            select(func.max(WorkArtifactReviewRequirement.review_round)).where(
                WorkArtifactReviewRequirement.tenant_id == task.tenant_id,
                WorkArtifactReviewRequirement.artifact_id == artifact.id,
                WorkArtifactReviewRequirement.revision_id == revision.id,
            )
        )
        or 0
    ) + 1
    reviewers = []
    if task.review_policy == "staff_then_attorney":
        reviewers.append(("staff", task.staff_reviewer_user_id))
    reviewers.append(
        ("attorney", task.attorney_reviewer_user_id or task.reviewer_user_id)
    )
    if any(user_id is None for _, user_id in reviewers):
        raise TaskWorkflowError("Artifact reviewers must be configured", 409)
    rows = [
        WorkArtifactReviewRequirement(
            tenant_id=task.tenant_id,
            artifact_id=artifact.id,
            revision_id=revision.id,
            review_round=round_no,
            sequence=index,
            reviewer_role=role,
            reviewer_user_id=user_id,
            required=True,
            status="pending",
        )
        for index, (role, user_id) in enumerate(reviewers, 1)
    ]
    db.add_all(rows)
    await db.flush()
    # Preserve a pre-migration staff decision without pretending it was made now.
    if (
        round_no == 1
        and task.review_policy == "staff_then_attorney"
        and task.staff_reviewed_at
        and task.staff_reviewed_by_user_id == task.staff_reviewer_user_id
    ):
        await _decision(
            db,
            rows[0],
            revision,
            document,
            actor_id=task.staff_reviewed_by_user_id,
            decision="approve",
            stamp={
                "source": "legacy_task",
                "original_decided_at": task.staff_reviewed_at.isoformat(),
                "task_id": str(task.id),
            },
        )
    return rows


async def verify_review_bytes(task, document):
    from app.services.cloud_artifact_materialization import cloud_artifact_materializer

    try:
        content = await cloud_artifact_materializer.read_current_cloud_bytes(
            tenant_id=task.tenant_id, document=document
        )
        if hashlib.sha256(content).hexdigest() != document.document_sha256:
            raise ValueError("changed cloud bytes")
    except Exception as exc:
        raise TaskWorkflowError(
            "The cloud document changed or is unavailable; refresh before approval", 409
        ) from exc


async def record_artifact_decision(
    db, task, *, actor, stage, decision, reason=None, override=False
):
    binding = await current_binding(db, task)
    if binding is None:
        return None
    artifact, revision, document = binding
    if artifact.status != "review":
        raise TaskWorkflowError("The artifact is no longer awaiting review", 409)
    requirements = await ensure_review_requirements(db, task, binding=binding)
    requirement = next((r for r in requirements if r.reviewer_role == stage), None)
    if (
        requirement is None
        or requirement.status != "pending"
        or requirement.reviewer_user_id != actor.id
    ):
        raise TaskWorkflowError(
            "This review requirement is unavailable or already decided", 409
        )
    if decision == "approve":
        await verify_review_bytes(task, document)
    if override:
        clean_reason = (reason or "").strip()
        if not clean_reason or stage != "attorney":
            raise TaskWorkflowError("Attorney override requires a reason", 422)
        staff = next((r for r in requirements if r.reviewer_role == "staff"), None)
        if staff is not None and staff.status == "pending":
            await _decision(
                db,
                staff,
                revision,
                document,
                actor_id=actor.id,
                decision="override",
                reason=clean_reason,
                stamp={"skipped_reviewer_user_id": str(staff.reviewer_user_id)},
            )
    if decision == "approve" and any(
        r.required
        and r.sequence < requirement.sequence
        and r.status not in {"approved", "skipped"}
        for r in requirements
    ):
        raise TaskWorkflowError("Complete the preceding review first", 409)
    approval = await _decision(
        db,
        requirement,
        revision,
        document,
        actor_id=actor.id,
        decision=decision,
        reason=(reason or "").strip() or None,
        stamp={
            "task_id": str(task.id),
            "task_version": task.version,
            "policy": task.review_policy,
        },
    )
    if decision == "request_changes":
        now = datetime.now(timezone.utc)
        for row in requirements:
            row.status, row.superseded_at = "superseded", now
        await db.flush()
    return approval


async def current_artifact_approval(db, task):
    binding = await current_binding(db, task)
    if binding is None:
        return None
    artifact, revision, document = binding
    # Existing queued work without spine rows uses the unchanged signed task
    # snapshot path. All newly proposed/reviewed artifacts have requirements.
    has_spine = await db.scalar(
        select(WorkArtifactReviewRequirement.id)
        .where(
            WorkArtifactReviewRequirement.tenant_id == task.tenant_id,
            WorkArtifactReviewRequirement.artifact_id == artifact.id,
        )
        .limit(1)
    )
    if has_spine is None:
        return None
    approval = await db.scalar(
        select(WorkArtifactApproval)
        .join(
            WorkArtifactReviewRequirement,
            WorkArtifactReviewRequirement.id == WorkArtifactApproval.requirement_id,
        )
        .where(
            WorkArtifactApproval.tenant_id == task.tenant_id,
            WorkArtifactApproval.artifact_id == artifact.id,
            WorkArtifactApproval.revision_id == revision.id,
            WorkArtifactApproval.decision == "approve",
            WorkArtifactApproval.document_sha256 == document.document_sha256,
            WorkArtifactApproval.content_sha256 == revision.content_sha256,
            WorkArtifactReviewRequirement.reviewer_role == "attorney",
            WorkArtifactReviewRequirement.status == "approved",
            WorkArtifactReviewRequirement.superseded_at.is_(None),
        )
    )
    if approval is None:
        raise TaskWorkflowError(
            "This exact artifact revision needs attorney approval", 409
        )
    return approval


async def resolve_approved_attachment(
    db, *, tenant_id, matter_id, artifact_id, expected=None
):
    """Lock the artifact through send and bind only the current approved output."""
    from app.schemas.chat_action import ApprovedArtifactAttachment

    artifact = await db.scalar(
        select(GeneratedArtifact)
        .where(
            GeneratedArtifact.tenant_id == tenant_id,
            GeneratedArtifact.id == artifact_id,
            GeneratedArtifact.matter_id == matter_id,
        )
        .with_for_update()
    )
    if artifact is None or artifact.status != "approved":
        raise TaskWorkflowError(
            "The attachment requires a current approved artifact", 409
        )
    revision = await db.scalar(
        select(GeneratedArtifactRevision).where(
            GeneratedArtifactRevision.tenant_id == tenant_id,
            GeneratedArtifactRevision.artifact_id == artifact.id,
            GeneratedArtifactRevision.revision_no == artifact.current_revision_no,
        )
    )
    document = await db.scalar(
        select(MatterDocument).where(
            MatterDocument.tenant_id == tenant_id,
            MatterDocument.id == artifact.output_document_id,
            MatterDocument.matter_id == matter_id,
            MatterDocument.storage_state == "verified",
        )
    )
    if (
        revision is None
        or document is None
        or document.generated_artifact_revision_id != revision.id
    ):
        raise TaskWorkflowError("The approved document binding is unavailable", 409)
    approval = await db.scalar(
        select(WorkArtifactApproval)
        .join(
            WorkArtifactReviewRequirement,
            WorkArtifactReviewRequirement.id == WorkArtifactApproval.requirement_id,
        )
        .where(
            WorkArtifactApproval.tenant_id == tenant_id,
            WorkArtifactApproval.artifact_id == artifact.id,
            WorkArtifactApproval.revision_id == revision.id,
            WorkArtifactApproval.document_id == document.id,
            WorkArtifactApproval.document_sha256 == document.document_sha256,
            WorkArtifactApproval.content_sha256 == revision.content_sha256,
            WorkArtifactApproval.decision == "approve",
            WorkArtifactReviewRequirement.reviewer_role == "attorney",
            WorkArtifactReviewRequirement.status == "approved",
            WorkArtifactReviewRequirement.superseded_at.is_(None),
        )
    )
    if approval is None:
        raise TaskWorkflowError("The exact attachment needs attorney review", 409)
    # A safe basename, without allowing provider paths or header characters.
    filename = document.filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = (
        "".join(c for c in filename if c.isprintable())[:255]
        or "approved-document.docx"
    )
    binding = ApprovedArtifactAttachment(
        artifact_id=artifact.id,
        revision_id=revision.id,
        approval_id=approval.id,
        document_id=document.id,
        document_sha256=document.document_sha256,
        filename=filename,
    )
    if expected is not None and binding != expected:
        raise TaskWorkflowError(
            "The approved attachment changed; create and review a new email", 409
        )
    return binding, document


async def append_delivery_receipt(db, run, *, status):
    """Receipt identity comes from the immutable send snapshot, even after edits."""
    from app.schemas.chat_action import EmailClientAction

    payload = run.action_snapshot or {}
    if payload.get("type") != "email_client" or not payload.get("artifact_attachment"):
        return None
    action = EmailClientAction.model_validate(payload)
    binding = action.artifact_attachment
    existing = await db.scalar(
        select(WorkArtifactDelivery).where(
            WorkArtifactDelivery.tenant_id == run.tenant_id,
            WorkArtifactDelivery.attempt_id == run.id,
            WorkArtifactDelivery.status == status,
        )
    )
    if existing:
        return existing
    row = WorkArtifactDelivery(
        tenant_id=run.tenant_id,
        artifact_id=binding.artifact_id,
        revision_id=binding.revision_id,
        approval_id=binding.approval_id,
        actor_user_id=run.triggered_by_user_id,
        attempt_id=run.id,
        channel="email",
        status=status,
        document_sha256=binding.document_sha256,
        recipient_bindings=[
            r.model_dump(mode="json") for r in action.recipient_bindings
        ],
        provider_message_id=run.provider_message_id,
        detail={
            "task_id": str(run.task_id),
            "action_sha256": run.action_sha256,
            "delivery_certainty": run.delivery_certainty,
        },
    )
    db.add(row)
    await db.flush()
    return row
