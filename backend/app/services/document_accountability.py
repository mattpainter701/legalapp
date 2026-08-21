"""Metadata-only accountability helpers for tenant cloud document workflows."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from app.models.document_integrity_event import DocumentIntegrityEvent
from app.models.document_storage_operation import DocumentStorageOperation

_MAX_EVENT_METADATA_BYTES = 12_000
_MAX_EVENT_COLLECTION_ITEMS = 50
_MAX_EVENT_STRING_CHARS = 1_000
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "secret",
    "storage_path",
    "token",
    "url",
)


class DocumentAccountabilityError(RuntimeError):
    """Accountability evidence could not be recorded safely."""


def _safe_metadata_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise DocumentAccountabilityError(
            "Integrity event metadata is too deeply nested"
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_EVENT_STRING_CHARS]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        if len(value) > _MAX_EVENT_COLLECTION_ITEMS:
            raise DocumentAccountabilityError(
                "Integrity event metadata has too many keys"
            )
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key).strip()[:100]
            if not key:
                raise DocumentAccountabilityError(
                    "Integrity event metadata has an empty key"
                )
            lowered = key.casefold()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                raise DocumentAccountabilityError(
                    f"Integrity event metadata key {key!r} is not permitted"
                )
            result[key] = _safe_metadata_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_EVENT_COLLECTION_ITEMS:
            raise DocumentAccountabilityError(
                "Integrity event metadata has too many values"
            )
        return [_safe_metadata_value(item, depth=depth + 1) for item in value]
    return str(value)[:_MAX_EVENT_STRING_CHARS]


def bounded_integrity_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Canonicalize bounded audit metadata and reject secret/link-shaped fields."""
    normalized = _safe_metadata_value(metadata or {})
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_EVENT_METADATA_BYTES:
        raise DocumentAccountabilityError("Integrity event metadata is too large")
    return normalized


def integrity_event_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def append_document_integrity_event(
    db: Any,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    actor_type: str,
    actor_user_id: uuid.UUID | None = None,
    matter_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    artifact_id: uuid.UUID | None = None,
    artifact_revision_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
    content_sha256: str | None = None,
    provider_object_id: str | None = None,
    provider_etag: str | None = None,
    provider_version_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DocumentIntegrityEvent:
    """Append one event under a tenant advisory lock to keep a linear hash chain."""
    clean_type = str(event_type or "").strip()[:50]
    if not clean_type:
        raise DocumentAccountabilityError("Integrity event type is required")
    if actor_type not in {"user", "service", "provider", "system"}:
        raise DocumentAccountabilityError("Integrity event actor type is invalid")
    clean_metadata = bounded_integrity_metadata(metadata)

    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock(" "hashtextextended(:integrity_scope, 0)" ")"
        ),
        {"integrity_scope": f"lawhand-document-integrity:{tenant_id}"},
    )
    previous_event = await db.scalar(
        select(DocumentIntegrityEvent)
        .where(DocumentIntegrityEvent.tenant_id == tenant_id)
        .order_by(DocumentIntegrityEvent.chain_position.desc())
        .limit(1)
    )
    previous_hash = previous_event.event_hash if previous_event is not None else None
    chain_position = (
        previous_event.chain_position + 1 if previous_event is not None else 1
    )
    event_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    payload = {
        "event_id": str(event_id),
        "tenant_id": str(tenant_id),
        "matter_id": str(matter_id) if matter_id else None,
        "task_id": str(task_id) if task_id else None,
        "artifact_id": str(artifact_id) if artifact_id else None,
        "artifact_revision_id": (
            str(artifact_revision_id) if artifact_revision_id else None
        ),
        "document_id": str(document_id) if document_id else None,
        "operation_id": str(operation_id) if operation_id else None,
        "event_type": clean_type,
        "actor_type": actor_type,
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "content_sha256": content_sha256,
        "provider_object_id": provider_object_id,
        "provider_etag": provider_etag,
        "provider_version_id": provider_version_id,
        "metadata": clean_metadata,
        "chain_position": chain_position,
        "prev_event_hash": previous_hash,
        "created_at": created_at.isoformat(),
    }
    event = DocumentIntegrityEvent(
        id=event_id,
        tenant_id=tenant_id,
        matter_id=matter_id,
        task_id=task_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
        document_id=document_id,
        operation_id=operation_id,
        event_type=clean_type,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        content_sha256=content_sha256,
        provider_object_id=provider_object_id,
        provider_etag=provider_etag,
        provider_version_id=provider_version_id,
        metadata_json=clean_metadata,
        chain_position=chain_position,
        prev_event_hash=previous_hash,
        event_hash=integrity_event_sha256(payload),
        created_at=created_at,
    )
    db.add(event)
    await db.flush()
    return event


async def ensure_document_storage_operation(
    db: Any,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    artifact_id: uuid.UUID,
    artifact_revision_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    content_sha256: str,
    content_size: int,
    target_provider: str,
    target_backend: str,
    target_drive_id: str | None,
    target_parent_id: str,
) -> DocumentStorageOperation:
    """Lock or create the idempotent storage operation for one artifact revision."""
    idempotency_key = f"generated-artifact:{artifact_revision_id}:cloud-create"
    operation = await db.scalar(
        select(DocumentStorageOperation)
        .where(
            DocumentStorageOperation.tenant_id == tenant_id,
            DocumentStorageOperation.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if operation is None:
        operation = DocumentStorageOperation(
            tenant_id=tenant_id,
            matter_id=matter_id,
            task_id=task_id,
            artifact_id=artifact_id,
            artifact_revision_id=artifact_revision_id,
            idempotency_key=idempotency_key,
            operation_type="create",
            status="planned",
            actor_user_id=actor_user_id,
            content_sha256=content_sha256,
            content_size=content_size,
            target_provider=target_provider,
            target_backend=target_backend,
            target_drive_id=target_drive_id,
            target_parent_id=target_parent_id,
        )
        db.add(operation)
        await db.flush()
        return operation

    expected = (
        operation.matter_id == matter_id
        and operation.task_id == task_id
        and operation.artifact_id == artifact_id
        and operation.artifact_revision_id == artifact_revision_id
        and operation.content_sha256 == content_sha256
        and operation.content_size == content_size
        and operation.target_backend == target_backend
        and operation.target_drive_id == target_drive_id
        and operation.target_parent_id == target_parent_id
    )
    if not expected:
        raise DocumentAccountabilityError(
            "Existing cloud operation does not match this artifact revision"
        )
    return operation
