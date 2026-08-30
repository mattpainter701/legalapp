"""Mediation Platform — external portal router.

Endpoints used by end clients (firm clients with role="client" logins) and
opposing parties (magic-link, no account). A party uploads/discloses assets and
documents, submits them for their attorney's review, the opposing party reviews
what's been *sent* to them and approves/disputes, and either side exchanges
settlement proposals. Authentication is via ``get_portal_context`` which
accepts either a portal-scoped JWT or a firm client login. The case is scoped
by the token (magic link) or by the ``case_id`` query param (client login).
"""

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import PortalContext, get_portal_context
from app.models.mediation import (
    MediationAsset,
    MediationDocument,
    MediationDocumentRecipient,
    MediationInvite,
    MediationParty,
    MediationProposal,
    MediationProposalRecipient,
)
from app.models.plugin import MediationCase
from app.schemas.mediation import (
    AssetCreate,
    AssetDecision,
    AssetResponse,
    AssetUpdate,
    DocumentResponse,
    PortalAcceptRequest,
    PortalAcceptResponse,
    PortalCaseView,
    ProposalCreate,
    ProposalResponse,
)
from app.services import mediation_service as ms
from app.services.portal_invites import (
    PORTAL_INVITE_UNAVAILABLE_DETAIL,
    PORTAL_INVITE_UNAVAILABLE_STATUS,
    resolve_active_portal_invite,
)
from app.services.portal_token import create_portal_token

settings = get_settings()

router = APIRouter(prefix="/api/portal/mediation", tags=["mediation-portal"])
MEDIATION_PORTAL_COOKIE_NAME = "mediation_portal_token"


def _set_cookie(response: Response, token: str) -> None:
    is_production = settings.BACKEND_URL.startswith("https://")
    response.set_cookie(
        key=MEDIATION_PORTAL_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_production,
        samesite="Lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid UUID") from None


# ── Invite acceptance ──────────────────────────────────────────────────────


