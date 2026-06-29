"""Client Portal — matter-scoped secure access for firm clients.

Generalizes the mediation portal pattern (``mediation_portal.py``) to firm
matters. A client accepts a tokenized invite, receives a short-lived
``client_portal`` JWT cookie scoped to one matter, and can then: view matter
status + key dates, exchange secure messages with the legal team, view/upload
documents the firm has shared, and view invoices (with pay links).

Two routers are exported:
  - ``router`` (``/api/portal/client``): unauthenticated-by-default client surface
    guarded by the portal token.
  - ``firm_router`` (``/api/matters``): firm-side invite management (firm login).
"""

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
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
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.billing import Invoice
from app.models.client_portal import ClientPortalInvite
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.matter_assignment import MatterAssignment
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.user import User
from app.schemas.client_portal import (
    ClientPortalAcceptRequest,
    ClientPortalAcceptResponse,
    FirmInviteCreate,
    FirmInviteResponse,
    PortalAttorney,
    PortalDocumentResponse,
    PortalInvoiceResponse,
    PortalMatterView,
    PortalMessageCreate,
    PortalMessageResponse,
)
from app.services.matter_file_store import MatterFileStore
from app.services.email import send_client_portal_invite
from app.services.portal_token import (
    PORTAL_TOKEN_EXPIRE_MINUTES,
    create_matter_portal_token,
)

settings = get_settings()
matter_file_store = MatterFileStore()

INVITE_TTL_DAYS = 14
CLIENT_PORTAL_COOKIE_NAME = "client_portal_token"

router = APIRouter(prefix="/api/portal/client", tags=["client-portal"])
firm_router = APIRouter(prefix="/api/matters", tags=["client-portal-admin"])


# ── Portal auth ─────────────────────────────────────────────────────────────


class ClientPortalContext:
    """Resolved identity for a client-portal request (magic-link, no User row)."""

    def __init__(
        self,
        *,
        tenant_id: str,
        matter_id: str,
        contact_id: str | None,
        email: str | None = None,
        invite_id: str | None = None,
    ):
        self.tenant_id = tenant_id
        self.matter_id = matter_id
        self.contact_id = contact_id
        self.email = email
        self.invite_id = invite_id


