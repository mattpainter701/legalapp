"""Retention, legal-hold, and shared-CAS deletion gates for Studio artifacts."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from itertools import islice
from typing import AsyncContextManager, Awaitable, Callable, Iterable

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.durable_job import DurableJob
from app.models.studio_render import StudioRenderArtifact
from app.services.studio_object_storage import (
    StudioObjectRef,
    StudioObjectStore,
    StudioStagedObject,
    run_storage_mutation_to_completion,
)


@dataclass(frozen=True)
class StudioCleanupCandidate:
    tenant_id: uuid.UUID
    artifact_id: uuid.UUID
    job_terminal: bool
    adoption_outcome: str
    retention_class: str
    expires_at: datetime | None
    legal_hold_at: datetime | None = None
    live_evidence_reference: bool | None = None


@dataclass(frozen=True)
class StudioCleanupDecision:
    artifact_id: uuid.UUID
    eligible: bool
    reason: str


LegalHoldCheck = Callable[[StudioCleanupCandidate], Awaitable[bool]]
CurrentEvidenceCheck = Callable[[uuid.UUID, uuid.UUID], Awaitable[bool]]


async def cleanup_decision(
    candidate: StudioCleanupCandidate,
    *,
    now: datetime,
    legal_hold_check: LegalHoldCheck,
    legal_hold_timeout_seconds: float = 5.0,
) -> StudioCleanupDecision:
    if not 0.01 <= legal_hold_timeout_seconds <= 30:
        raise ValueError("legal hold timeout must be between 0.01 and 30 seconds")
    if not candidate.job_terminal:
        return StudioCleanupDecision(candidate.artifact_id, False, "job_not_terminal")
    if candidate.live_evidence_reference is None:
        return StudioCleanupDecision(
            candidate.artifact_id, False, "evidence_check_unavailable"
        )
    if candidate.live_evidence_reference:
        return StudioCleanupDecision(candidate.artifact_id, False, "current_evidence")
    if candidate.retention_class == "evidence":
        return StudioCleanupDecision(candidate.artifact_id, False, "evidence_retention")
    if candidate.expires_at is None or candidate.expires_at > now:
        return StudioCleanupDecision(candidate.artifact_id, False, "not_expired")
    if candidate.legal_hold_at is not None:
        return StudioCleanupDecision(candidate.artifact_id, False, "legal_hold")
    try:
        held = await asyncio.wait_for(
            legal_hold_check(candidate), timeout=legal_hold_timeout_seconds
        )
    except Exception:
        return StudioCleanupDecision(candidate.artifact_id, False, "hold_check_failed")
    if held:
        return StudioCleanupDecision(candidate.artifact_id, False, "legal_hold")
    return StudioCleanupDecision(candidate.artifact_id, True, "expired")


async def bounded_cleanup_candidates(
    candidates: Iterable[StudioCleanupCandidate],
    *,
    now: datetime,
    legal_hold_check: LegalHoldCheck,
    limit: int,
    legal_hold_timeout_seconds: float = 5.0,
) -> list[StudioCleanupDecision]:
    if not 1 <= limit <= 500:
        raise ValueError("cleanup limit must be between 1 and 500")
    decisions: list[StudioCleanupDecision] = []
    for candidate in candidates:
        if len(decisions) >= limit:
            break
        decisions.append(
            await cleanup_decision(
                candidate,
                now=now,
                legal_hold_check=legal_hold_check,
                legal_hold_timeout_seconds=legal_hold_timeout_seconds,
            )
        )
    return decisions


@dataclass(frozen=True)
class StudioDurableJobCleanupCandidate:
    job_id: uuid.UUID
    terminal: bool
    completed_at: datetime | None
    retain_until: datetime
    has_artifact: bool
    has_staged_object: bool


@dataclass(frozen=True)
class StudioDurableJobCleanupDecision:
    job_id: uuid.UUID
    eligible: bool
    reason: str


def durable_job_cleanup_decision(
    candidate: StudioDurableJobCleanupCandidate,
    *,
    now: datetime,
) -> StudioDurableJobCleanupDecision:
    if not candidate.terminal or candidate.completed_at is None:
        return StudioDurableJobCleanupDecision(candidate.job_id, False, "job_not_terminal")
    if candidate.retain_until > now:
        return StudioDurableJobCleanupDecision(candidate.job_id, False, "job_not_expired")
    if candidate.has_artifact:
        return StudioDurableJobCleanupDecision(candidate.job_id, False, "artifact_retained")
    if candidate.has_staged_object:
        return StudioDurableJobCleanupDecision(candidate.job_id, False, "stage_retained")
    return StudioDurableJobCleanupDecision(candidate.job_id, True, "expired")


def bounded_durable_job_cleanup(
    candidates: Iterable[StudioDurableJobCleanupCandidate],
    *,
    now: datetime,
    limit: int,
) -> list[StudioDurableJobCleanupDecision]:
    if not 1 <= limit <= 500:
        raise ValueError("job cleanup limit must be between 1 and 500")
    return [
        durable_job_cleanup_decision(candidate, now=now)
        for candidate in islice(candidates, limit)
    ]


@dataclass(frozen=True)
class StudioStagedReconciliationDecision:
    stage_id: uuid.UUID
    action: str
    reason: str


StageActiveCheck = Callable[[StudioStagedObject], Awaitable[bool]]
ObjectReferenceCheck = Callable[[StudioObjectRef], Awaitable[bool]]
ObjectReconciliationLock = Callable[
    [StudioObjectRef], AsyncContextManager[None]
]


async def reconcile_staged_batch(
    object_store: StudioObjectStore,
    *,
    tenant_id: uuid.UUID,
    now: datetime,
    limit: int,
    stage_active_check: StageActiveCheck,
    object_reference_check: ObjectReferenceCheck,
    object_lock: ObjectReconciliationLock,
    check_timeout_seconds: float = 5.0,
) -> list[StudioStagedReconciliationDecision]:
    """Reconcile durable pre-adoption receipts under the shared object lock."""

    if not 1 <= limit <= 500:
        raise ValueError("stage reconciliation limit must be between 1 and 500")
    if not 0.01 <= check_timeout_seconds <= 30:
        raise ValueError("stage reconciliation timeout must be between 0.01 and 30")
    stages = await asyncio.to_thread(
        object_store.list_staged,
        tenant_id,
        reconcile_before=now,
        limit=limit,
    )
    decisions: list[StudioStagedReconciliationDecision] = []
    for stage in stages:
        async with object_lock(stage.object_ref):
            try:
                active = await asyncio.wait_for(
                    stage_active_check(stage), timeout=check_timeout_seconds
                )
                referenced = await asyncio.wait_for(
                    object_reference_check(stage.object_ref),
                    timeout=check_timeout_seconds,
                )
            except Exception:
                decisions.append(
                    StudioStagedReconciliationDecision(
                        stage.stage_id, "kept", "check_failed"
                    )
                )
                continue
            if active:
                decisions.append(
                    StudioStagedReconciliationDecision(
                        stage.stage_id, "kept", "lease_active"
                    )
                )
                continue
            if referenced:
                await asyncio.to_thread(object_store.acknowledge_stage, stage)
                decisions.append(
                    StudioStagedReconciliationDecision(
                        stage.stage_id, "acknowledged", "artifact_referenced"
                    )
                )
                continue
            if await asyncio.to_thread(object_store.has_other_stages, stage):
                await asyncio.to_thread(object_store.acknowledge_stage, stage)
                decisions.append(
                    StudioStagedReconciliationDecision(
                        stage.stage_id, "acknowledged", "sibling_stage_retained"
                    )
                )
                continue
            try:
                await run_storage_mutation_to_completion(
                    partial(object_store.delete_staged, stage)
                )
            except Exception:
                decisions.append(
                    StudioStagedReconciliationDecision(
                        stage.stage_id, "kept", "delete_failed"
                    )
                )
                continue
            decisions.append(
                StudioStagedReconciliationDecision(
                    stage.stage_id, "deleted", "unreferenced"
                )
            )
    return decisions


class StudioStagedReceiptReconciler:
    """Production facade for crash-safe stage acknowledgement or reclamation."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        object_store: StudioObjectStore,
        legal_hold_check: LegalHoldCheck,
        current_evidence_check: CurrentEvidenceCheck,
        check_timeout_seconds: float = 5.0,
    ):
        if not 0.01 <= check_timeout_seconds <= 30:
            raise ValueError("stage reconciliation timeout must be between 0.01 and 30")
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.object_store = object_store
        self.legal_hold_check = legal_hold_check
        self.current_evidence_check = current_evidence_check
        self.check_timeout_seconds = check_timeout_seconds

    async def _bind_tenant_context(self) -> None:
        await set_tenant_context(self.db, str(self.tenant_id))

    async def _clock_now(self) -> datetime:
        now = await self.db.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise RuntimeError("Studio database time is unavailable")
        return now

    @asynccontextmanager
    async def _object_lock(self, ref: StudioObjectRef):
        if ref.tenant_id != self.tenant_id:
            raise ValueError("cross-tenant Studio stage")
        await self._bind_tenant_context()
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-object:{ref.object_key}"},
        )
        try:
            yield
        finally:
            if self.db.in_transaction():
                await self.db.rollback()

    async def _stage_active(self, stage: StudioStagedObject) -> bool:
        if stage.object_ref.tenant_id != self.tenant_id:
            return True
        row = await self.db.scalar(
            select(DurableJob).where(
                DurableJob.id == stage.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
        )
        if row is None or row.status not in {"running", "cancel_requested"}:
            return False
        payload = row.payload if isinstance(row.payload, dict) else {}
        try:
            token = uuid.UUID(str(payload.get("lease_token")))
            lease_expires_at = datetime.fromisoformat(
                str(payload.get("lease_expires_at"))
            )
        except (TypeError, ValueError):
            return False
        return bool(
            token == stage.lease_token
            and lease_expires_at.tzinfo is not None
            and lease_expires_at > await self._clock_now()
        )

    async def _object_referenced(self, ref: StudioObjectRef) -> bool:
        rows = list(
            (
                await self.db.scalars(
                    select(StudioRenderArtifact).where(
                        StudioRenderArtifact.tenant_id == self.tenant_id,
                        StudioRenderArtifact.object_key == ref.object_key,
                        StudioRenderArtifact.content_sha256 == ref.sha256,
                        StudioRenderArtifact.byte_size == ref.byte_size,
                        StudioRenderArtifact.media_type == ref.media_type,
                    ).with_for_update()
                )
            ).all()
        )
        for artifact in rows:
            job = await self.db.scalar(
                select(DurableJob).where(
                    DurableJob.id == artifact.job_id,
                    DurableJob.tenant_id == self.tenant_id,
                )
            )
            candidate = StudioCleanupCandidate(
                tenant_id=self.tenant_id,
                artifact_id=artifact.id,
                job_terminal=job is not None
                and job.status in {"completed", "failed", "cancelled"},
                adoption_outcome=artifact.adoption_outcome,
                retention_class=artifact.retention_class,
                expires_at=artifact.expires_at,
                legal_hold_at=artifact.legal_hold_at,
                live_evidence_reference=None,
            )
            try:
                held = artifact.legal_hold_at is not None or await asyncio.wait_for(
                    self.legal_hold_check(candidate),
                    timeout=self.check_timeout_seconds,
                )
                current = await asyncio.wait_for(
                    self.current_evidence_check(self.tenant_id, artifact.id),
                    timeout=self.check_timeout_seconds,
                )
            except Exception:
                return True
            if (
                artifact.storage_state in {"active", "delete_pending"}
                or held
                or current is not False
            ):
                return True
        return False

    async def reconcile_batch(
        self, *, limit: int
    ) -> list[StudioStagedReconciliationDecision]:
        """Run a bounded tenant batch; shared scheduler registration is gated."""

        if not 1 <= limit <= 500:
            raise ValueError("stage reconciliation limit must be between 1 and 500")
        await self._bind_tenant_context()
        now = await self._clock_now()
        await self.db.rollback()
        return await reconcile_staged_batch(
            self.object_store,
            tenant_id=self.tenant_id,
            now=now,
            limit=limit,
            stage_active_check=self._stage_active,
            object_reference_check=self._object_referenced,
            object_lock=self._object_lock,
            check_timeout_seconds=self.check_timeout_seconds,
        )


class StudioArtifactRetentionService:
    """Tenant-scoped, transactionally rechecked artifact cleanup facade."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        object_store: StudioObjectStore,
        legal_hold_check: LegalHoldCheck,
        current_evidence_check: CurrentEvidenceCheck,
        legal_hold_timeout_seconds: float = 5.0,
    ):
        if not 0.01 <= legal_hold_timeout_seconds <= 30:
            raise ValueError("legal hold timeout must be between 0.01 and 30 seconds")
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.object_store = object_store
        self.legal_hold_check = legal_hold_check
        self.current_evidence_check = current_evidence_check
        self.legal_hold_timeout_seconds = legal_hold_timeout_seconds

    async def _bind_tenant_context(self) -> None:
        await set_tenant_context(self.db, str(self.tenant_id))

    async def _object_lock(self, object_key: str) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-object:{object_key}"},
        )

    async def _clock_now(self) -> datetime:
        """Read wall-clock database time at each destructive phase boundary."""

        now = await self.db.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise RuntimeError("Studio database time is unavailable")
        return now

    async def _candidate(
        self, artifact: StudioRenderArtifact
    ) -> StudioCleanupCandidate:
        job = await self.db.scalar(
            select(DurableJob).where(
                DurableJob.id == artifact.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
        )
        try:
            live_reference = await asyncio.wait_for(
                self.current_evidence_check(self.tenant_id, artifact.id),
                timeout=self.legal_hold_timeout_seconds,
            )
        except Exception:
            live_reference = None
        return StudioCleanupCandidate(
            tenant_id=self.tenant_id,
            artifact_id=artifact.id,
            job_terminal=job is not None
            and job.status in {"completed", "failed", "cancelled"},
            adoption_outcome=artifact.adoption_outcome,
            retention_class=artifact.retention_class,
            expires_at=artifact.expires_at,
            legal_hold_at=artifact.legal_hold_at,
            live_evidence_reference=live_reference,
        )

    async def _active_reference_count(
        self, artifact: StudioRenderArtifact, object_key: str
    ) -> int:
        count = await self.db.scalar(
            select(func.count())
            .select_from(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.tenant_id == self.tenant_id,
                StudioRenderArtifact.object_key == object_key,
                StudioRenderArtifact.id != artifact.id,
                StudioRenderArtifact.storage_state.in_({"active", "delete_pending"}),
            )
        )
        return int(count or 0)

    async def _delete_to_completion(self, ref: StudioObjectRef) -> None:
        """Do not release the object lock while a synchronous delete is live."""

        await run_storage_mutation_to_completion(
            partial(self.object_store.delete, ref)
        )

    async def _finalize_pending_delete(
        self,
        artifact_id: uuid.UUID,
        *,
        object_key: str,
    ) -> StudioCleanupDecision:
        await self._bind_tenant_context()
        await self._object_lock(object_key)
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.id == artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if artifact is None or artifact.object_key != object_key:
            await self.db.rollback()
            return StudioCleanupDecision(artifact_id, False, "not_found")
        if artifact.storage_state == "deleted":
            await self.db.rollback()
            return StudioCleanupDecision(artifact_id, False, "already_deleted")
        if artifact.storage_state != "delete_pending":
            await self.db.rollback()
            return StudioCleanupDecision(artifact_id, False, "not_delete_pending")

        candidate = await self._candidate(artifact)
        decision = await cleanup_decision(
            candidate,
            now=await self._clock_now(),
            legal_hold_check=self.legal_hold_check,
            legal_hold_timeout_seconds=self.legal_hold_timeout_seconds,
        )
        if not decision.eligible:
            # A newly-added hold or evidence gate can restore the metadata
            # lifecycle only after storage is verified. A retried pending
            # delete may have unlinked the object before acknowledgement failed.
            ref = StudioObjectRef(
                tenant_id=artifact.tenant_id,
                object_key=artifact.object_key,
                sha256=artifact.content_sha256,
                byte_size=artifact.byte_size,
                media_type=artifact.media_type,
            )
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self.object_store.read,
                        ref,
                        max_bytes=ref.byte_size,
                    ),
                    timeout=30,
                )
            except Exception:
                await self.db.rollback()
                return StudioCleanupDecision(
                    artifact.id, False, "storage_delete_pending"
                )
            await self._clock_now()
            artifact.storage_state = "active"
            artifact.delete_requested_at = None
            await self.db.commit()
            return decision

        if await self._active_reference_count(artifact, object_key):
            artifact.storage_state = "deleted"
            artifact.deleted_at = await self._clock_now()
            await self.db.commit()
            return decision

        ref = StudioObjectRef(
            tenant_id=artifact.tenant_id,
            object_key=artifact.object_key,
            sha256=artifact.content_sha256,
            byte_size=artifact.byte_size,
            media_type=artifact.media_type,
        )
        try:
            await self._delete_to_completion(ref)
        except asyncio.CancelledError:
            await self.db.rollback()
            raise
        except Exception:
            # Phase A already committed delete_pending. A later reconciler can
            # safely retry without ever exposing a late background unlink.
            await self.db.rollback()
            return StudioCleanupDecision(
                artifact.id, False, "storage_delete_pending"
            )
        deleted_at = await self._clock_now()
        artifact.storage_state = "deleted"
        artifact.deleted_at = deleted_at
        await self.db.commit()
        return decision

    async def delete_if_eligible(
        self, artifact_id: uuid.UUID
    ) -> StudioCleanupDecision:
        await self._bind_tenant_context()
        # First discover the immutable object key without retaining a row lock;
        # adoption always acquires the object advisory lock before job/artifact
        # mutation, so retention must use the same order to avoid deadlocks.
        discovered = await self.db.scalar(
            select(StudioRenderArtifact).where(
                StudioRenderArtifact.id == artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
        )
        if discovered is None:
            await self.db.rollback()
            return StudioCleanupDecision(artifact_id, False, "not_found")
        object_key = discovered.object_key
        await self.db.rollback()
        await self._bind_tenant_context()
        await self._object_lock(object_key)
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.id == artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if artifact is None or artifact.object_key != object_key:
            await self.db.rollback()
            return StudioCleanupDecision(artifact_id, False, "not_found")
        if artifact.storage_state == "deleted":
            await self.db.rollback()
            return StudioCleanupDecision(artifact_id, False, "already_deleted")
        if artifact.storage_state == "delete_pending":
            # This row already completed durable Phase A. Release this
            # transaction and enter the Phase B reconciler, which reacquires
            # the same object lock and rechecks holds, storage, and references.
            await self.db.rollback()
            return await self._finalize_pending_delete(
                artifact.id,
                object_key=object_key,
            )
        candidate = await self._candidate(artifact)
        decision = await cleanup_decision(
            candidate,
            now=await self._clock_now(),
            legal_hold_check=self.legal_hold_check,
            legal_hold_timeout_seconds=self.legal_hold_timeout_seconds,
        )
        if not decision.eligible:
            await self.db.rollback()
            return decision

        other_active_refs = await self._active_reference_count(artifact, object_key)
        action_now = await self._clock_now()
        artifact.storage_state = "delete_pending"
        artifact.delete_requested_at = action_now
        if other_active_refs == 0:
            # Durable phase A: a crash before physical deletion leaves an
            # explicit retryable marker rather than an active row for a missing
            # object. Phase B reacquires and rechecks the object lock.
            await self.db.commit()
            return await self._finalize_pending_delete(
                artifact.id,
                object_key=object_key,
            )
        artifact.storage_state = "deleted"
        artifact.deleted_at = action_now
        await self.db.commit()
        return decision

    async def cleanup_batch(self, *, limit: int) -> list[StudioCleanupDecision]:
        """Process a bounded tenant batch; scheduler registration remains gated."""

        if not 1 <= limit <= 500:
            raise ValueError("cleanup limit must be between 1 and 500")
        await self._bind_tenant_context()
        now = await self._clock_now()
        artifact_ids = list(
            (
                await self.db.scalars(
                    select(StudioRenderArtifact.id)
                    .where(
                        StudioRenderArtifact.tenant_id == self.tenant_id,
                        StudioRenderArtifact.storage_state.in_(
                            {"active", "delete_pending"}
                        ),
                        or_(
                            StudioRenderArtifact.storage_state == "delete_pending",
                            StudioRenderArtifact.expires_at <= now,
                        ),
                    )
                    .order_by(StudioRenderArtifact.created_at, StudioRenderArtifact.id)
                    .limit(limit)
                )
            ).all()
        )
        await self.db.rollback()
        decisions: list[StudioCleanupDecision] = []
        for artifact_id in artifact_ids:
            decisions.append(await self.delete_if_eligible(artifact_id))
        return decisions