@router.post("/accept", response_model=PortalAcceptResponse)
async def accept_invite(
    body: PortalAcceptRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
    invite = await resolve_active_portal_invite(
        db,
        MediationInvite,
        token_hash,
    )
    if (
        invite is None
        or invite.revoked
        or invite.expires_at < datetime.now(timezone.utc)
        or invite.accepted_at is not None
    ):
        raise HTTPException(
            status_code=PORTAL_INVITE_UNAVAILABLE_STATUS,
            detail=PORTAL_INVITE_UNAVAILABLE_DETAIL,
        )

    party = await db.get(MediationParty, invite.party_id)
    if party is None:
        raise HTTPException(status_code=404, detail="Party not found")

    invite.accepted_at = datetime.now(timezone.utc)

    token = create_portal_token(
        tenant_id=str(invite.tenant_id),
        case_id=str(invite.case_id),
        party_id=str(party.id),
        party_role=party.role,
        invite_id=str(invite.id),
    )

    await db.commit()
    _set_cookie(response, token)
    return PortalAcceptResponse(
        case_id=str(invite.case_id),
        party_id=str(party.id),
        party_role=party.role,
        kind=invite.kind,
    )


# ── Context resolution ─────────────────────────────────────────────────────


async def _resolve(
    request: Request, db: AsyncSession, case_id: str | None
) -> PortalContext:
    ctx = await get_portal_context(request, db, case_id=case_id)
    return ctx


async def _load_case(db: AsyncSession, ctx: PortalContext) -> MediationCase:
    result = await db.execute(
        select(MediationCase)
        .options(
            selectinload(MediationCase.case_parties),
            selectinload(MediationCase.assets),
        )
        .where(
            MediationCase.id == ctx.case_id,
            MediationCase.tenant_id == ctx.tenant_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _can_see_asset(asset: MediationAsset, ctx: PortalContext) -> bool:
    if str(asset.submitted_by_party_id) == str(ctx.party_id):
        return True
    return asset.status in ms.SHARED_ASSET_STATUSES


def _document_visible_to_party(ctx: PortalContext):
    party_id = uuid.UUID(str(ctx.party_id))
    return or_(
        MediationDocument.uploaded_by_party_id == party_id,
        MediationDocument.recipients.any(
            MediationDocumentRecipient.party_id == party_id
        ),
    )


def _proposal_visible_to_party(ctx: PortalContext):
    party_id = uuid.UUID(str(ctx.party_id))
    return or_(
        MediationProposal.proposed_by_party_id == party_id,
        MediationProposal.recipients.any(
            MediationProposalRecipient.party_id == party_id
        ),
    )


def _portal_document_response(
    document: MediationDocument, ctx: PortalContext
) -> DocumentResponse:
    """Return release metadata without exposing other parties' grant IDs."""
    response = ms.document_to_response(document)
    recipient = next(
        (row for row in document.recipients if str(row.party_id) == str(ctx.party_id)),
        None,
    )
    if str(document.uploaded_by_party_id) != str(ctx.party_id):
        response.uploaded_by_party_id = None
    response.uploaded_by_user_id = None
    response.recipient_party_ids = [str(ctx.party_id)] if recipient else []
    if recipient is not None:
        response.released_at = recipient.released_at
    return response


def _portal_proposal_response(
    proposal: MediationProposal,
    ctx: PortalContext,
    proposed_by_name: str | None = None,
) -> ProposalResponse:
    """Return only the current party's grant in a portal response."""
    response = ms.proposal_to_response(proposal, proposed_by_name)
    recipient = next(
        (row for row in proposal.recipients if str(row.party_id) == str(ctx.party_id)),
        None,
    )
    is_own = str(proposal.proposed_by_party_id) == str(ctx.party_id)
    if not is_own:
        response.proposed_by_party_id = None
        response.review_notes = None
    response.created_by_user_id = None
    response.reviewed_by_user_id = None
    response.released_by_user_id = None
    response.recipient_party_ids = [str(ctx.party_id)] if recipient else []
    if recipient is not None:
        response.released_at = recipient.released_at
    return response


# ── Case view ──────────────────────────────────────────────────────────────


@router.get("/case", response_model=PortalCaseView)
async def portal_case(
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    case = await _load_case(db, ctx)

    assets_result = await db.execute(
        select(MediationAsset).where(
            MediationAsset.case_id == ctx.case_id,
            MediationAsset.tenant_id == ctx.tenant_id,
        )
    )
    assets = assets_result.scalars().all()
    my_assets = [
        ms.asset_to_response(a)
        for a in assets
        if str(a.submitted_by_party_id) == str(ctx.party_id)
    ]
    shared_assets = [
        ms.asset_to_response(a)
        for a in assets
        if str(a.submitted_by_party_id) != str(ctx.party_id)
        and a.status in ms.SHARED_ASSET_STATUSES
    ]

    docs_result = await db.execute(
        select(MediationDocument)
        .options(selectinload(MediationDocument.recipients))
        .where(
            MediationDocument.case_id == ctx.case_id,
            MediationDocument.tenant_id == ctx.tenant_id,
            _document_visible_to_party(ctx),
        )
        .order_by(MediationDocument.created_at.desc())
    )
    documents = [
        _portal_document_response(document, ctx)
        for document in docs_result.scalars().all()
    ]

    proposals = await _list_proposals(db, ctx)

    return PortalCaseView(
        case=ms.case_to_response(case),
        party_role=ctx.party_role,
        party_id=str(ctx.party_id),
        my_assets=my_assets,
        shared_assets=shared_assets,
        documents=documents,
        proposals=proposals,
    )


# ── Assets ─────────────────────────────────────────────────────────────────


async def _get_asset(
    db: AsyncSession,
    ctx: PortalContext,
    asset_id: str,
    *,
    for_update: bool = False,
) -> MediationAsset:
    statement = select(MediationAsset).where(
        MediationAsset.id == asset_id,
        MediationAsset.case_id == ctx.case_id,
        MediationAsset.tenant_id == ctx.tenant_id,
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.get("/assets", response_model=List[AssetResponse])
async def portal_list_assets(
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    result = await db.execute(
        select(MediationAsset)
        .where(
            MediationAsset.case_id == ctx.case_id,
            MediationAsset.tenant_id == ctx.tenant_id,
        )
        .order_by(MediationAsset.created_at)
    )
    return [
        ms.asset_to_response(a)
        for a in result.scalars().all()
        if _can_see_asset(a, ctx)
    ]


@router.post("/assets", response_model=AssetResponse, status_code=201)
async def portal_create_asset(
    body: AssetCreate,
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    asset = MediationAsset(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(ctx.tenant_id),
        case_id=uuid.UUID(ctx.case_id),
        kind=body.kind,
        category=body.category,
        description=body.description,
        value=body.value,
        owned_by=body.owned_by,
        claimed_by=body.claimed_by,
        notes=body.notes,
        status="draft",
        submitted_by_party_id=uuid.UUID(ctx.party_id),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


@router.patch("/assets/{asset_id}", response_model=AssetResponse)
async def portal_update_asset(
    asset_id: str,
    body: AssetUpdate,
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    asset = await _get_asset(db, ctx, asset_id, for_update=True)
    if str(asset.submitted_by_party_id) != str(ctx.party_id):
        raise HTTPException(status_code=403, detail="Not your asset")
    if asset.status not in ("draft", "submitted"):
        raise HTTPException(status_code=409, detail="Asset can no longer be edited")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    asset.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


@router.post("/assets/{asset_id}/submit", response_model=AssetResponse)
async def portal_submit_asset(
    asset_id: str,
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    asset = await _get_asset(db, ctx, asset_id, for_update=True)
    if str(asset.submitted_by_party_id) != str(ctx.party_id):
        raise HTTPException(status_code=403, detail="Not your asset")
    if asset.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Cannot submit from status '{asset.status}'"
        )
    asset.status = "submitted"
    asset.submitted_at = datetime.now(timezone.utc)
    asset.updated_at = asset.submitted_at
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


@router.post("/assets/{asset_id}/decision", response_model=AssetResponse)
async def portal_decide_asset(
    asset_id: str,
    body: AssetDecision,
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    if ctx.party_role != "opposing_party":
        raise HTTPException(
            status_code=403, detail="Only the opposing party can decide on sent items"
        )
    asset = await _get_asset(db, ctx, asset_id, for_update=True)
    if asset.status != "sent":
        raise HTTPException(
            status_code=409, detail="Asset is not awaiting your decision"
        )
    if body.decision not in ("approved", "disputed"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    asset.status = "opposing_approved" if body.decision == "approved" else "disputed"
    asset.opposing_decision = body.decision
    asset.opposing_decided_at = datetime.now(timezone.utc)
    asset.dispute_reason = body.dispute_reason if body.decision == "disputed" else None
    asset.updated_at = asset.opposing_decided_at
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


# ── Documents ──────────────────────────────────────────────────────────────


@router.get("/documents", response_model=List[DocumentResponse])
async def portal_list_documents(
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    result = await db.execute(
        select(MediationDocument)
        .options(selectinload(MediationDocument.recipients))
        .where(
            MediationDocument.case_id == ctx.case_id,
            MediationDocument.tenant_id == ctx.tenant_id,
            _document_visible_to_party(ctx),
        )
        .order_by(MediationDocument.created_at.desc())
    )
    return [
        _portal_document_response(document, ctx) for document in result.scalars().all()
    ]


@router.post("/documents/upload", response_model=DocumentResponse, status_code=201)
async def portal_upload_document(
    request: Request,
    case_id: str | None = None,
    file: UploadFile = File(...),
    description: str | None = Form(None),
    asset_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    doc_id = uuid.uuid4()
    asset_uuid = None
    if asset_id:
        asset = await _get_asset(db, ctx, asset_id)
        if str(asset.submitted_by_party_id) != str(ctx.party_id):
            raise HTTPException(
                status_code=403,
                detail="Documents may only be attached to your own asset",
            )
        asset_uuid = asset.id

    storage_path, size, content_sha256 = await ms.save_case_upload(
        file, uuid.UUID(ctx.tenant_id), ctx.case_id, doc_id
    )
    doc = MediationDocument(
        id=doc_id,
        tenant_id=uuid.UUID(ctx.tenant_id),
        case_id=uuid.UUID(ctx.case_id),
        asset_id=asset_uuid,
        uploaded_by_party_id=uuid.UUID(ctx.party_id),
        filename=os.path.basename(file.filename),
        content_type=file.content_type,
        file_size=size,
        storage_path=storage_path,
        content_sha256=content_sha256,
        description=description,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _portal_document_response(doc, ctx)


@router.get("/documents/{doc_id}/download")
async def portal_download_document(
    doc_id: str,
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    result = await db.execute(
        select(MediationDocument)
        .options(selectinload(MediationDocument.recipients))
        .where(
            MediationDocument.id == doc_id,
            MediationDocument.case_id == ctx.case_id,
            MediationDocument.tenant_id == ctx.tenant_id,
            _document_visible_to_party(ctx),
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="File not found")
    response = await ms.case_document_download_response(doc)
    recipient = next(
        (row for row in doc.recipients if str(row.party_id) == str(ctx.party_id)),
        None,
    )
    if recipient is not None and recipient.first_downloaded_at is None:
        recipient.first_downloaded_at = datetime.now(timezone.utc)
        await db.commit()
    return response


# ── Proposals ──────────────────────────────────────────────────────────────


async def _list_proposals(
    db: AsyncSession, ctx: PortalContext
) -> List[ProposalResponse]:
    result = await db.execute(
        select(MediationProposal)
        .options(selectinload(MediationProposal.recipients))
        .where(
            MediationProposal.case_id == ctx.case_id,
            MediationProposal.tenant_id == ctx.tenant_id,
            _proposal_visible_to_party(ctx),
        )
        .order_by(MediationProposal.created_at)
    )
    proposals = result.scalars().all()
    names_result = await db.execute(
        select(MediationParty.id, MediationParty.name).where(
            MediationParty.case_id == ctx.case_id,
            MediationParty.tenant_id == ctx.tenant_id,
        )
    )
    names = {str(pid): name for pid, name in names_result.all()}
    return [
        _portal_proposal_response(
            p,
            ctx,
            names.get(str(p.proposed_by_party_id)),
        )
        for p in proposals
    ]


@router.get("/proposals", response_model=List[ProposalResponse])
async def portal_list_proposals(
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    return await _list_proposals(db, ctx)


@router.post("/proposals", response_model=ProposalResponse, status_code=201)
async def portal_create_proposal(
    body: ProposalCreate,
    request: Request,
    case_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    ctx = await _resolve(request, db, case_id)
    parent_id = _as_uuid(body.parent_proposal_id)
    if parent_id:
        parent_result = await db.execute(
            select(MediationProposal)
            .options(selectinload(MediationProposal.recipients))
            .where(
                MediationProposal.id == parent_id,
                MediationProposal.case_id == ctx.case_id,
                MediationProposal.tenant_id == ctx.tenant_id,
            )
            .with_for_update()
        )
        parent = parent_result.scalar_one_or_none()
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent proposal not found")
        if parent.status != "open":
            raise HTTPException(
                status_code=409,
                detail="Only an active proposal can receive a counterproposal",
            )
        if not any(
            str(recipient.party_id) == str(ctx.party_id)
            for recipient in parent.recipients
        ):
            raise HTTPException(
                status_code=403,
                detail="You may only counter a proposal released to you",
            )

    proposal = MediationProposal(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(ctx.tenant_id),
        case_id=uuid.UUID(ctx.case_id),
        proposed_by_party_id=uuid.UUID(ctx.party_id),
        parent_proposal_id=parent_id,
        title=body.title,
        body=body.body,
        status="open",
        review_state="pending",
        content_sha256=ms.proposal_content_sha256(
            title=body.title,
            body=body.body,
            parent_proposal_id=parent_id,
        ),
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return _portal_proposal_response(proposal, ctx, ctx.party_name)
