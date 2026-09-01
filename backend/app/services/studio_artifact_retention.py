"""Retention, legal-hold, and shared-CAS deletion gates for Studio artifacts."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from itertools import islice
from typing import AsyncContextManager, Awaitable, Callable, Iterable

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import set_tenant_context
from app.models.durable_job import DurableJob
from app.models.studio_draft import StudioDraft
from app.models.studio_render import (
    StudioPreferredRenderEvidence,
    StudioRenderArtifact,
)
from app.models.tenant import Tenant
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
    content_expires_at: datetime | None
    metadata_expires_at: datetime | None
    legal_hold_at: datetime | None = None
    live_evidence_reference: bool | None = None


@dataclass(frozen=True)
class StudioCleanupDecision:
    artifact_id: uuid.UUID
    eligible: bool
    reason: str


LegalHoldCheck = Callable[[StudioCleanupCandidate], Awaitable[bool]]
CurrentEvidenceCheck = Callable[[uuid.UUID, uuid.UUID], Awaitable[bool]]


def metadata_is_retained(candidate: StudioCleanupCandidate, *, now: datetime) -> bool:
    """Fail closed for legal hold, exact preferred evidence, or unknown evidence state."""

    if (
        candidate.legal_hold_at is not None
        or candidate.live_evidence_reference is not False
        or candidate.retention_class == "evidence"
    ):
        return True
    return (
        candidate.metadata_expires_at is None
        or candidate.metadata_expires_at > now
    )


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
    if candidate.content_expires_at is None or candidate.content_expires_at > now:
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
                try:
                    await asyncio.to_thread(
                        object_store.defer_stage,
                        stage,
                        reconcile_after=now + timedelta(minutes=5),
                    )
                except Exception:
                    pass
                decisions.append(
                    StudioStagedReconciliationDecision(
                        stage.stage_id, "kept", "check_failed"
                    )
                )
                continue
            if active:
                try:
                    await asyncio.to_thread(
                        object_store.defer_stage,
                        stage,
                        reconcile_after=now + timedelta(minutes=1),
                    )
                except Exception:
                    pass
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
                try:
                    await asyncio.to_thread(
                        object_store.defer_stage,
                        stage,
                        reconcile_after=now + timedelta(minutes=5),
                    )
                except Exception:
                    pass
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
                content_expires_at=artifact.content_expires_at,
                metadata_expires_at=artifact.metadata_expires_at,
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
            content_expires_at=artifact.content_expires_at,
            metadata_expires_at=artifact.metadata_expires_at,
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
                    artifact_id, False, "storage_delete_pending"
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
                artifact_id, False, "storage_delete_pending"
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
                artifact_id,
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
                artifact_id,
                object_key=object_key,
            )
        artifact.storage_state = "deleted"
        artifact.deleted_at = action_now
        await self.db.commit()
        return decision

    async def cleanup_batch(self, *, limit: int) -> list[StudioCleanupDecision]:
        """Process a bounded tenant batch of content-expiry candidates."""

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
                            and_(
                                StudioRenderArtifact.storage_state == "active",
                                StudioRenderArtifact.content_expires_at <= now,
                                StudioRenderArtifact.legal_hold_at.is_(None),
                                StudioRenderArtifact.retention_class != "evidence",
                                ~select(StudioPreferredRenderEvidence.artifact_id)
                                .where(
                                    StudioPreferredRenderEvidence.tenant_id
                                    == self.tenant_id,
                                    StudioPreferredRenderEvidence.artifact_id
                                    == StudioRenderArtifact.id,
                                )
                                .exists(),
                            ),
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


    async def cleanup_metadata_batch(
        self, *, limit: int
    ) -> list[StudioCleanupDecision]:
        """Delete only expired metadata after rechecking every retention gate."""

        if not 1 <= limit <= 500:
            raise ValueError("metadata cleanup limit must be between 1 and 500")
        await self._bind_tenant_context()
        now = await self._clock_now()
        artifact_ids = tuple(
            (
                await self.db.scalars(
                    select(StudioRenderArtifact.id)
                    .where(
                        StudioRenderArtifact.tenant_id == self.tenant_id,
                        StudioRenderArtifact.storage_state == "deleted",
                        StudioRenderArtifact.metadata_expires_at <= now,
                        StudioRenderArtifact.legal_hold_at.is_(None),
                        ~select(StudioPreferredRenderEvidence.artifact_id)
                        .where(
                            StudioPreferredRenderEvidence.tenant_id
                            == self.tenant_id,
                            StudioPreferredRenderEvidence.artifact_id
                            == StudioRenderArtifact.id,
                        )
                        .exists(),
                    )
                    .order_by(StudioRenderArtifact.created_at, StudioRenderArtifact.id)
                    .limit(limit)
                )
            ).all()
        )
        await self.db.rollback()
        decisions: list[StudioCleanupDecision] = []
        for artifact_id in artifact_ids:
            await self._bind_tenant_context()
            artifact = await self.db.scalar(
                select(StudioRenderArtifact)
                .where(
                    StudioRenderArtifact.id == artifact_id,
                    StudioRenderArtifact.tenant_id == self.tenant_id,
                )
                .with_for_update()
            )
            if artifact is None:
                await self.db.rollback()
                decisions.append(
                    StudioCleanupDecision(artifact_id, False, "not_found")
                )
                continue
            candidate = await self._candidate(artifact)
            action_now = await self._clock_now()
            if metadata_is_retained(candidate, now=action_now):
                await self.db.rollback()
                decisions.append(
                    StudioCleanupDecision(artifact_id, False, "metadata_retained")
                )
                continue
            try:
                held = await asyncio.wait_for(
                    self.legal_hold_check(candidate),
                    timeout=self.legal_hold_timeout_seconds,
                )
            except Exception:
                held = True
            if held:
                await self.db.rollback()
                decisions.append(
                    StudioCleanupDecision(artifact_id, False, "legal_hold")
                )
                continue
            await self.db.delete(artifact)
            await self.db.commit()
            decisions.append(
                StudioCleanupDecision(artifact_id, True, "metadata_expired")
            )
        return decisions

    async def cleanup_durable_jobs_batch(
        self,
        *,
        limit: int,
        retain_for: timedelta = timedelta(days=7),
    ) -> list[StudioDurableJobCleanupDecision]:
        """Bound tenant idempotency growth after artifact and stage reclamation."""

        if not 1 <= limit <= 500:
            raise ValueError("job cleanup limit must be between 1 and 500")
        if not timedelta(days=1) <= retain_for <= timedelta(days=365):
            raise ValueError("job retention must be between one day and one year")
        await self._bind_tenant_context()
        now = await self._clock_now()
        jobs = tuple(
            (
                await self.db.execute(
                    select(DurableJob.id, DurableJob.completed_at)
                    .where(
                        DurableJob.tenant_id == self.tenant_id,
                        DurableJob.kind.in_(
                            {
                                "studio_template_analysis",
                                "studio_template_ocr",
                                "studio_page_preview",
                                "studio_test_render",
                            }
                        ),
                        DurableJob.status.in_({"completed", "failed", "cancelled"}),
                        DurableJob.completed_at <= now - retain_for,
                        ~select(StudioRenderArtifact.id)
                        .where(
                            StudioRenderArtifact.tenant_id == self.tenant_id,
                            StudioRenderArtifact.job_id == DurableJob.id,
                        )
                        .exists(),
                    )
                    .order_by(DurableJob.completed_at, DurableJob.id)
                    .limit(limit)
                )
            ).all()
        )
        await self.db.rollback()
        try:
            stages = await asyncio.to_thread(
                self.object_store.list_staged,
                self.tenant_id,
                reconcile_before=datetime.max.replace(tzinfo=timezone.utc),
                limit=500,
            )
        except Exception:
            stages = None
        staged_jobs = {stage.job_id for stage in stages or ()}
        decisions: list[StudioDurableJobCleanupDecision] = []
        for job_id, completed_at in jobs:
            await self._bind_tenant_context()
            artifact_exists = await self.db.scalar(
                select(StudioRenderArtifact.id).where(
                    StudioRenderArtifact.tenant_id == self.tenant_id,
                    StudioRenderArtifact.job_id == job_id,
                )
            )
            decision = durable_job_cleanup_decision(
                StudioDurableJobCleanupCandidate(
                    job_id=job_id,
                    terminal=True,
                    completed_at=completed_at,
                    retain_until=(completed_at or now) + retain_for,
                    has_artifact=artifact_exists is not None,
                    has_staged_object=(
                        stages is None or job_id in staged_jobs
                    ),
                ),
                now=now,
            )
            if not decision.eligible:
                await self.db.rollback()
                decisions.append(decision)
                continue
            result = await self.db.execute(
                delete(DurableJob).where(
                    DurableJob.id == job_id,
                    DurableJob.tenant_id == self.tenant_id,
                    DurableJob.status.in_({"completed", "failed", "cancelled"}),
                    DurableJob.completed_at <= now - retain_for,
                )
            )
            if result.rowcount == 1:
                await self.db.commit()
                decisions.append(decision)
            else:
                await self.db.rollback()
                decisions.append(
                    StudioDurableJobCleanupDecision(
                        job_id, False, "job_changed"
                    )
                )
        return decisions


class StudioRenderMaintenance:
    """Bounded tenant/RLS maintenance owned by the dedicated render worker."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        object_store: StudioObjectStore,
        tenant_batch_size: int = 10,
        item_batch_size: int = 25,
        artifact_ttl_seconds: int = 86_400,
        metadata_ttl_seconds: int = 2_592_000,
    ) -> None:
        if not 1 <= tenant_batch_size <= 500:
            raise ValueError("maintenance tenant batch is invalid")
        if not 1 <= item_batch_size <= 100:
            raise ValueError("maintenance item batch is invalid")
        if not 300 <= artifact_ttl_seconds <= 604_800:
            raise ValueError("maintenance artifact TTL is invalid")
        if not 86_400 <= metadata_ttl_seconds <= 31_536_000:
            raise ValueError("maintenance metadata TTL is invalid")
        if metadata_ttl_seconds <= artifact_ttl_seconds:
            raise ValueError("maintenance metadata TTL must outlive artifact bytes")
        self.session_factory = session_factory
        self.object_store = object_store
        self.tenant_batch_size = tenant_batch_size
        self.item_batch_size = item_batch_size
        self.artifact_ttl_seconds = artifact_ttl_seconds
        self.metadata_ttl_seconds = metadata_ttl_seconds
        self._tenant_cursor: uuid.UUID | None = None

    async def _tenant_ids(self) -> tuple[uuid.UUID, ...]:
        async with self.session_factory() as db:
            # Offboarding cannot strand render bytes/jobs merely because the
            # tenant was deactivated before its retention windows elapsed.
            query = select(Tenant.id)
            if self._tenant_cursor is not None:
                query = query.where(Tenant.id > self._tenant_cursor)
            ids = tuple(
                (
                    await db.scalars(
                        query.order_by(Tenant.id).limit(self.tenant_batch_size)
                    )
                ).all()
            )
            if not ids and self._tenant_cursor is not None:
                ids = tuple(
                    (
                        await db.scalars(
                            select(Tenant.id)
                            .order_by(Tenant.id)
                            .limit(self.tenant_batch_size)
                        )
                    ).all()
                )
            return ids

    async def run_once(self) -> int:
        changed = 0
        tenant_ids = await self._tenant_ids()
        for tenant_id in tenant_ids:
            async with self.session_factory() as db:
                await set_tenant_context(db, str(tenant_id))
                preferred_rows = tuple(
                    (
                        await db.scalars(
                            select(StudioPreferredRenderEvidence)
                            .outerjoin(
                                StudioDraft,
                                (
                                    StudioDraft.tenant_id
                                    == StudioPreferredRenderEvidence.tenant_id
                                )
                                & (
                                    StudioDraft.id
                                    == StudioPreferredRenderEvidence.draft_id
                                ),
                            )
                            .where(
                                StudioPreferredRenderEvidence.tenant_id == tenant_id,
                                or_(
                                    StudioDraft.id.is_(None),
                                    StudioDraft.lifecycle_state != "active",
                                    StudioDraft.cancellation_requested_at.is_not(None),
                                    StudioDraft.revision
                                    != StudioPreferredRenderEvidence.revision,
                                    StudioDraft.identity_sha256
                                    != StudioPreferredRenderEvidence.identity_sha256,
                                    StudioDraft.evidence_revision.is_(None),
                                    StudioDraft.evidence_revision
                                    != StudioPreferredRenderEvidence.revision,
                                ),
                            )
                            .order_by(
                                StudioPreferredRenderEvidence.updated_at,
                                StudioPreferredRenderEvidence.draft_id,
                            )
                            .with_for_update(
                                of=StudioPreferredRenderEvidence,
                                skip_locked=True,
                            )
                            .limit(self.item_batch_size)
                        )
                    ).all()
                )
                action_now = await db.scalar(select(func.clock_timestamp()))
                if not isinstance(action_now, datetime):
                    raise RuntimeError("Studio maintenance clock is unavailable")
                for preferred in preferred_rows:
                    draft = await db.scalar(
                        select(StudioDraft).where(
                            StudioDraft.tenant_id == tenant_id,
                            StudioDraft.id == preferred.draft_id,
                        )
                    )
                    if not (
                        draft is not None
                        and draft.lifecycle_state == "active"
                        and draft.cancellation_requested_at is None
                        and draft.revision == preferred.revision
                        and draft.identity_sha256 == preferred.identity_sha256
                        and draft.evidence_revision == preferred.revision
                    ):
                        artifact = await db.scalar(
                            select(StudioRenderArtifact)
                            .where(
                                StudioRenderArtifact.tenant_id == tenant_id,
                                StudioRenderArtifact.id == preferred.artifact_id,
                            )
                            .with_for_update()
                        )
                        if (
                            artifact is not None
                            and artifact.retention_class == "evidence"
                            and artifact.storage_state != "deleted"
                        ):
                            artifact.retention_class = "review"
                            artifact.content_expires_at = action_now + timedelta(
                                seconds=self.artifact_ttl_seconds
                            )
                            artifact.metadata_expires_at = action_now + timedelta(
                                seconds=self.metadata_ttl_seconds
                            )
                        await db.delete(preferred)
                        changed += 1
                await db.commit()
                await set_tenant_context(db, str(tenant_id))

                async def current_evidence(
                    checked_tenant_id: uuid.UUID, artifact_id: uuid.UUID
                ) -> bool:
                    if checked_tenant_id != tenant_id:
                        return True
                    row = await db.execute(
                        select(StudioPreferredRenderEvidence.artifact_id)
                        .join(
                            StudioDraft,
                            (
                                StudioDraft.tenant_id
                                == StudioPreferredRenderEvidence.tenant_id
                            )
                            & (
                                StudioDraft.id
                                == StudioPreferredRenderEvidence.draft_id
                            ),
                        )
                        .where(
                            StudioPreferredRenderEvidence.tenant_id == tenant_id,
                            StudioPreferredRenderEvidence.artifact_id == artifact_id,
                            StudioDraft.lifecycle_state == "active",
                            StudioDraft.cancellation_requested_at.is_(None),
                            StudioDraft.revision
                            == StudioPreferredRenderEvidence.revision,
                            StudioDraft.identity_sha256
                            == StudioPreferredRenderEvidence.identity_sha256,
                            StudioDraft.evidence_revision
                            == StudioPreferredRenderEvidence.revision,
                        )
                    )
                    return row.scalar_one_or_none() is not None

                async def legal_hold(candidate: StudioCleanupCandidate) -> bool:
                    return candidate.legal_hold_at is not None

                retention = StudioArtifactRetentionService(
                    db,
                    tenant_id=tenant_id,
                    object_store=self.object_store,
                    legal_hold_check=legal_hold,
                    current_evidence_check=current_evidence,
                )
                content = await retention.cleanup_batch(limit=self.item_batch_size)
                metadata = await retention.cleanup_metadata_batch(
                    limit=self.item_batch_size
                )
                jobs = await retention.cleanup_durable_jobs_batch(
                    limit=self.item_batch_size
                )
                staged = await StudioStagedReceiptReconciler(
                    db,
                    tenant_id=tenant_id,
                    object_store=self.object_store,
                    legal_hold_check=legal_hold,
                    current_evidence_check=current_evidence,
                ).reconcile_batch(limit=self.item_batch_size)
                changed += sum(item.eligible for item in (*content, *metadata, *jobs))
                changed += sum(
                    item.action in {"acknowledged", "deleted"} for item in staged
                )
            self._tenant_cursor = tenant_id
        return changed
