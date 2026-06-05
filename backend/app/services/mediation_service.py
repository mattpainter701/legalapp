"""Shared helpers for the Mediation Platform (firm router + portal router).

Response builders, token hashing, and file-storage utilities live here so the
internal (``/api/plugins/mediation``) and external portal
(``/api/portal/mediation``) routers stay DRY.
"""

import hashlib
import os
import uuid

import aiofiles
from fastapi import HTTPException, UploadFile

from app.config import get_settings
from app.models.mediation import (
    MediationAsset,
    MediationDocument,
    MediationParty,
    MediationProposal,
)
from app.models.plugin import MediationCase, MediationCaseEvent
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


def case_to_response(case: MediationCase) -> MediationCaseResponse:
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
    return DocumentResponse(
        id=str(d.id),
        case_id=str(d.case_id),
        asset_id=str(d.asset_id) if d.asset_id else None,
        filename=d.filename,
        content_type=d.content_type,
        file_size=d.file_size,
        description=d.description,
        uploaded_by_party_id=(
            str(d.uploaded_by_party_id) if d.uploaded_by_party_id else None
        ),
        uploaded_by_user_id=(
            str(d.uploaded_by_user_id) if d.uploaded_by_user_id else None
        ),
        created_at=d.created_at,
    )


def proposal_to_response(
    p: MediationProposal, proposed_by_name: str | None = None
) -> ProposalResponse:
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
        created_at=p.created_at,
    )


async def save_case_upload(
    file: UploadFile, tenant_id: uuid.UUID, case_id: str, doc_id: uuid.UUID
) -> tuple[str, int]:
    """Persist an uploaded file to disk; returns (storage_path, size). Mirrors
    the matter-documents storage convention."""
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

    return storage_path, len(file_bytes)
