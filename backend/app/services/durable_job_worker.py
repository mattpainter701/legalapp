"""Handlers and scheduler poller for durable jobs."""

import hmac
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from sqlalchemy import case, or_
from sqlalchemy import select
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
    from app.services.document_sync import document_sync
    from app.routers.documents import _process_document

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
        for index, cloud_doc in enumerate(docs):
            local_path = await document_sync.download_and_process(
                session, tenant_id, cloud_doc, body.get("user_id")
            )
            if not local_path:
                continue
            record = await session.scalar(
                select(Document).where(
                    Document.tenant_id == row.tenant_id,
                    Document.storage_path == local_path,
                )
            )
            if record is None:
                record = Document(
                    id=uuid.uuid4(),
                    tenant_id=row.tenant_id,
                    filename=cloud_doc["name"],
                    content_type=cloud_doc.get("mime_type", "application/octet-stream"),
                    file_size=Path(local_path).stat().st_size,
                    storage_path=local_path,
                    status="pending",
                    chunk_count=0,
                )
                session.add(record)
                await session.commit()
            elif record.status == "ready":
                imported += 1
                continue
            await _process_document(str(record.id), tenant_id)
            await set_tenant_context(session, tenant_id)
            await session.refresh(record)
            if record.status != "ready":
                raise RuntimeError(
                    record.error_message or f"Ingestion failed for {record.filename}"
                )
            imported += 1
            current = await session.get(DurableJob, row.id)
            if current:
                current.progress = min(95, int((index + 1) / max(1, len(docs)) * 95))
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
    app = await session.scalar(
        select(TenantOAuthApp).where(
            TenantOAuthApp.tenant_id == row.tenant_id,
            TenantOAuthApp.provider == "zoom_phone",
            TenantOAuthApp.is_active,
            TenantOAuthApp.encrypted_webhook_secret_token.is_not(None),
        )
    )
    grant = await session.scalar(
        select(TenantCredential).where(
            TenantCredential.tenant_id == row.tenant_id,
            TenantCredential.provider == "zoom_phone",
            TenantCredential.is_active,
        )
    )
    mapping_matches = bool(
        app
        and app.zoom_account_id
        and grant
        and grant.service_account_email
        and hmac.compare_digest(
            app.zoom_account_id.strip(), grant.service_account_email.strip()
        )
    )
    verification = row.payload.get("account_verification")
    pending_exact_event = bool(
        mapping_matches
        and grant
        and grant.health == "account_verification_required"
        and row.kind == ZOOM_PHONE_CALL_JOB
        and isinstance(verification, dict)
        and verification.get("proof") == "signed_v3_call_element"
        and hmac.compare_digest(
            str(verification.get("account_id") or ""),
            app.zoom_account_id.strip(),
        )
        and row.payload.get("event_name")
        in {
            "phone.callee_call_element_completed",
            "phone.caller_call_element_completed",
        }
        and row.payload.get("call_element_id")
        and row.payload.get("call_history_id")
    )
    if (
        not app
        or not grant
        or not app.zoom_account_id
        or not grant.encrypted_refresh_token
        or not grant.service_account_email
        or not mapping_matches
        or (grant.health != "healthy" and not pending_exact_event)
    ):
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone requires a tenant-owned app and refreshable account grant."
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


async def process_job(job_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    async with async_session_maker() as db:
        await set_tenant_context(db, str(tenant_id))
        row = await claim_job(db, job_id)
        if not row:
            return False
        try:
            tenant_active = bool(
                await db.scalar(
                    select(Tenant.is_active).where(Tenant.id == row.tenant_id)
                )
            )
            if not tenant_active and row.kind not in {
                "mcp_stripe_meter",
                *ZOOM_PHONE_JOB_KINDS,
            }:
                result = {"ignored": "inactive_tenant"}
            elif row.kind == "document_ingest":
                result = await _run_document_ingest(row)
            elif row.kind == "cloud_sync":
                result = await _run_cloud_sync(row)
            elif row.kind == "mcp_stripe_meter":
                from app.services.mcp_product import deliver_mcp_meter_event

                result = await deliver_mcp_meter_event(row.payload)
            elif row.kind == "user_sync":
                result = await _run_user_sync(row)
            elif row.kind == ZOOM_PHONE_CALL_JOB:
                result = await _run_zoom_phone_call_import(row)
            elif row.kind == ZOOM_PHONE_RECONCILE_JOB:
                result = await _run_zoom_phone_reconcile(row)
            else:
                raise ValueError(f"Unsupported durable job kind: {row.kind}")
            await set_tenant_context(db, str(tenant_id))
            row = await db.get(DurableJob, job_id)
            await finish_job(db, row, result=result)
        except Exception as exc:
            from app.services.zoom_phone import (
                ZoomPhonePermanentError,
                ZoomPhoneReauthorizationRequired,
            )

            await set_tenant_context(db, str(tenant_id))
            row = await db.get(DurableJob, job_id)
            await fail_job(
                db,
                row,
                exc,
                retryable=not isinstance(
                    exc,
                    (ZoomPhonePermanentError, ZoomPhoneReauthorizationRequired),
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
    for tenant_id in tenant_ids:
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
                        (DurableJob.kind == ZOOM_PHONE_RECONCILE_JOB, 1),
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


async def process_pending_jobs() -> None:
    """Process general jobs without letting them block near-real-time calls."""
    await _process_pending_jobs(exclude_kinds=ZOOM_PHONE_JOB_KINDS)


async def process_pending_zoom_phone_jobs() -> None:
    """Dedicated bounded drain for latency-sensitive Zoom Phone work."""
    await _process_pending_jobs(
        include_kinds=ZOOM_PHONE_JOB_KINDS,
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
                    TenantOAuthApp.encrypted_webhook_secret_token.is_not(None),
                )
            )
            grant_ready = await db.scalar(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_id,
                    TenantCredential.provider == "zoom_phone",
                    TenantCredential.is_active,
                    TenantCredential.encrypted_refresh_token.is_not(None),
                    TenantCredential.service_account_email.is_not(None),
                    TenantCredential.health == "healthy",
                )
            )
            if (
                not app_ready
                or not app_ready.zoom_account_id
                or not grant_ready
                or not grant_ready.service_account_email
                or not hmac.compare_digest(
                    app_ready.zoom_account_id.strip(),
                    grant_ready.service_account_email.strip(),
                )
            ):
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