def _read_token(request: Request) -> str | None:
    token = request.cookies.get(CLIENT_PORTAL_COOKIE_NAME)
    if token:
        return token
    # Compatibility for portal sessions minted before the dedicated cookie.
    token = request.cookies.get("access_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1]
    return None


async def get_client_portal_context(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ClientPortalContext:
    """Authenticate a client-portal request via the ``client_portal`` JWT."""
    import time as _time

    token = _read_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("client_portal") is not True:
        raise HTTPException(status_code=403, detail="Client portal access only")

    # Check JTI revocation so revoking a portal invite also kills active sessions.
    jti: str | None = payload.get("jti")
    if jti:
        redis = getattr(request.app.state, "redis", None)
        if redis:
            if await redis.exists(f"jti:{jti}"):
                raise HTTPException(
                    status_code=401, detail="Portal session has been revoked"
                )
        else:
            blacklist = getattr(request.app.state, "jti_blacklist", {})
            ts = blacklist.get(jti)
            if ts and _time.time() < ts:
                raise HTTPException(
                    status_code=401, detail="Portal session has been revoked"
                )

    tenant_id = payload.get("tenant_id")
    matter_id = payload.get("matter_id")
    if not tenant_id or not matter_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    await set_tenant_context(db, str(tenant_id))

    invite_id = payload.get("invite_id")
    if not invite_id:
        raise HTTPException(status_code=401, detail="Portal session must be renewed")
    result = await db.execute(
        select(ClientPortalInvite).where(
            ClientPortalInvite.id == invite_id,
            ClientPortalInvite.tenant_id == tenant_id,
            ClientPortalInvite.matter_id == matter_id,
        )
    )
    invite = result.scalar_one_or_none()
    if invite is None or invite.revoked:
        raise HTTPException(status_code=401, detail="Portal session has been revoked")
    if invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Portal session has expired")

    return ClientPortalContext(
        tenant_id=str(tenant_id),
        matter_id=str(matter_id),
        contact_id=payload.get("contact_id"),
        email=payload.get("email"),
        invite_id=str(invite_id) if invite_id else None,
    )


def _set_cookie(response: Response, token: str) -> None:
    is_production = settings.BACKEND_URL.startswith("https://")
    response.set_cookie(
        key=CLIENT_PORTAL_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_production,
        samesite="Lax",
        max_age=PORTAL_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


async def _load_matter(db: AsyncSession, ctx: ClientPortalContext) -> Matter:
    result = await db.execute(
        select(Matter).where(
            Matter.id == ctx.matter_id,
            Matter.tenant_id == ctx.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None or not matter.portal_enabled:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


# ── Invite acceptance ───────────────────────────────────────────────────────


@router.post("/accept", response_model=ClientPortalAcceptResponse)
async def accept_invite(
    body: ClientPortalAcceptRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(ClientPortalInvite).where(ClientPortalInvite.token_hash == token_hash)
    )
    invite = result.scalar_one_or_none()
    if invite is None or invite.revoked:
        raise HTTPException(status_code=404, detail="Invite not found or revoked")
    if invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Invite has expired")

    await set_tenant_context(db, str(invite.tenant_id))
    matter = await db.get(Matter, invite.matter_id)
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    invite.accepted_at = datetime.now(timezone.utc)
    token = create_matter_portal_token(
        tenant_id=str(invite.tenant_id),
        matter_id=str(invite.matter_id),
        contact_id=str(invite.contact_id) if invite.contact_id else None,
        email=invite.email,
        invite_id=str(invite.id),
    )
    await db.commit()
    _set_cookie(response, token)
    return ClientPortalAcceptResponse(
        matter_id=str(matter.id), matter_name=matter.matter_name
    )


# ── Matter overview ─────────────────────────────────────────────────────────


@router.get("/matter", response_model=PortalMatterView)
async def portal_matter(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    matter = await _load_matter(db, ctx)

    assignments = await db.execute(
        select(MatterAssignment.role, User.full_name, User.email)
        .join(User, User.id == MatterAssignment.user_id)
        .where(
            MatterAssignment.matter_id == ctx.matter_id,
            MatterAssignment.tenant_id == ctx.tenant_id,
        )
    )
    attorneys = [
        PortalAttorney(name=name or email or "Legal team", role=role)
        for role, name, email in assignments.all()
    ]

    return PortalMatterView(
        matter_id=str(matter.id),
        matter_name=matter.matter_name,
        status=matter.status,
        stage=matter.stage,
        practice_area=matter.practice_area,
        description=matter.description,
        key_dates=matter.key_dates,
        attorneys=attorneys,
    )


# ── Messages (CommunicationLog channel='portal') ────────────────────────────


@router.get("/messages", response_model=List[PortalMessageResponse])
async def portal_list_messages(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    await _load_matter(db, ctx)
    result = await db.execute(
        select(CommunicationLog)
        .where(
            CommunicationLog.matter_id == ctx.matter_id,
            CommunicationLog.tenant_id == ctx.tenant_id,
            CommunicationLog.channel == "portal",
        )
        .order_by(CommunicationLog.occurred_at)
    )
    return [
        PortalMessageResponse(
            id=str(m.id),
            direction=m.direction,
            subject=m.subject,
            body=m.body,
            occurred_at=m.occurred_at,
        )
        for m in result.scalars().all()
    ]


@router.post("/messages", response_model=PortalMessageResponse, status_code=201)
async def portal_create_message(
    body: PortalMessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    await _load_matter(db, ctx)
    msg = CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(ctx.tenant_id),
        matter_id=uuid.UUID(ctx.matter_id),
        contact_id=uuid.UUID(ctx.contact_id) if ctx.contact_id else None,
        direction="inbound",  # from the client to the firm
        channel="portal",
        status="received",
        subject=body.subject or "Portal message",
        body=body.body,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return PortalMessageResponse(
        id=str(msg.id),
        direction=msg.direction,
        subject=msg.subject,
        body=msg.body,
        occurred_at=msg.occurred_at,
    )


# ── Documents ───────────────────────────────────────────────────────────────


@router.get("/documents", response_model=List[PortalDocumentResponse])
async def portal_list_documents(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    await _load_matter(db, ctx)
    result = await db.execute(
        select(MatterDocument)
        .where(
            MatterDocument.matter_id == ctx.matter_id,
            MatterDocument.tenant_id == ctx.tenant_id,
            MatterDocument.portal_visible.is_(True),
        )
        .order_by(MatterDocument.created_at.desc())
    )
    return [
        PortalDocumentResponse(
            id=str(d.id),
            filename=d.filename,
            content_type=d.content_type,
            file_size=d.file_size,
            description=d.description,
            uploaded_by_client=d.uploaded_by_user_id is None,
            created_at=d.created_at,
        )
        for d in result.scalars().all()
    ]


@router.post(
    "/documents/upload", response_model=PortalDocumentResponse, status_code=201
)
async def portal_upload_document(
    request: Request,
    file: UploadFile = File(...),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    matter = await _load_matter(db, ctx)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    file_bytes = await file.read()
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    safe_filename = os.path.basename(file.filename)
    storage_path = await matter_file_store.store_matter_file(
        db=db,
        tenant_id=str(ctx.tenant_id),
        matter_slug=matter.slug,
        category="client-portal",
        filename=safe_filename,
        content=file_bytes,
        content_type=file.content_type or "application/octet-stream",
    )
    # Client uploads are visible to the client (uploaded_by_user_id stays NULL).
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(ctx.tenant_id),
        matter_id=uuid.UUID(ctx.matter_id),
        uploaded_by_user_id=None,
        filename=safe_filename,
        content_type=file.content_type,
        file_size=len(file_bytes),
        storage_path=storage_path,
        description=description,
        document_category="client-portal",
        portal_visible=True,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return PortalDocumentResponse(
        id=str(doc.id),
        filename=doc.filename,
        content_type=doc.content_type,
        file_size=doc.file_size,
        description=doc.description,
        uploaded_by_client=True,
        created_at=doc.created_at,
    )


@router.get("/documents/{doc_id}/download")
async def portal_download_document(
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    await _load_matter(db, ctx)
    result = await db.execute(
        select(MatterDocument).where(
            MatterDocument.id == doc_id,
            MatterDocument.matter_id == ctx.matter_id,
            MatterDocument.tenant_id == ctx.tenant_id,
            MatterDocument.portal_visible.is_(True),
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None or not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "application/octet-stream",
    )


# ── Invoices ────────────────────────────────────────────────────────────────


@router.get("/invoices", response_model=List[PortalInvoiceResponse])
async def portal_list_invoices(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ctx = await get_client_portal_context(request, db)
    await _load_matter(db, ctx)
    result = await db.execute(
        select(Invoice)
        .where(
            Invoice.matter_id == ctx.matter_id,
            Invoice.tenant_id == ctx.tenant_id,
            Invoice.status.in_(("sent", "paid", "overdue")),
        )
        .order_by(Invoice.issue_date.desc())
    )
    return [
        PortalInvoiceResponse(
            id=str(inv.id),
            invoice_number=inv.invoice_number,
            status=inv.status,
            issue_date=inv.issue_date,
            due_date=inv.due_date,
            total=inv.total,
            stripe_payment_link=inv.stripe_payment_link,
        )
        for inv in result.scalars().all()
    ]


# ── Firm-side invite management ─────────────────────────────────────────────


async def _get_matter_for_firm(db: AsyncSession, matter_id: str, tenant_id) -> Matter:
    result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


@firm_router.post(
    "/{matter_id}/portal/invite", response_model=FirmInviteResponse, status_code=201
)
async def create_portal_invite(
    matter_id: str,
    body: FirmInviteCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_for_firm(db, matter_id, user.tenant_id)

    email = body.email
    contact_id = body.contact_id
    # Default the invite email to the linked client contact, if present.
    if not email and (contact_id or matter.client_contact_id):
        cid = contact_id or str(matter.client_contact_id)
        contact = await db.get(Contact, uuid.UUID(cid))
        if contact is not None:
            email = contact.email
            contact_id = str(contact.id)
    if not email:
        raise HTTPException(
            status_code=400, detail="An email is required to send a portal invite"
        )

    # Enabling the portal is implied by issuing an invite.
    matter.portal_enabled = True

    raw_token = secrets.token_urlsafe(32)
    invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        contact_id=uuid.UUID(contact_id) if contact_id else None,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        email=email,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS),
        created_by_user_id=user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    invite_url = (
        f"{settings.FRONTEND_URL.rstrip('/')}/portal/client/accept?token={raw_token}"
    )
    email_sent = True
    delivery_error = None
    try:
        await send_client_portal_invite(
            to_email=email,
            matter_name=matter.matter_name,
            invite_url=invite_url,
        )
    except Exception:  # pragma: no cover - email best-effort
        email_sent = False
        delivery_error = (
            "Email delivery was not confirmed. Copy and share the invite link manually."
        )

    return FirmInviteResponse(
        id=str(invite.id),
        matter_id=str(matter.id),
        email=invite.email,
        invite_url=invite_url,
        email_sent=email_sent,
        delivery_error=delivery_error,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        revoked=invite.revoked,
    )


@firm_router.get("/{matter_id}/portal/invites", response_model=List[FirmInviteResponse])
async def list_portal_invites(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_for_firm(db, matter_id, user.tenant_id)
    result = await db.execute(
        select(ClientPortalInvite)
        .where(
            ClientPortalInvite.matter_id == matter_id,
            ClientPortalInvite.tenant_id == user.tenant_id,
        )
        .order_by(ClientPortalInvite.created_at.desc())
    )
    return [
        FirmInviteResponse(
            id=str(inv.id),
            matter_id=str(inv.matter_id),
            email=inv.email,
            invite_url=None,  # raw token is only returned at creation
            expires_at=inv.expires_at,
            accepted_at=inv.accepted_at,
            revoked=inv.revoked,
        )
        for inv in result.scalars().all()
    ]


@firm_router.delete("/{matter_id}/portal/invites/{invite_id}", status_code=204)
async def revoke_portal_invite(
    matter_id: str,
    invite_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(ClientPortalInvite).where(
            ClientPortalInvite.id == invite_id,
            ClientPortalInvite.matter_id == matter_id,
            ClientPortalInvite.tenant_id == user.tenant_id,
        )
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite.revoked = True
    await db.commit()
    return Response(status_code=204)
