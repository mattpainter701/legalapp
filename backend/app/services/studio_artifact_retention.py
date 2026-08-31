"""Retention, legal-hold, and shared-CAS deletion gates for Studio artifacts."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.durable_job import DurableJob
from app.models.studio_render import StudioRenderArtifact
from app.services.studio_object_storage import StudioObjectRef, StudioObjectStore


@dataclass(frozen=True)
class StudioCleanupCandidate:
    tenant_id: uuid.UUID
    artifact_id: uuid.UUID
    job_terminal: bool
    adoption_outcome: str
    retention_class: str
    expires_at: datetime | None
    legal_hold_at: datetime | None = None


@dataclass(frozen=True)
class StudioCleanupDecision:
    artifact_id: uuid.UUID
    eligible: bool
    reason: str


LegalHoldCheck = Callable[[StudioCleanupCandidate], Awaitable[bool]]


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
    if candidate.adoption_outcome == "current_evidence":
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


class StudioArtifactRetentionService:
    """Tenant-scoped, transactionally rechecked artifact cleanup facade."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        object_store: StudioObjectStore,
        legal_hold_check: LegalHoldCheck,
        legal_hold_timeout_seconds: float = 5.0,
    ):
        if not 0.01 <= legal_hold_timeout_seconds <= 30:
            raise ValueError("legal hold timeout must be between 0.01 and 30 seconds")
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.object_store = object_store
        self.legal_hold_check = legal_hold_check
        self.legal_hold_timeout_seconds = legal_hold_timeout_seconds

    async def _object_lock(self, object_key: str) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-object:{object_key}"},
        )

    async def _candidate(
        self, artifact: StudioRenderArtifact
    ) -> StudioCleanupCandidate:
        job = await self.db.scalar(
            select(DurableJob).where(
                DurableJob.id == artifact.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
        )
        return StudioCleanupCandidate(
            tenant_id=self.tenant_id,
            artifact_id=artifact.id,
            job_terminal=job is not None
            and job.status in {"completed", "failed", "cancelled"},
            adoption_outcome=artifact.adoption_outcome,
            retention_class=artifact.retention_class,
            expires_at=artifact.expires_at,
            legal_hold_at=artifact.legal_hold_at,
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

        task = asyncio.create_task(asyncio.to_thread(self.object_store.delete, ref))
        cancellation_requested = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Cancelling to_thread does not stop its worker thread. Drain
                # every cancellation request until the server-owned primitive
                # finishes, then preserve cancellation for the caller. This
                # favors no-late-delete safety over liveness; remote backends
                # must provide a genuinely bounded/cancellable delete API.
                cancellation_requested = True
        if cancellation_requested:
            try:
                task.result()
            except BaseException:
                pass
            raise asyncio.CancelledError
        task.result()

    async def _finalize_pending_delete(
        self,
        artifact_id: uuid.UUID,
        *,
        object_key: str,
        now: datetime,
    ) -> StudioCleanupDecision:
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

        decision = await cleanup_decision(
            await self._candidate(artifact),
            now=now,
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
            artifact.storage_state = "active"
            artifact.delete_requested_at = None
            await self.db.commit()
            return decision

        if await self._active_reference_count(artifact, object_key):
            artifact.storage_state = "deleted"
            artifact.deleted_at = now
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
        artifact.storage_state = "deleted"
        artifact.deleted_at = now
        await self.db.commit()
        return decision

    async def delete_if_eligible(
        self, artifact_id: uuid.UUID, *, now: datetime
    ) -> StudioCleanupDecision:
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
                now=now,
            )
        decision = await cleanup_decision(
            await self._candidate(artifact),
            now=now,
            legal_hold_check=self.legal_hold_check,
            legal_hold_timeout_seconds=self.legal_hold_timeout_seconds,
        )
        if not decision.eligible:
            await self.db.rollback()
            return decision

        other_active_refs = await self._active_reference_count(artifact, object_key)
        artifact.storage_state = "delete_pending"
        artifact.delete_requested_at = now
        if other_active_refs == 0:
            # Durable phase A: a crash before physical deletion leaves an
            # explicit retryable marker rather than an active row for a missing
            # object. Phase B reacquires and rechecks the object lock.
            await self.db.commit()
            return await self._finalize_pending_delete(
                artifact.id,
                object_key=object_key,
                now=now,
            )
        artifact.storage_state = "deleted"
        artifact.deleted_at = now
        await self.db.commit()
        return decision

