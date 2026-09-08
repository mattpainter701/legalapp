from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import hashlib

import pytest

from app.services import work_artifact_reviews as review
from app.services.task_workflow import TaskWorkflowError
from app.models.work_artifact_review import WorkArtifactApproval


class DB:
    def __init__(self, values=(), rows=()):
        self.values = iter(values)
        self.rows = rows
        self.added = []
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return next(self.values)

    async def scalars(self, statement):
        self.statements.append(statement)
        return NS(all=lambda: self.rows)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = uuid4()
        self.added.append(value)

    def add_all(self, values):
        for value in values:
            self.add(value)

    async def flush(self):
        pass


def binding():
    tenant_id, artifact_id, revision_id, document_id = [uuid4() for _ in range(4)]
    task = NS(
        id=uuid4(),
        tenant_id=tenant_id,
        matter_id=uuid4(),
        review_policy="staff_then_attorney",
        staff_reviewer_user_id=uuid4(),
        attorney_reviewer_user_id=uuid4(),
        reviewer_user_id=uuid4(),
        staff_reviewed_at=None,
        staff_reviewed_by_user_id=None,
        version=2,
    )
    artifact = NS(
        id=artifact_id,
        tenant_id=tenant_id,
        current_revision_no=1,
        output_document_id=document_id,
        status="review",
    )
    revision = NS(id=revision_id, revision_no=1, content_sha256="a" * 64)
    document = NS(
        id=document_id,
        storage_state="verified",
        document_sha256=hashlib.sha256(b"approved bytes").hexdigest(),
        generated_artifact_revision_id=revision_id,
        filename="approved.docx",
    )
    task.pending_action = dict(
        type="matter_document_draft",
        artifact_id=str(artifact_id),
        artifact_revision_id=str(revision_id),
        document_id=str(document_id),
        artifact_sha256=revision.content_sha256,
        document_sha256=document.document_sha256,
    )
    return task, artifact, revision, document


@pytest.mark.asyncio
async def test_current_binding_scopes_every_query_and_rejects_missing_binding():
    task, artifact, revision, document = binding()
    db = DB((artifact, revision, document))
    assert await review.current_binding(db, task) == (artifact, revision, document)
    assert all("tenant_id" in str(statement) for statement in db.statements)
    del task.pending_action["document_id"]
    with pytest.raises(TaskWorkflowError, match="incomplete"):
        await review.current_binding(DB(), task)
    assert await review.current_binding(DB(), NS()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["revision", "document", "hash", "storage", "status", "missing"]
)
async def test_stale_bindings_fail_closed(changed):
    task, artifact, revision, document = binding()
    if changed == "revision":
        artifact.current_revision_no += 1
    if changed == "document":
        artifact.output_document_id = uuid4()
    if changed == "hash":
        task.pending_action["document_sha256"] = "b" * 64
    if changed == "storage":
        document.storage_state = "conflict"
    if changed == "status":
        artifact.status = "rejected"
    if changed == "missing":
        artifact = None
    with pytest.raises(TaskWorkflowError):
        await review.current_binding(DB((artifact, revision, document)), task)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy,count", [("staff_then_attorney", 2), ("attorney_only", 1)]
)
async def test_requirements_freeze_assigned_reviewers(policy, count):
    task, *bound = binding()
    task.review_policy = policy
    db = DB((None,))
    rows = await review.ensure_review_requirements(db, task, binding=tuple(bound))
    assert len(rows) == count
    assert rows[-1].reviewer_user_id == task.attorney_reviewer_user_id
    assert rows[-1].reviewer_role == "attorney"
    assert [row.sequence for row in rows] == list(range(1, count + 1))
    assert all(row.review_round == 1 and row.status == "pending" for row in rows)


@pytest.mark.asyncio
async def test_requirements_replay_and_rejection_round():
    task, *bound = binding()
    prior = NS(id=uuid4())
    assert await review.ensure_review_requirements(
        DB(rows=[prior]), task, binding=tuple(bound)
    ) == [prior]
    rows = await review.ensure_review_requirements(DB((2,)), task, binding=tuple(bound))
    assert rows[0].review_round == 3
    task.staff_reviewer_user_id = None
    with pytest.raises(TaskWorkflowError, match="configured"):
        await review.ensure_review_requirements(DB((None,)), task, binding=tuple(bound))
    bound[0].status = "approved"
    with pytest.raises(TaskWorkflowError, match="no longer"):
        await review.ensure_review_requirements(DB(), task, binding=tuple(bound))


