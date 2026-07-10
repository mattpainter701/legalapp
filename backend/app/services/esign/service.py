"""Orchestration for the internal e-signature flow.

``record_portal_signature`` captures one signer's typed signature; once every
signer has signed, ``complete_request_if_done`` generates an executed-copy /
audit certificate, stores it as a portal-visible MatterDocument, writes a matter
timeline event, and marks the request completed.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.models.signature import SignatureRequest, SignatureSigner
from app.services.esign.certificate import (
    build_certificate,
    immutable_certificate_filename,
)
from app.services.matter_file_store import MatterFileStore

_file_store = MatterFileStore()


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def request_is_expired(
    request: SignatureRequest,
    *,
    now: datetime | None = None,
) -> bool:
    if not request.expires_at or request.status not in ("sent", "partially_signed"):
        return False
    now = now or datetime.now(timezone.utc)
    return _as_aware_utc(request.expires_at) <= _as_aware_utc(now)


def mark_request_expired_if_needed(
    request: SignatureRequest,
    *,
    now: datetime | None = None,
) -> bool:
    if not request_is_expired(request, now=now):
        return False
    request.status = "expired"
    return True


def next_pending_signers(request: SignatureRequest) -> list[SignatureSigner]:
    pending = [s for s in request.signers if s.status == "pending"]
    if not pending:
        return []
    min_order = min(s.sign_order for s in pending)
    return [s for s in pending if s.sign_order == min_order]


def signer_can_act_now(request: SignatureRequest, signer: SignatureSigner) -> bool:
    if signer.status != "pending":
        return False
    if not request.enforce_signing_order:
        return True
    return signer in next_pending_signers(request)


async def record_portal_signature(
    signer: SignatureSigner,
    *,
    typed_signature: str,
    ip: str | None,
    consent_text_version: str,
    user_agent: str | None = None,
) -> None:
    """Mark a single signer as signed (does not commit)."""
    now = datetime.now(timezone.utc)
    signer.status = "signed"
    signer.signed_at = now
    signer.signed_ip = ip
    signer.typed_signature = typed_signature
    signer.audit = {
        "signed_at": now.isoformat(),
        "ip": ip,
        "typed_signature": typed_signature,
        "method": "portal_typed",
        "consent_to_electronic_signature": True,
        "consent_text_version": consent_text_version,
        "user_agent": user_agent,
    }


async def record_portal_decline(
    request: SignatureRequest,
    signer: SignatureSigner,
    *,
    reason: str | None,
    ip: str | None,
) -> None:
    """Mark one signer as declined and close the request (does not commit)."""
    now = datetime.now(timezone.utc)
    clean_reason = reason.strip() if reason and reason.strip() else None
    signer.status = "declined"
    signer.declined_at = now
    signer.decline_reason = clean_reason
    signer.audit = {
        **(signer.audit or {}),
        "declined_at": now.isoformat(),
        "ip": ip,
        "reason": clean_reason,
        "method": "portal_decline",
    }
    request.status = "declined"
    request.declined_at = now
    request.decline_reason = clean_reason


async def complete_request_if_done(
    db: AsyncSession,
    request: SignatureRequest,
    matter: Matter,
) -> MatterDocument | None:
    """If all signers have signed, finalize the request. Returns the executed
    MatterDocument when completion happens, else None. Caller commits."""
    if request.status == "completed":
        if not request.provider_envelope_id:
            raise RuntimeError(
                "Completed signature request is missing its evidence artifact ID"
            )
        try:
            artifact_id = uuid.UUID(str(request.provider_envelope_id))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Completed signature request has an invalid evidence artifact ID"
            ) from exc
        existing = await db.get(MatterDocument, artifact_id)
        if (
            existing is None
            or str(existing.tenant_id) != str(request.tenant_id)
            or str(existing.matter_id) != str(request.matter_id)
        ):
            raise RuntimeError(
                "Completed signature request evidence artifact is unavailable"
            )
        return existing

    signers = list(request.signers)
    if not signers or any(s.status != "signed" for s in signers):
        request.status = "partially_signed"
        return None

    document_name = None
    if request.document_id:
        src = await db.get(MatterDocument, request.document_id)
        if src is not None:
            document_name = src.filename

    evidence_payload = {
        "request_id": str(request.id),
        "source_document_sha256": request.source_document_sha256,
        "signers": [s.audit for s in sorted(signers, key=lambda row: row.sign_order)],
    }
    evidence_sha256 = hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    request.evidence_sha256 = evidence_sha256
    content, _suggested_filename, content_type = build_certificate(
        matter_name=matter.matter_name,
        document_name=document_name or "document",
        signers=signers,
        request_id=str(request.id),
        source_sha256=request.source_document_sha256,
        evidence_sha256=evidence_sha256,
    )
    request.completion_artifact_sha256 = hashlib.sha256(content).hexdigest()
    filename = immutable_certificate_filename(
        document_name=document_name or "document",
        request_id=str(request.id),
        artifact_sha256=request.completion_artifact_sha256,
        content_type=content_type,
    )
    storage_result = await _file_store.store_matter_file_result(
        db=db,
        tenant_id=str(matter.tenant_id),
        matter_slug=matter.slug,
        category="signed",
        filename=filename,
        content=content,
        content_type=content_type,
        matter_cloud_folder=matter.cloud_folder,
    )
    signed_doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        uploaded_by_user_id=None,
        filename=filename,
        content_type=content_type,
        file_size=len(content),
        storage_path=storage_result.storage_path,
        storage_provider=storage_result.provider,
        storage_backend=storage_result.backend,
        provider_object_id=storage_result.provider_item_id,
        provider_drive_id=storage_result.drive_id,
        provider_parent_id=storage_result.parent_id,
        storage_error=storage_result.error,
        description=(
            "Signature acknowledgment certificate; cryptographically bound to the "
            "source document, not a signed or modified source document"
        ),
        document_category="signed",
        portal_visible=True,
    )
    db.add(signed_doc)

    db.add(
        MatterEvent(
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            event_type="signature",
            title=f"Signature acknowledgments completed: {document_name or filename}",
            content=(
                "All parties completed typed signature acknowledgments. "
                f"Evidence certificate stored: {filename}; source SHA-256 "
                f"{request.source_document_sha256}."
            ),
            note_type="system",
            created_by=None,
        )
    )

    now = datetime.now(timezone.utc)
    request.status = "completed"
    request.completed_at = now
    # For the internal provider this points to the evidence certificate, not an
    # externally executed or cryptographically signed source document.
    request.provider_envelope_id = str(signed_doc.id)
    return signed_doc
