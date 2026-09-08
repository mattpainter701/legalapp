"""Reconcile an uncertain cloud write by verifying its original exact bytes."""

from types import SimpleNamespace
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.document_storage_operation import DocumentStorageOperation
from app.models.workflow_run import WorkflowRunStep
from app.services.automation_capabilities import CapabilityError
from app.services.matter_file_store import (
    MatterFileStore,
    GRAPH_BASE,
    GOOGLE_DOWNLOAD_BASE,
)
from app.services.token_vault import get_fresh_token
from app.services.workflow_run_ledger import (
    load_run,
    append_event,
    queue_run,
    describe_run,
)


async def verify_provider_object(*, tenant_id, operation, provider_object_id):
    """Provider IDs are data; all URLs and credentials remain server-selected."""
    backend = operation.target_backend
    item = quote(provider_object_id, safe="")
    if backend == "google_drive":
        provider = "google"
        url = f"{GOOGLE_DOWNLOAD_BASE}/files/{item}"
        params = {
            "supportsAllDrives": "true",
            "fields": "id,name,parents,driveId,version,trashed",
        }
    elif backend in {"onedrive", "sharepoint"}:
        provider = "microsoft"
        drive = operation.target_drive_id
        if backend == "sharepoint" and not drive:
            raise CapabilityError(
                "cloud_binding_unavailable", "The original cloud drive is unavailable"
            )
        base = (
            f"{GRAPH_BASE}/drives/{quote(drive, safe='')}/items"
            if drive
            else f"{GRAPH_BASE}/me/drive/items"
        )
        url = f"{base}/{item}"
        params = {"$select": "id,name,parentReference,eTag,cTag,deleted"}
    else:
        raise CapabilityError(
            "cloud_binding_unavailable", "The original cloud provider is unavailable"
        )
    async with async_session_maker() as provider_db:
        await set_tenant_context(provider_db, str(tenant_id))
        token = await get_fresh_token(provider_db, str(tenant_id), provider)
        if not token:
            raise CapabilityError(
                "cloud_credentials_unavailable", "Reconnect the firm's cloud storage"
            )
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                response = await client.get(
                    url, params=params, headers={"Authorization": f"Bearer {token}"}
                )
                response.raise_for_status()
                metadata = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise CapabilityError(
                "cloud_verification_unavailable",
                "The provider object could not be verified",
            ) from error
        if (
            not isinstance(metadata, dict)
            or metadata.get("id") != provider_object_id
            or "deleted" in metadata
            or metadata.get("trashed")
        ):
            raise CapabilityError(
                "cloud_object_unavailable", "The provider object is unavailable"
            )
        if backend == "google_drive":
            parents = metadata.get("parents") or []
            parent_matches = operation.target_parent_id in parents
            drive_matches = (
                not operation.target_drive_id
                or metadata.get("driveId") == operation.target_drive_id
            )
        else:
            parent = metadata.get("parentReference") or {}
            parent_matches = parent.get("id") == operation.target_parent_id
            drive_matches = (
                not operation.target_drive_id
                or parent.get("driveId") == operation.target_drive_id
            )
        if not parent_matches or not drive_matches:
            raise CapabilityError(
                "cloud_folder_mismatch",
                "The object is outside the original matter folder",
            )
        document = SimpleNamespace(
            tenant_id=tenant_id,
            storage_backend=backend,
            storage_provider=provider,
            provider_object_id=provider_object_id,
            provider_drive_id=operation.target_drive_id,
            file_size=operation.content_size,
        )
        try:
            await MatterFileStore().read_matter_file_bytes(
                db=provider_db,
                tenant_id=str(tenant_id),
                document=document,
                expected_sha256=operation.content_sha256,
                expected_size=operation.content_size,
                max_bytes=16 * 1024 * 1024,
            )
        except Exception as error:
            raise CapabilityError(
                "cloud_bytes_mismatch",
                "The object does not verify against the original draft bytes",
            ) from error
    return {
        "provider_etag": metadata.get("eTag"),
        "provider_version_id": metadata.get("version") or metadata.get("cTag"),
    }


async def reconcile_run(context, run_id, body):
    db = context.db
    run = await load_run(context, run_id, lock=True)
    if run.version != body.expected_version:
        raise CapabilityError(
            "run_version_conflict", "Reload this run before reconciling"
        )
    if run.status != "reconciliation_required":
        raise CapabilityError(
            "run_not_uncertain", "This run does not need cloud reconciliation"
        )
    step = await db.scalar(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.tenant_id == run.tenant_id,
            WorkflowRunStep.run_id == run.id,
            WorkflowRunStep.position == run.next_step,
        )
        .with_for_update()
    )
    if not step or not step.storage_operation_id or step.result_ciphertext:
        raise CapabilityError(
            "cloud_operation_unavailable",
            "Use the existing delivery reconciliation for a submitted communication",
        )
    operation = await db.scalar(
        select(DocumentStorageOperation)
        .where(
            DocumentStorageOperation.tenant_id == run.tenant_id,
            DocumentStorageOperation.id == step.storage_operation_id,
            DocumentStorageOperation.artifact_revision_id == step.artifact_revision_id,
        )
        .with_for_update()
    )
    if not operation or operation.status not in {
        "writing",
        "provider_accepted",
        "ambiguous",
    }:
        raise CapabilityError(
            "cloud_operation_unavailable",
            "The uncertain cloud operation is unavailable",
        )
    if (
        operation.provider_object_id
        and operation.provider_object_id != body.provider_object_id
    ):
        raise CapabilityError(
            "cloud_identity_conflict",
            "The original provider receipt identifies a different object",
        )
    verified = await verify_provider_object(
        tenant_id=run.tenant_id,
        operation=operation,
        provider_object_id=body.provider_object_id,
    )
    operation.provider_object_id = body.provider_object_id
    operation.provider_etag = verified["provider_etag"]
    operation.provider_version_id = (
        str(verified["provider_version_id"])
        if verified["provider_version_id"]
        else None
    )
    operation.status = "provider_accepted"
    operation.delivery_certainty = "provider_accepted"
    operation.error_code = None
    operation.error_message = None
    step.status = "pending"
    step.failure_code = None
    run.status = "queued"
    run.failure_code = None
    await append_event(
        db,
        run,
        "cloud_object_reconciled",
        step=step,
        actor_id=context.actor_user_id,
        metadata={
            "operation_id": str(operation.id),
            "document_sha256": operation.content_sha256,
            "reason": body.reason,
        },
    )
    await queue_run(db, run)
    return await describe_run(db, run)