@pytest.mark.asyncio
async def test_legacy_staff_stamp_is_labelled_and_never_reimported_in_new_round():
    task, *bound = binding()
    task.staff_reviewed_at = datetime.now(timezone.utc)
    task.staff_reviewed_by_user_id = task.staff_reviewer_user_id
    db = DB((None,))
    rows = await review.ensure_review_requirements(db, task, binding=tuple(bound))
    assert rows[0].status == "approved"
    stamp = next(row for row in db.added if isinstance(row, WorkArtifactApproval))
    assert stamp.stamp_metadata["source"] == "legacy_task"
    assert (
        stamp.stamp_metadata["original_decided_at"]
        == task.staff_reviewed_at.isoformat()
    )
    db = DB((1,))
    rows = await review.ensure_review_requirements(db, task, binding=tuple(bound))
    assert rows[0].status == "pending"
    assert not any(isinstance(row, WorkArtifactApproval) for row in db.added)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content", [b"approved bytes", b"edited bytes", OSError("unavailable")]
)
async def test_byte_verification_checks_exact_cloud_content(monkeypatch, content):
    task, _, _, document = binding()
    from app.services.cloud_artifact_materialization import cloud_artifact_materializer

    read = AsyncMock(
        side_effect=content if isinstance(content, Exception) else None,
        return_value=content,
    )
    monkeypatch.setattr(cloud_artifact_materializer, "read_current_cloud_bytes", read)
    if content == b"approved bytes":
        await review.verify_review_bytes(task, document)
    else:
        with pytest.raises(TaskWorkflowError, match="refresh"):
            await review.verify_review_bytes(task, document)


async def prepare_decision(monkeypatch):
    task, *bound = binding()
    rows = await review.ensure_review_requirements(
        DB((None,)), task, binding=tuple(bound)
    )
    monkeypatch.setattr(review, "current_binding", AsyncMock(return_value=tuple(bound)))
    monkeypatch.setattr(
        review, "ensure_review_requirements", AsyncMock(return_value=rows)
    )
    monkeypatch.setattr(review, "verify_review_bytes", AsyncMock())
    return task, bound, rows


@pytest.mark.asyncio
async def test_attorney_cannot_skip_staff_without_explicit_override(monkeypatch):
    task, _, rows = await prepare_decision(monkeypatch)
    db = DB()
    with pytest.raises(TaskWorkflowError, match="preceding"):
        await review.record_artifact_decision(
            db,
            task,
            actor=NS(id=task.attorney_reviewer_user_id),
            stage="attorney",
            decision="approve",
        )
    assert not db.added
    for reason in (None, "  "):
        with pytest.raises(TaskWorkflowError, match="reason"):
            await review.record_artifact_decision(
                db,
                task,
                actor=NS(id=task.attorney_reviewer_user_id),
                stage="attorney",
                decision="approve",
                override=True,
                reason=reason,
            )
    decision = await review.record_artifact_decision(
        db,
        task,
        actor=NS(id=task.attorney_reviewer_user_id),
        stage="attorney",
        decision="approve",
        override=True,
        reason="Urgent filing",
    )
    assert [row.decision for row in db.added] == ["override", "approve"]
    assert [row.status for row in rows] == ["skipped", "approved"]
    assert decision.reason == "Urgent filing"


@pytest.mark.asyncio
async def test_wrong_reviewer_and_duplicate_decision_are_rejected(monkeypatch):
    task, _, rows = await prepare_decision(monkeypatch)
    for actor in (uuid4(), task.staff_reviewer_user_id):
        if actor == task.staff_reviewer_user_id:
            rows[0].status = "approved"
        with pytest.raises(TaskWorkflowError, match="unavailable"):
            await review.record_artifact_decision(
                DB(), task, actor=NS(id=actor), stage="staff", decision="approve"
            )


@pytest.mark.asyncio
async def test_request_changes_preserves_decision_and_supersedes_round(monkeypatch):
    task, _, rows = await prepare_decision(monkeypatch)
    db = DB()
    await review.record_artifact_decision(
        db,
        task,
        actor=NS(id=task.staff_reviewer_user_id),
        stage="staff",
        decision="request_changes",
        reason="Correct the dates",
    )
    assert db.added[0].decision == "request_changes"
    assert all(row.status == "superseded" and row.superseded_at for row in rows)
    review.verify_review_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_approval_allows_only_explicit_legacy_or_exact_decision(
    monkeypatch,
):
    task, *bound = binding()
    monkeypatch.setattr(review, "current_binding", AsyncMock(return_value=tuple(bound)))
    assert await review.current_artifact_approval(DB((None,)), task) is None
    with pytest.raises(TaskWorkflowError, match="attorney approval"):
        await review.current_artifact_approval(DB((uuid4(), None)), task)
    stamp = NS(id=uuid4())
    assert await review.current_artifact_approval(DB((uuid4(), stamp)), task) is stamp


@pytest.mark.asyncio
async def test_attachment_resolution_refuses_changed_or_unapproved_revision():
    task, artifact, revision, document = binding()
    kwargs = dict(
        tenant_id=task.tenant_id, matter_id=task.matter_id, artifact_id=artifact.id
    )
    with pytest.raises(TaskWorkflowError):
        await review.resolve_approved_attachment(DB((artifact,)), **kwargs)
    artifact.status = "approved"
    approval = NS(id=uuid4())
    resolved, _ = await review.resolve_approved_attachment(
        DB((artifact, revision, document, approval)), **kwargs
    )
    assert resolved.document_sha256 == document.document_sha256
    with pytest.raises(TaskWorkflowError, match="new email"):
        await review.resolve_approved_attachment(
            DB((artifact, revision, document, NS(id=uuid4()))),
            **kwargs,
            expected=resolved,
        )
    with pytest.raises(TaskWorkflowError, match="attorney review"):
        await review.resolve_approved_attachment(
            DB((artifact, revision, document, None)), **kwargs
        )
    with pytest.raises(TaskWorkflowError, match="unavailable"):
        await review.resolve_approved_attachment(
            DB((artifact, revision, None)), **kwargs
        )
