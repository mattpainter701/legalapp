"""Native e-signature router (Epic 2).

Firm side (``router``, ``/api/matters/{matter_id}/signatures``): create/list/
view/send/void signature requests on a matter document.

Client side (``portal_router``, ``/api/portal/client/signatures``): the client
signs in the portal via the matter-scoped portal token.
"""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.signature import SignatureRequest, SignatureSigner
from app.routers.client_portal import ClientPortalContext, get_client_portal_context
from app.schemas.signature import (
    PortalSignRequest,
    SignatureRequestCreate,
    SignatureRequestResponse,
    SignerResponse,
)
from app.services.esign import (
    complete_request_if_done,
    get_provider,
    record_portal_signature,
)

router = APIRouter(prefix="/api/matters", tags=["esignature"])
portal_router = APIRouter(prefix="/api/portal/client", tags=["esignature-portal"])


# ── Serialization ───────────────────────────────────────────────────────────


async def _to_response(
    db: AsyncSession, req: SignatureRequest
) -> SignatureRequestResponse:
    document_name = None
    if req.document_id:
        doc = await db.get(MatterDocument, req.document_id)
        if doc is not None:
            document_name = doc.filename
    signed_document_id = (
        req.provider_envelope_id
        if req.provider == "internal" and req.status == "completed"
        else None
    )
    return SignatureRequestResponse(
        id=str(req.id),
        matter_id=str(req.matter_id),
        document_id=str(req.document_id) if req.document_id else None,
        document_name=document_name,
        status=req.status,
        provider=req.provider,
        sent_at=req.sent_at,
        completed_at=req.completed_at,
        created_at=req.created_at,
        signed_document_id=signed_document_id,
        signers=[
            SignerResponse(
                id=str(s.id),
                name=s.name,
                email=s.email,
                sign_order=s.sign_order,
                status=s.status,
                signed_at=s.signed_at,
            )
            for s in sorted(req.signers, key=lambda s: s.sign_order)
        ],
    )


async def _load_request(
    db: AsyncSession, request_id: str, matter_id: str, tenant_id
) -> SignatureRequest:
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == matter_id,
            SignatureRequest.tenant_id == tenant_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    return req


def _portal_signer_matches_context(
    signer: SignatureSigner, ctx: ClientPortalContext
) -> bool:
    if (
        ctx.contact_id
        and signer.contact_id
        and str(signer.contact_id) == str(ctx.contact_id)
    ):
        return True
    if ctx.email and signer.email:
        return signer.email.strip().lower() == ctx.email.strip().lower()
    return False


# ── Firm side ───────────────────────────────────────────────────────────────


async def _get_matter(db: AsyncSession, matter_id: str, tenant_id) -> Matter:
    result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


@router.post(
    "/{matter_id}/signatures",
    response_model=SignatureRequestResponse,
    status_code=201,
)
async def create_signature_request(
    matter_id: str,
    body: SignatureRequestCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter(db, matter_id, user.tenant_id)

    if not body.signers:
        raise HTTPException(status_code=400, detail="At least one signer is required")

    # Validate the document belongs to this matter.
    doc = await db.get(MatterDocument, uuid.UUID(body.document_id))
    if (
        doc is None
        or str(doc.matter_id) != str(matter_id)
        or str(doc.tenant_id) != str(user.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="Document not found on this matter")

    req = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(matter_id),
        document_id=doc.id,
        status="draft",
        provider=body.provider or "internal",
        created_by_user_id=user.id,
    )
    db.add(req)
    for i, s in enumerate(body.signers):
        db.add(
            SignatureSigner(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                request_id=req.id,
                contact_id=uuid.UUID(s.contact_id) if s.contact_id else None,
                name=s.name,
                email=s.email,
                sign_order=s.sign_order if s.sign_order is not None else i,
                status="pending",
            )
        )
    await db.commit()
    req = await _load_request(db, str(req.id), matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.get("/{matter_id}/signatures", response_model=List[SignatureRequestResponse])
async def list_signature_requests(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.matter_id == matter_id,
            SignatureRequest.tenant_id == user.tenant_id,
        )
        .order_by(SignatureRequest.created_at.desc())
    )
    return [await _to_response(db, r) for r in result.scalars().all()]


@router.get(
    "/{matter_id}/signatures/{request_id}",
    response_model=SignatureRequestResponse,
)
async def get_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.post(
    "/{matter_id}/signatures/{request_id}/send",
    response_model=SignatureRequestResponse,
)
async def send_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    if req.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Cannot send from status '{req.status}'"
        )
    provider = get_provider(req.provider)
    envelope_id = await provider.send(req)
    if envelope_id:
        req.provider_envelope_id = envelope_id
    req.status = "sent"
    req.sent_at = datetime.now(timezone.utc)
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


@router.post(
    "/{matter_id}/signatures/{request_id}/void",
    response_model=SignatureRequestResponse,
)
async def void_signature_request(
    matter_id: str,
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    if req.status in ("completed", "voided"):
        raise HTTPException(
            status_code=409, detail=f"Cannot void from status '{req.status}'"
        )
    req.status = "voided"
    await db.commit()
    req = await _load_request(db, request_id, matter_id, user.tenant_id)
    return await _to_response(db, req)


# ── Client portal side ──────────────────────────────────────────────────────


@portal_router.get("/signatures", response_model=List[SignatureRequestResponse])
async def portal_list_signatures(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.matter_id == ctx.matter_id,
            SignatureRequest.tenant_id == ctx.tenant_id,
            SignatureRequest.status.in_(("sent", "partially_signed")),
        )
        .order_by(SignatureRequest.created_at.desc())
    )
    requests = [
        r
        for r in result.scalars().all()
        if any(
            s.status == "pending" and _portal_signer_matches_context(s, ctx)
            for s in r.signers
        )
    ]
    return [await _to_response(db, r) for r in requests]


@portal_router.post(
    "/signatures/{request_id}/sign", response_model=SignatureRequestResponse
)
async def portal_sign(
    request_id: str,
    body: PortalSignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    if not body.typed_signature or not body.typed_signature.strip():
        raise HTTPException(status_code=400, detail="A typed signature is required")

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.id == request_id,
            SignatureRequest.matter_id == ctx.matter_id,
            SignatureRequest.tenant_id == ctx.tenant_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    if req.status not in ("sent", "partially_signed"):
        raise HTTPException(
            status_code=409, detail="This request is not open for signing"
        )

    # Resolve the signer: explicit signer_id, else the next pending in order.
    signer = None
    if body.signer_id:
        signer = next((s for s in req.signers if str(s.id) == body.signer_id), None)
        if signer is None:
            raise HTTPException(status_code=404, detail="Signer not found")
        if not _portal_signer_matches_context(signer, ctx):
            raise HTTPException(
                status_code=403, detail="Signer does not match portal session"
            )
        if signer.status != "pending":
            raise HTTPException(status_code=409, detail="Signer already actioned")
    else:
        pending = sorted(
            (
                s
                for s in req.signers
                if s.status == "pending" and _portal_signer_matches_context(s, ctx)
            ),
            key=lambda s: s.sign_order,
        )
        if not pending:
            raise HTTPException(
                status_code=403, detail="No pending signer for this portal session"
            )
        signer = pending[0]

    ip = request.client.host if request.client else None
    await record_portal_signature(
        signer, typed_signature=body.typed_signature.strip(), ip=ip
    )

    matter = await db.get(Matter, req.matter_id)
    await complete_request_if_done(db, req, matter)
    await db.commit()

    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
    )
    req = result.scalar_one()
    return await _to_response(db, req)
