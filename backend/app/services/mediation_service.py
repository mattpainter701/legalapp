"""Shared helpers for the Mediation Platform (firm router + portal router).

Response builders, token hashing, and file-storage utilities live here so the
internal (``/api/plugins/mediation``) and external portal
(``/api/portal/mediation``) routers stay DRY.
"""

import hashlib
import hmac
import os
import uuid
from urllib.parse import quote

import aiofiles
from fastapi import HTTPException, Response, UploadFile

from app.config import get_settings
from app.models.mediation import (
    MediationAsset,
    MediationDocument,
    MediationParty,
    MediationProposal,
)
from app.models.plugin import MediationCase, MediationCaseEvent
from app.models.task import Task
from app.schemas.mediation import (
    AssetResponse,
    DocumentResponse,
    MediationCaseResponse,
    PartyResponse,
    ProposalResponse,
    SessionResponse,
)

settings = get_settings()

# Asset statuses visible to the opposing party (never another party's drafts).
SHARED_ASSET_STATUSES = ("sent", "opposing_approved", "disputed")


def hash_token(raw: str) -> str:
    """sha256 hex digest of a raw invite token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def case_to_response(
    case: MediationCase, next_task: Task | None = None
) -> MediationCaseResponse:
    return MediationCaseResponse(
        id=str(case.id),
        case_name=case.case_name or case.title,
        title=case.title,
        party_a=case.party_a,
        party_b=case.party_b,
        dispute_type=case.dispute_type,
        mediation_stage=case.mediation_stage,
        mediator=case.mediator,
        attorney=case.attorney,
        claim_value=case.claim_value,
        jurisdiction=case.jurisdiction,
        court=case.court,
        case_number=case.case_number,
        waiting_on=case.waiting_on,
        fixed_fee=case.fixed_fee,
        next_action=next_task.title if next_task else None,
        next_action_due=next_task.due_date if next_task else None,
        next_action_priority=next_task.priority if next_task else None,
        scheduled_session=case.scheduled_session,
        confidentiality_signed=bool(case.confidentiality_signed),
        status=case.status,
        summary=case.summary,
        matter_id=str(case.matter_id) if case.matter_id else None,
        client_contact_id=(
            str(case.client_contact_id) if case.client_contact_id else None
        ),
        parties_count=len(case.case_parties or []),
        assets_count=len(case.assets or []),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def session_to_response(ev: MediationCaseEvent) -> SessionResponse:
    return SessionResponse(
        id=str(ev.id),
        session_type=ev.session_type or ev.event_type,
        event_type=ev.event_type,
        title=ev.title,
        content=ev.content,
        added_by=ev.added_by,
        created_at=ev.created_at,
    )


def party_to_response(p: MediationParty, invited: bool = False) -> PartyResponse:
    return PartyResponse(
        id=str(p.id),
        case_id=str(p.case_id),
        name=p.name,
        role=p.role,
        email=p.email,
        contact_id=str(p.contact_id) if p.contact_id else None,
        user_id=str(p.user_id) if p.user_id else None,
        is_initiator=bool(p.is_initiator),
        has_account=p.user_id is not None,
        invited=invited,
        created_at=p.created_at,
    )


def asset_to_response(a: MediationAsset) -> AssetResponse:
    return AssetResponse(
        id=str(a.id),
        case_id=str(a.case_id),
        kind=a.kind,
        category=a.category,
        description=a.description,
        value=a.value,
        owned_by=a.owned_by,
        claimed_by=a.claimed_by,
        status=a.status,
        submitted_by_party_id=(
            str(a.submitted_by_party_id) if a.submitted_by_party_id else None
        ),
        submitted_at=a.submitted_at,
        attorney_approved_at=a.attorney_approved_at,
        sent_at=a.sent_at,
        opposing_decision=a.opposing_decision,
        opposing_decided_at=a.opposing_decided_at,
        dispute_reason=a.dispute_reason,
        notes=a.notes,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def document_to_response(d: MediationDocument) -> DocumentResponse:
    recipients = list(d.recipients or [])
    return DocumentResponse(
        id=str(d.id),
        case_id=str(d.case_id),
        asset_id=str(d.asset_id) if d.asset_id else None,
        filename=d.filename,
        content_type=d.content_type,
        file_size=d.file_size,
        content_sha256=d.content_sha256,
        description=d.description,
        uploaded_by_party_id=(
            str(d.uploaded_by_party_id) if d.uploaded_by_party_id else None
        ),
        uploaded_by_user_id=(
            str(d.uploaded_by_user_id) if d.uploaded_by_user_id else None
        ),
        is_released=bool(recipients),
        recipient_party_ids=[str(r.party_id) for r in recipients],
        released_at=(min(r.released_at for r in recipients) if recipients else None),
        created_at=d.created_at,
    )


def proposal_to_response(
    p: MediationProposal, proposed_by_name: str | None = None
) -> ProposalResponse:
    recipients = list(p.recipients or [])
    return ProposalResponse(
        id=str(p.id),
        case_id=str(p.case_id),
        proposed_by_party_id=(
            str(p.proposed_by_party_id) if p.proposed_by_party_id else None
        ),
        proposed_by_name=proposed_by_name,
        parent_proposal_id=(
            str(p.parent_proposal_id) if p.parent_proposal_id else None
        ),
        title=p.title,
        body=p.body,
        status=p.status,
        review_state=p.review_state,
        review_notes=p.review_notes,
        reviewed_by_user_id=(
            str(p.reviewed_by_user_id) if p.reviewed_by_user_id else None
        ),
        reviewed_at=p.reviewed_at,
        released_by_user_id=(
            str(p.released_by_user_id) if p.released_by_user_id else None
        ),
        released_at=p.released_at,
        created_by_user_id=(
            str(p.created_by_user_id) if p.created_by_user_id else None
        ),
        content_sha256=p.content_sha256,
        is_released=bool(recipients),
        recipient_party_ids=[str(r.party_id) for r in recipients],
        created_at=p.created_at,
    )


def proposal_content_sha256(
    *, title: str, body: str | None, parent_proposal_id: uuid.UUID | None
) -> str:
    """Bind review/release evidence to the exact proposal text and lineage."""
    payload = "\n".join(
        (
            title.strip(),
            (body or "").strip(),
            str(parent_proposal_id or ""),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_proposal_integrity(proposal: MediationProposal) -> None:
    """Fail closed if reviewed proposal content no longer matches its digest."""
    if not proposal.content_sha256:
        return
    digest = proposal_content_sha256(
        title=proposal.title,
        body=proposal.body,
        parent_proposal_id=proposal.parent_proposal_id,
    )
    if not hmac.compare_digest(digest, proposal.content_sha256):
        raise HTTPException(
            status_code=409,
            detail="The mediation proposal failed its integrity check",
        )


async def save_case_upload(
    file: UploadFile, tenant_id: uuid.UUID, case_id: str, doc_id: uuid.UUID
) -> tuple[str, int, str]:
    """Persist an upload and return its path, size, and immutable digest."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    file_bytes = await file.read()
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    storage_dir = os.path.join(
        settings.UPLOAD_DIR,
        str(tenant_id),
        "mediation",
        case_id,
        str(doc_id),
    )
    os.makedirs(storage_dir, exist_ok=True)
    safe_filename = os.path.basename(file.filename)
    storage_path = os.path.join(storage_dir, safe_filename)

    async with aiofiles.open(storage_path, "wb") as out_file:
        await out_file.write(file_bytes)

    return storage_path, len(file_bytes), hashlib.sha256(file_bytes).hexdigest()


async def case_document_download_response(
    document: MediationDocument,
) -> Response:
    """Read once, verify the recorded digest, and return an attachment."""
    if not document.storage_path or not os.path.exists(document.storage_path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        async with aiofiles.open(document.storage_path, "rb") as source:
            content = await source.read()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    digest = hashlib.sha256(content).hexdigest()
    if document.content_sha256 and not hmac.compare_digest(
        digest, document.content_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail="The stored mediation document failed its integrity check",
        )

    safe_name = (document.filename or "document").replace("\r", "").replace("\n", "")
    fallback_name = safe_name.encode("ascii", "ignore").decode() or "document"
    fallback_name = fallback_name.replace('"', "'").replace("\\", "_")
    disposition = (
        f'attachment; filename="{fallback_name}"; '
        f"filename*=UTF-8''{quote(safe_name, safe='')}"
    )
    return Response(
        content=content,
        media_type=document.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )
