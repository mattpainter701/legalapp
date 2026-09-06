"""Handlers and scheduler poller for durable jobs."""

import asyncio
import hashlib
import hmac
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import case, or_
from sqlalchemy import select, text
from app.database import async_session_maker, set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.document import Document
from app.models.durable_job import DurableJob
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.services.durable_jobs import (
    claim_job,
    enqueue_job,
    fail_job,
    finish_job,
)

ZOOM_PHONE_CALL_JOB = "zoom_phone_call_import"
ZOOM_PHONE_RECONCILE_JOB = "zoom_phone_reconcile"
ZOOM_PHONE_JOB_KINDS = {ZOOM_PHONE_CALL_JOB, ZOOM_PHONE_RECONCILE_JOB}
TEAMS_VOICE_CALL_JOB = "teams_voice_call_import"
TEAMS_VOICE_RECONCILE_JOB = "teams_voice_reconcile"
TEAMS_VOICE_JOB_KINDS = {TEAMS_VOICE_CALL_JOB, TEAMS_VOICE_RECONCILE_JOB}
# Both providers capture inbound calls into the intake dashboard, so both are
# drained on the low-latency path rather than behind general background work.
VOICE_JOB_KINDS = ZOOM_PHONE_JOB_KINDS | TEAMS_VOICE_JOB_KINDS
# Declared here rather than imported so the worker's kind table stays readable
# in one place; task_automation owns the same literal.
TASK_AUTOMATION_JOB = "task_automation"
_ZOOM_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,255}$")


