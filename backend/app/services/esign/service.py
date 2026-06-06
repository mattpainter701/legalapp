"""Orchestration for the internal e-signature flow.

``record_portal_signature`` captures one signer's typed signature; once every
signer has signed, ``complete_request_if_done`` generates an executed-copy /
audit certificate, stores it as a portal-visible MatterDocument, writes a matter
timeline event, and marks the request completed.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.models.signature import SignatureRequest, SignatureSigner
from app.services.esign.certificate import build_certificate
from app.services.matter_file_store import MatterFileStore

_file_store = MatterFileStore()


async def record_portal_signature(
    signer: SignatureSigner,
    *,
    typed_signature: str,
    ip: str | None,
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
    }


async def complete_request_if_done(
    db: AsyncSession,
    request: SignatureRequest,
    matter: Matter,
) -> MatterDocument | None:
    """If all signers have signed, finalize the request. Returns the executed
    MatterDocument when completion happens, else None. Caller commits."""
    signers = list(request.signers)
    if not signers or any(s.status != "signed" for s in signers):
        request.status = "partially_signed"
        return None

    document_name = None
    if request.document_id:
        src = await db.get(MatterDocument, request.document_id)
        if src is not None:
            document_name = src.filename

    content, filename, content_type = build_certificate(
        matter_name=matter.matter_name,
        document_name=document_name or "document",
        signers=signers,
    )
    storage_path = await _file_store.store_matter_file(
        db=db,
        tenant_id=str(matter.tenant_id),
        matter_slug=matter.slug,
        category="signed",
        filename=filename,
        content=content,
        content_type=content_type,
    )
    signed_doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        uploaded_by_user_id=None,
        filename=filename,
        content_type=content_type,
        file_size=len(content),
        storage_path=storage_path,
        description="Executed copy / certificate of completion",
        document_category="signed",
        portal_visible=True,
    )
    db.add(signed_doc)

    db.add(
        MatterEvent(
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            event_type="signature",
            title=f"Document signed: {document_name or filename}",
            content=(
                "All parties completed e-signature. Executed copy stored: "
                f"{filename}."
            ),
            note_type="system",
            created_by=None,
        )
    )

    now = datetime.now(timezone.utc)
    request.status = "completed"
    request.completed_at = now
    # For the internal provider, the "envelope id" is the executed document id.
    request.provider_envelope_id = str(signed_doc.id)
    return signed_doc
