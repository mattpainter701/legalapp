"""Handlers and scheduler poller for durable jobs."""

import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_
from sqlalchemy import select
from app.database import async_session_maker, set_tenant_context
from app.models.document import Document
from app.models.durable_job import DurableJob
from app.models.tenant import Tenant
from app.services.durable_jobs import claim_job, fail_job, finish_job


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


async def process_job(job_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    async with async_session_maker() as db:
        await set_tenant_context(db, str(tenant_id))
        row = await claim_job(db, job_id)
        if not row:
            return False
        try:
            if row.kind == "document_ingest":
                result = await _run_document_ingest(row)
            elif row.kind == "cloud_sync":
                result = await _run_cloud_sync(row)
            elif row.kind == "mcp_stripe_meter":
                from app.services.mcp_product import deliver_mcp_meter_event

                result = await deliver_mcp_meter_event(row.payload)
            elif row.kind == "user_sync":
                result = await _run_user_sync(row)
            else:
                raise ValueError(f"Unsupported durable job kind: {row.kind}")
            await set_tenant_context(db, str(tenant_id))
            row = await db.get(DurableJob, job_id)
            await finish_job(db, row, result=result)
        except Exception as exc:
            await set_tenant_context(db, str(tenant_id))
            row = await db.get(DurableJob, job_id)
            await fail_job(db, row, exc)
        return True


async def process_pending_jobs() -> None:
    """Poll one ready or stale job per tenant; row locks prevent duplicates."""
    async with async_session_maker() as db:
        tenant_ids = list((await db.scalars(select(Tenant.id))).all())
    for tenant_id in tenant_ids:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            now = datetime.now(timezone.utc)
            job_id = await db.scalar(
                select(DurableJob.id)
                .where(
                    DurableJob.available_at <= now,
                    or_(
                        DurableJob.status == "pending",
                        (DurableJob.status == "running")
                        & (DurableJob.leased_at < now - timedelta(minutes=15)),
                    ),
                )
                .order_by(DurableJob.available_at, DurableJob.created_at)
                .limit(1)
            )
        if job_id:
            await process_job(job_id, tenant_id)