def _path_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cloud_modified_at(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _zoom_account_binding(value) -> str | None:
    normalized = str(value or "").strip()
    if (
        not normalized
        or normalized.isdecimal()
        or not _ZOOM_ACCOUNT_ID_PATTERN.fullmatch(normalized)
    ):
        return None
    return normalized


async def _run_document_ingest(row: DurableJob) -> dict:
    from app.routers.documents import _process_document

    await _process_document(row.payload["document_id"], str(row.tenant_id))
    async with async_session_maker() as check:
        await set_tenant_context(check, str(row.tenant_id))
        doc = await check.get(Document, uuid.UUID(row.payload["document_id"]))
        if not doc or doc.status != "ready":
            raise RuntimeError(
                (doc.error_message if doc else None)
                or "Document ingestion did not complete"
            )
        return {"document_id": str(doc.id), "chunks": doc.chunk_count}


async def _run_cloud_sync(row: DurableJob) -> dict:
    from app.routers.documents import _process_document
    from app.services.corpus_revision import advance_rag_corpus_revision
    from app.services.document_sync import (
        _legacy_synced_storage_path,
        _sync_source_key,
        document_sync,
    )

    body, tenant_id = row.payload, str(row.tenant_id)
    imported = 0
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        provider = body["provider"]
        if provider == "onedrive":
            docs = await document_sync.sync_onedrive(
                session,
                tenant_id,
                body.get("user_id"),
                max_files=body.get("max_files", 100),
            )
        elif provider == "sharepoint":
            docs = await document_sync.sync_sharepoint(
                session,
                tenant_id,
                site_id=body.get("site_id"),
                max_files=body.get("max_files", 100),
            )
        elif provider == "google_drive":
            docs = await document_sync.sync_google_drive(
                session,
                tenant_id,
                body.get("user_id"),
                max_files=body.get("max_files", 100),
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        # OAuth refreshes commit the shared session and clear SET LOCAL.
        await set_tenant_context(session, tenant_id)
        for index, cloud_doc in enumerate(docs):
            local_path = await document_sync.download_and_process(
                session, tenant_id, cloud_doc, body.get("user_id")
            )
            # Download can refresh OAuth too.
            await set_tenant_context(session, tenant_id)
            if not local_path:
                continue

            source_key = _sync_source_key(cloud_doc)
            lock_key = f"cloud-sync:{tenant_id}:{source_key}"
            await session.execute(
                text("SELECT pg_advisory_xact_lock(" "hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            records = (
                (
                    await session.execute(
                        select(Document)
                        .where(
                            Document.tenant_id == row.tenant_id,
                            Document.storage_path == local_path,
                        )
                        .order_by(Document.created_at, Document.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            record = records[0] if records else None

            # Adopt an unchanged pre-versioning sync row so deployment does not
            # duplicate its chunks or break its durable document id.
            if record is None:
                legacy_path = _legacy_synced_storage_path(
                    Path(local_path).parent,
                    cloud_doc,
                )
                legacy_records = (
                    (
                        await session.execute(
                            select(Document)
                            .where(
                                Document.tenant_id == row.tenant_id,
                                Document.storage_path == str(legacy_path),
                                Document.sync_source_key.is_(None),
                            )
                            .order_by(Document.created_at, Document.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                local_digest = _path_sha256(local_path)
                record = next(
                    (
                        candidate
                        for candidate in legacy_records
                        if candidate.storage_path
                        and Path(candidate.storage_path).is_file()
                        and _path_sha256(candidate.storage_path) == local_digest
                    ),
                    None,
                )
                if record is not None:
                    record.storage_path = local_path
                    record.file_size = Path(local_path).stat().st_size
                    record.sync_source_key = source_key
                    record.source_modified_at = _cloud_modified_at(
                        cloud_doc.get("modified")
                    )
                elif legacy_records:
                    # The provider changed since the last legacy sync. Retain
                    # the old row/path as the prior audit version, but attach
                    # it to this remote identity so finalization removes its
                    # obsolete chunks from current RAG results.
                    legacy_records[0].sync_source_key = source_key

            if record is None:
                record = Document(
                    id=uuid.uuid4(),
                    tenant_id=row.tenant_id,
                    filename=cloud_doc["name"],
                    content_type=cloud_doc.get(
                        "mime_type",
                        "application/octet-stream",
                    ),
                    file_size=Path(local_path).stat().st_size,
                    storage_path=local_path,
                    status="pending",
                    chunk_count=0,
                    sync_source_key=source_key,
                    source_modified_at=_cloud_modified_at(cloud_doc.get("modified")),
                )
                session.add(record)
            else:
                if record.sync_source_key != source_key:
                    record.sync_source_key = source_key
                modified_at = _cloud_modified_at(cloud_doc.get("modified"))
                if modified_at and record.source_modified_at != modified_at:
                    record.source_modified_at = modified_at
            claim_marker = f"cloud_sync_claim:{row.id}"
            claimed_for_processing = False
            processing_elsewhere = False
            if record.status not in {"ready", "superseded", "staged"}:
                if (
                    record.status != "processing"
                    or record.error_message == claim_marker
                ):
                    record.status = "processing"
                    record.error_message = claim_marker
                    claimed_for_processing = True
                else:
                    processing_elsewhere = True
            await session.commit()

            if claimed_for_processing:
                await _process_document(
                    str(record.id),
                    tenant_id,
                    expected_claim=claim_marker,
                )
            elif processing_elsewhere:
                # The row claim is durable; the owning job will finalize this
                # source version. Do not run a second extractor/embedder.
                continue

            # Pick the newest successfully indexed version under the source
            # lock. Different content versions may ingest concurrently, so
            # completion order must not decide which contract RAG considers
            # current.
            await set_tenant_context(session, tenant_id)
            await session.execute(
                text("SELECT pg_advisory_xact_lock(" "hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            versions = (
                (
                    await session.execute(
                        select(Document)
                        .where(
                            Document.tenant_id == row.tenant_id,
                            Document.sync_source_key == source_key,
                        )
                        .order_by(Document.created_at, Document.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            record = next((item for item in versions if item.id == record.id), None)
            if record is None or record.status not in {
                "ready",
                "superseded",
                "staged",
            }:
                raise RuntimeError(
                    (record.error_message if record else None)
                    or f"Ingestion failed for {cloud_doc['name']}"
                )
            indexed_versions = [
                item
                for item in versions
                if item.status in {"ready", "superseded", "staged"}
            ]
            current_version = max(
                indexed_versions,
                key=lambda item: (
                    item.source_modified_at
                    or item.created_at
                    or datetime.min.replace(tzinfo=timezone.utc),
                    str(item.id),
                ),
            )
            for version in indexed_versions:
                desired_status = (
                    "ready" if version.id == current_version.id else "superseded"
                )
                if version.status != desired_status:
                    version.status = desired_status
            imported += 1
            current = await session.get(DurableJob, row.id)
            if current:
                current.progress = min(95, int((index + 1) / max(1, len(docs)) * 95))
            await advance_rag_corpus_revision(session, row.tenant_id)
            await session.commit()
    return {"documents_found": len(docs), "documents_ingested": imported}


async def _run_user_sync(row: DurableJob) -> dict:
    """Run a tenant-requested directory sync in the scheduler-owned worker."""
    from app.models.tenant_credential import TenantCredential
    from app.services.user_sync import user_sync

    tenant_id = str(row.tenant_id)
    results: dict[str, dict] = {}
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        providers = list(
            (
                await session.execute(
                    select(TenantCredential.provider).where(
                        TenantCredential.is_active,
                        TenantCredential.provider.in_(["microsoft", "google"]),
                    )
                )
            ).scalars()
        )
        for provider in sorted(set(providers)):
            if provider == "microsoft":
                results[provider] = await user_sync.sync_microsoft_users(
                    session, tenant_id
                )
            else:
                results[provider] = await user_sync.sync_google_users(
                    session, tenant_id
                )
            await set_tenant_context(session, tenant_id)
    return {"providers": results, "provider_count": len(results)}


async def _zoom_phone_tenant_ready(session, row: DurableJob) -> bool:
    """Recheck tenant/integration state immediately before provider egress."""
    from app.services.zoom_phone import ZoomPhoneReauthorizationRequired

    active = await session.scalar(
        select(Tenant.is_active).where(Tenant.id == row.tenant_id)
    )
    if not active:
        return False
    app_query = select(TenantOAuthApp).where(
        TenantOAuthApp.tenant_id == row.tenant_id,
        TenantOAuthApp.provider == "zoom_phone",
        TenantOAuthApp.is_active,
    )
    if row.kind == ZOOM_PHONE_CALL_JOB:
        app_query = app_query.where(
            TenantOAuthApp.encrypted_webhook_secret_token.is_not(None)
        )
    app = await session.scalar(app_query)
    grant = await session.scalar(
        select(TenantCredential).where(
            TenantCredential.tenant_id == row.tenant_id,
            TenantCredential.provider == "zoom_phone",
            TenantCredential.is_active,
        )
    )
    if (
        not app
        or not grant
        or not grant.encrypted_refresh_token
        or grant.health not in {"healthy", "account_verification_required"}
    ):
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone requires a tenant-owned app and refreshable account grant."
        )

    # Historical reconciliation is an API-only workflow. Webhook account proof
    # must never suppress it or manual history sync.
    if row.kind == ZOOM_PHONE_RECONCILE_JOB:
        return True

    app_binding = _zoom_account_binding(app.zoom_account_id)
    grant_binding = _zoom_account_binding(grant.service_account_email)
    if (
        app_binding
        and grant_binding
        and not hmac.compare_digest(app_binding, grant_binding)
    ):
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone webhook and grant account bindings conflict."
        )
    mapping_verified = bool(app_binding and grant_binding)
    binding_proof = row.payload.get("account_binding")
    proof_account = (
        _zoom_account_binding(binding_proof.get("account_id"))
        if isinstance(binding_proof, dict)
        and binding_proof.get("proof") == "signed_event_exact_call_fetch"
        else None
    )
    if not mapping_verified and not proof_account:
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone webhook account binding requires exact-call proof."
        )
    if proof_account and (
        (app_binding and not hmac.compare_digest(proof_account, app_binding))
        or (grant_binding and not hmac.compare_digest(proof_account, grant_binding))
    ):
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone exact-call proof does not match the existing binding."
        )
    return True


async def _run_zoom_phone_call_import(row: DurableJob) -> dict:
    from app.services.zoom_phone import import_zoom_phone_webhook_job

    tenant_id = str(row.tenant_id)
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        if not await _zoom_phone_tenant_ready(session, row):
            return {"ignored": "inactive_tenant"}
        result = await import_zoom_phone_webhook_job(
            session,
            tenant_id=tenant_id,
            payload=row.payload,
        )
        await session.commit()
        return {
            "imported": result.imported,
            "updated": result.updated,
            "skipped": result.skipped,
        }


async def _run_zoom_phone_reconcile(row: DurableJob) -> dict:
    from app.services.zoom_phone import sync_zoom_phone_call_history

    tenant_id = str(row.tenant_id)
    days = max(1, min(int(row.payload.get("days") or 2), 7))
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        if not await _zoom_phone_tenant_ready(session, row):
            return {"ignored": "inactive_tenant"}
        result = await sync_zoom_phone_call_history(
            session,
            tenant_id=tenant_id,
            days=days,
        )

        # A signed webhook with an exhausted/non-retryable call job remains a
        # durable record of the missed work. Reconciliation may repair it, but
        # only a canonical communication row for that job's stable call ID is
        # proof that this specific work is complete. Never blanket-clear other
        # tenant failures.
        failed_jobs = list(
            (
                await session.scalars(
                    select(DurableJob).where(
                        DurableJob.tenant_id == row.tenant_id,
                        DurableJob.kind == ZOOM_PHONE_CALL_JOB,
                        DurableJob.status == "failed",
                    )
                )
            ).all()
        )
        expected_ref_by_job: dict[uuid.UUID, str] = {}
        for failed_job in failed_jobs:
            stable_call_id = str(failed_job.payload.get("stable_call_id") or "").strip()
            if stable_call_id:
                expected_ref_by_job[failed_job.id] = f"zoom_phone:call:{stable_call_id}"
        existing_refs: set[str] = set()
        if expected_ref_by_job:
            existing_refs = set(
                (
                    await session.scalars(
                        select(CommunicationLog.external_ref).where(
                            CommunicationLog.tenant_id == row.tenant_id,
                            CommunicationLog.external_ref.in_(
                                set(expected_ref_by_job.values())
                            ),
                        )
                    )
                ).all()
            )
        repaired_failed_jobs = 0
        completed_at = datetime.now(timezone.utc)
        for failed_job in failed_jobs:
            if expected_ref_by_job.get(failed_job.id) not in existing_refs:
                continue
            failed_job.status = "completed"
            failed_job.progress = 100
            failed_job.result = {"reconciled": True}
            failed_job.completed_at = completed_at
            failed_job.leased_at = None
            failed_job.lease_owner = None
            failed_job.last_error = None
            repaired_failed_jobs += 1
        await session.commit()
        return {
            "imported": result.imported,
            "updated": result.updated,
            "skipped": result.skipped,
            "days": days,
            "repaired_failed_jobs": repaired_failed_jobs,
        }


async def _teams_voice_tenant_ready(session, row: DurableJob) -> bool:
    """Recheck tenant + voice configuration immediately before Graph egress."""
    from app.models.teams_voice_setting import TeamsVoiceSetting
    from app.services.teams_voice import TeamsVoiceNotConfigured

    active = await session.scalar(
        select(Tenant.is_active).where(Tenant.id == row.tenant_id)
    )
    if not active:
        return False
    voice = await session.scalar(
        select(TeamsVoiceSetting).where(
            TeamsVoiceSetting.tenant_id == row.tenant_id,
            TeamsVoiceSetting.is_enabled.is_(True),
        )
    )
    if not voice or not voice.entra_tenant_id:
        # Permanent for this job: a disabled or unconfigured tenant will not
        # become ready by retrying the same Graph read.
        raise TeamsVoiceNotConfigured(
            "Teams voice capture is not enabled for this tenant."
        )
    return True


async def _announce_captured_voice_calls(tenant_id: str, captured: list[dict]) -> int:
    """Post a Teams card for each newly captured inbound call.

    Runs after the capture transaction commits: a channel post is an outward
    side effect and must never be able to roll back with — or hold open — the
    write that recorded the call.
    """
    from app.services import teams_notify

    announced = 0
    for call in captured:
        caller = (
            call.get("caller_name") or call.get("caller_number") or "Unknown caller"
        )
        fields = {"matter_name": caller}
        if call.get("caller_number"):
            fields["Number"] = str(call["caller_number"])
        if call.get("callee_name"):
            fields["Answered by"] = str(call["callee_name"])
        if call.get("duration_seconds") is not None:
            fields["Duration"] = f"{call['duration_seconds']}s"
        if call.get("result"):
            fields["Result"] = str(call["result"])
        announced += await teams_notify.notify(
            tenant_id,
            "voice_call_captured",
            title="Inbound Teams call captured",
            fields=fields,
        )
    return announced


async def _run_teams_voice_call_import(row: DurableJob) -> dict:
    from app.services.teams_voice import import_teams_voice_webhook_job

    tenant_id = str(row.tenant_id)
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        if not await _teams_voice_tenant_ready(session, row):
            return {"ignored": "inactive_tenant"}
        result = await import_teams_voice_webhook_job(
            session,
            tenant_id=tenant_id,
            payload=row.payload,
        )
        await session.commit()

    announced = await _announce_captured_voice_calls(tenant_id, result.captured)
    return {
        "imported": result.imported,
        "updated": result.updated,
        "skipped": result.skipped,
        "announced": announced,
    }


async def _run_teams_voice_reconcile(row: DurableJob) -> dict:
    from app.services.teams_voice import sync_teams_voice_call_history

    tenant_id = str(row.tenant_id)
    days = max(1, min(int(row.payload.get("days") or 2), 7))
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        if not await _teams_voice_tenant_ready(session, row):
            return {"ignored": "inactive_tenant"}
        result = await sync_teams_voice_call_history(
            session,
            tenant_id=tenant_id,
            days=days,
        )
        await session.commit()
        return {
            "imported": result.imported,
            "updated": result.updated,
            "skipped": result.skipped,
            "days": days,
        }


async def process_job(job_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    async with async_session_maker() as db:
        await set_tenant_context(db, str(tenant_id))
        exhausted = await db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == job_id,
                DurableJob.tenant_id == tenant_id,
                DurableJob.kind == "matter_workflow_plan",
                DurableJob.status == "running",
                DurableJob.attempts >= DurableJob.max_attempts,
                DurableJob.leased_at
                < datetime.now(timezone.utc) - timedelta(minutes=15),
            )
            .with_for_update(skip_locked=True)
        )
        if exhausted:
            await fail_job(
                db,
                exhausted,
                RuntimeError("Workflow planning retry limit reached"),
                retryable=False,
            )
            return True
        row = await claim_job(db, job_id)
        if not row:
            return False
        workflow_planning_job = row.kind == "matter_workflow_plan"
        claim_token = (row.attempts, row.leased_at)
        try:
            # claim_job commits; restore transaction-local tenant context.
            await set_tenant_context(db, str(tenant_id))
            if row.kind == "matter_workflow_plan":
                # Hold through completion so lease recovery cannot overlap a
                # still-running planner, and completion commits the plan too.
                row = await db.scalar(
                    select(DurableJob)
                    .where(
                        DurableJob.id == job_id,
                        DurableJob.tenant_id == tenant_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    row is None
                    or row.status != "running"
                    or (row.attempts, row.leased_at) != claim_token
                ):
                    return False
            tenant_active = bool(
                await db.scalar(
                    select(Tenant.is_active).where(Tenant.id == row.tenant_id)
                )
            )
            if not tenant_active and row.kind not in {
                "mcp_stripe_meter",
                *VOICE_JOB_KINDS,
            }:
                result = (
                    {"outcome": "blocked", "failure_code": "inactive_tenant"}
                    if row.kind == "matter_workflow_plan"
                    else {"ignored": "inactive_tenant"}
                )
            elif row.kind == "matter_workflow_plan":
                from app.services.durable_workflow_automations import run_planning_job

                result = await run_planning_job(db, row)
            elif row.kind == "document_ingest":
                result = await _run_document_ingest(row)
            elif row.kind == "cloud_sync":
                result = await _run_cloud_sync(row)
            elif row.kind == "mcp_stripe_meter":
                from app.services.mcp_product import deliver_mcp_meter_event

                result = await deliver_mcp_meter_event(row.payload)
            elif row.kind == "user_sync":
                result = await _run_user_sync(row)
            elif row.kind == TASK_AUTOMATION_JOB:
                from app.services.task_automation import run_task_automation_job

                result = await run_task_automation_job(row)
            elif row.kind == ZOOM_PHONE_CALL_JOB:
                result = await _run_zoom_phone_call_import(row)
            elif row.kind == ZOOM_PHONE_RECONCILE_JOB:
                result = await _run_zoom_phone_reconcile(row)
            elif row.kind == TEAMS_VOICE_CALL_JOB:
                result = await _run_teams_voice_call_import(row)
            elif row.kind == TEAMS_VOICE_RECONCILE_JOB:
                result = await _run_teams_voice_reconcile(row)
            else:
                raise ValueError(f"Unsupported durable job kind: {row.kind}")
            await set_tenant_context(db, str(tenant_id))
            row = await db.get(DurableJob, job_id)
            await finish_job(db, row, result=result)
        except Exception as exc:
            failure = exc
            if workflow_planning_job:
                # Never persist SQL parameters or source content in job errors.
                await db.rollback()
                failure = RuntimeError(
                    "Workflow planning temporarily unavailable; retry scheduled"
                )
            from app.services.teams_voice import (
                TeamsVoiceNotConfigured,
                TeamsVoicePermanentError,
            )
            from app.services.zoom_phone import (
                ZoomPhonePermanentError,
                ZoomPhoneReauthorizationRequired,
            )

            await set_tenant_context(db, str(tenant_id))
            if workflow_planning_job:
                row = await db.scalar(
                    select(DurableJob)
                    .where(
                        DurableJob.id == job_id,
                        DurableJob.tenant_id == tenant_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    row is None
                    or row.status != "running"
                    or (row.attempts, row.leased_at) != claim_token
                ):
                    return False
            else:
                row = await db.get(DurableJob, job_id)
            await fail_job(
                db,
                row,
                failure,
                retryable=not isinstance(
                    failure,
                    (
                        ZoomPhonePermanentError,
                        ZoomPhoneReauthorizationRequired,
                        TeamsVoicePermanentError,
                        TeamsVoiceNotConfigured,
                    ),
                ),
            )
        return True


async def _process_pending_jobs(
    *,
    include_kinds: set[str] | None = None,
    exclude_kinds: set[str] | None = None,
    per_tenant_limit: int = 1,
) -> None:
    """Drain ready jobs; handlers enforce provider-specific tenant state."""
    async with async_session_maker() as db:
        tenant_ids = list((await db.scalars(select(Tenant.id))).all())

    async def drain_tenant(tenant_id: uuid.UUID) -> None:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            now = datetime.now(timezone.utc)
            stmt = (
                select(DurableJob.id)
                .where(
                    DurableJob.tenant_id == tenant_id,
                    DurableJob.available_at <= now,
                    or_(
                        DurableJob.status == "pending",
                        (DurableJob.status == "running")
                        & (DurableJob.leased_at < now - timedelta(minutes=15)),
                    ),
                )
                .order_by(
                    case(
                        (DurableJob.kind == ZOOM_PHONE_CALL_JOB, 0),
                        (DurableJob.kind == TEAMS_VOICE_CALL_JOB, 0),
                        (DurableJob.kind == ZOOM_PHONE_RECONCILE_JOB, 1),
                        (DurableJob.kind == TEAMS_VOICE_RECONCILE_JOB, 1),
                        else_=2,
                    ),
                    DurableJob.available_at,
                    DurableJob.created_at,
                )
                .limit(per_tenant_limit)
            )
            if include_kinds:
                stmt = stmt.where(DurableJob.kind.in_(include_kinds))
            if exclude_kinds:
                stmt = stmt.where(DurableJob.kind.not_in(exclude_kinds))
            job_ids = list((await db.scalars(stmt)).all())
        for job_id in job_ids:
            await process_job(job_id, tenant_id)

    from app.config import get_settings

    await _run_bounded(
        tenant_ids,
        drain_tenant,
        concurrency=get_settings().DURABLE_JOB_TENANT_CONCURRENCY,
    )


async def _run_bounded(
    items: Iterable[Any],
    handler: Callable[[Any], Awaitable[None]],
    *,
    concurrency: int,
) -> None:
    """Run independent work with a fixed number of worker coroutines."""
    iterator = iter(items)

    async def worker() -> None:
        for item in iterator:
            await handler(item)

    await asyncio.gather(*(worker() for _ in range(concurrency)))


async def process_pending_jobs() -> None:
    """Process general jobs without letting them block near-real-time calls."""
    from app.schemas.studio_render import STUDIO_RENDER_JOB_KINDS

    await _process_pending_jobs(
        exclude_kinds=VOICE_JOB_KINDS | set(STUDIO_RENDER_JOB_KINDS)
    )


async def process_pending_zoom_phone_jobs() -> None:
    """Dedicated bounded drain for latency-sensitive Zoom Phone work."""
    await _process_pending_jobs(
        include_kinds=ZOOM_PHONE_JOB_KINDS,
        per_tenant_limit=10,
    )


async def process_pending_teams_voice_jobs() -> None:
    """Dedicated bounded drain for latency-sensitive Teams voice work."""
    await _process_pending_jobs(
        include_kinds=TEAMS_VOICE_JOB_KINDS,
        per_tenant_limit=10,
    )


async def enqueue_zoom_phone_reconciliation_jobs() -> None:
    """Enqueue one bounded-time-bucket reconciliation per connected tenant."""
    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp() // (60 * 60))
    async with async_session_maker() as root:
        tenant_ids = list(
            (
                await root.scalars(
                    select(Tenant.id)
                    .where(Tenant.is_active.is_(True))
                    .order_by(Tenant.id)
                )
            ).all()
        )
    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            app_ready = await db.scalar(
                select(TenantOAuthApp).where(
                    TenantOAuthApp.tenant_id == tenant_id,
                    TenantOAuthApp.provider == "zoom_phone",
                    TenantOAuthApp.is_active,
                )
            )
            grant_ready = await db.scalar(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_id,
                    TenantCredential.provider == "zoom_phone",
                    TenantCredential.is_active,
                    TenantCredential.encrypted_refresh_token.is_not(None),
                    TenantCredential.health.in_(
                        ["healthy", "account_verification_required"]
                    ),
                )
            )
            if not app_ready or not grant_ready:
                continue
            outstanding = await db.scalar(
                select(DurableJob.id).where(
                    DurableJob.tenant_id == tenant_id,
                    DurableJob.kind == ZOOM_PHONE_RECONCILE_JOB,
                    DurableJob.status.in_(["pending", "running"]),
                )
            )
            if outstanding:
                continue
            await enqueue_job(
                db,
                tenant_id=tenant_id,
                kind=ZOOM_PHONE_RECONCILE_JOB,
                idempotency_key=f"hour:{bucket}",
                payload={"days": 1},
            )
            await db.commit()


async def enqueue_teams_voice_reconciliation_jobs() -> None:
    """Enqueue one bounded reconciliation per voice-enabled tenant.

    Graph change notifications are the primary feed; this hourly sweep of the
    PSTN report is the backstop that heals anything the notification path
    dropped. One outstanding job per tenant at a time.
    """
    from app.models.teams_voice_setting import TeamsVoiceSetting

    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp() // (60 * 60))
    async with async_session_maker() as root:
        tenant_ids = list(
            (
                await root.scalars(
                    select(Tenant.id)
                    .where(Tenant.is_active.is_(True))
                    .order_by(Tenant.id)
                )
            ).all()
        )
    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            voice_ready = await db.scalar(
                select(TeamsVoiceSetting).where(
                    TeamsVoiceSetting.tenant_id == tenant_id,
                    TeamsVoiceSetting.is_enabled.is_(True),
                    TeamsVoiceSetting.entra_tenant_id.is_not(None),
                )
            )
            if not voice_ready:
                continue
            outstanding = await db.scalar(
                select(DurableJob.id).where(
                    DurableJob.tenant_id == tenant_id,
                    DurableJob.kind == TEAMS_VOICE_RECONCILE_JOB,
                    DurableJob.status.in_(["pending", "running"]),
                )
            )
            if outstanding:
                continue
            await enqueue_job(
                db,
                tenant_id=tenant_id,
                kind=TEAMS_VOICE_RECONCILE_JOB,
                idempotency_key=f"hour:{bucket}",
                payload={"days": 1},
            )
            await db.commit()


async def renew_teams_voice_subscriptions() -> None:
    """Renew Graph call-record subscriptions before they lapse.

    A callRecords subscription lives at most ~3 days. Left to expire, the
    tenant silently loses the low-latency feed and only the hourly PSTN sweep
    keeps working — so this runs on its own schedule rather than as a durable
    job, and failures are recorded on the settings row for the admin panel.
    """
    from app.config import get_settings as _get_settings
    from app.models.teams_voice_setting import TeamsVoiceSetting
    from app.services.teams_voice import (
        TeamsVoiceError,
        ensure_subscription,
        subscription_needs_renewal,
    )

    app_settings = _get_settings()
    async with async_session_maker() as root:
        tenant_ids = list(
            (
                await root.scalars(
                    select(Tenant.id)
                    .where(Tenant.is_active.is_(True))
                    .order_by(Tenant.id)
                )
            ).all()
        )

    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            row = await db.scalar(
                select(TeamsVoiceSetting).where(
                    TeamsVoiceSetting.tenant_id == tenant_id,
                    TeamsVoiceSetting.is_enabled.is_(True),
                    TeamsVoiceSetting.entra_tenant_id.is_not(None),
                )
            )
            if not row or not subscription_needs_renewal(row):
                continue
            notification_url = row.notification_url or (
                f"{app_settings.BACKEND_URL}/api/integrations/teams/voice/webhook/"
                f"{tenant_id}"
            )
            try:
                await ensure_subscription(
                    db,
                    tenant_id=str(tenant_id),
                    notification_url=notification_url,
                )
                row.last_sync_error = None
            except TeamsVoiceError as exc:
                # Surfaced in the admin panel; the PSTN sweep keeps capturing
                # calls in the meantime, just with more delay.
                row.last_sync_status = "subscription_error"
                row.last_sync_error = str(exc)[:1000]
            except Exception:
                row.last_sync_status = "subscription_error"
                row.last_sync_error = "Unexpected error renewing the subscription."
            await db.commit()
