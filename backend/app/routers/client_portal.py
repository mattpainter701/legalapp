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
import logging
import os
import re
import secrets
import time as _time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.services.upload_guard import reject_oversized_request
from app.database import bind_tenant_context, get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.billing import Invoice, Payment
from app.models.client_portal import ClientPortalInvite
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.matter_assignment import MatterAssignment
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.signature import SignatureRequest, SignatureSigner
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.client_portal import (
    MAX_DOCUMENT_DESCRIPTION,
    ClientPortalAcceptRequest,
    ClientPortalAcceptResponse,
    FirmInviteCreate,
    FirmInviteResponse,
    PortalAttorney,
    PortalDocumentResponse,
    PortalInvoiceList,
    PortalInvoiceResponse,
    PortalKeyDate,
    PortalMarkReadRequest,
    PortalMarkReadResponse,
    PortalMatterView,
    PortalMessageCreate,
    PortalMessageList,
    PortalMessageResponse,
    PortalSessionResponse,
)
from app.services.matter_file_store import (
    MatterFileAccessError,
    MatterFileIntegrityError,
    MatterFileMetadataError,
    MatterFileNotFound,
    MatterFileStore,
    MatterFileTooLarge,
)
from app.services.email import (
    EmailDeliveryResult,
    email_delivery_http_error,
    send_client_portal_invite,
    send_client_portal_message_alert,
)
from app.services.provider_http import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
)
from app.services.portal_invites import (
    PORTAL_INVITE_UNAVAILABLE_DETAIL,
    PORTAL_INVITE_UNAVAILABLE_STATUS,
    resolve_active_portal_invite,
)
from app.services.esign.service import signer_can_act_now
from app.services.portal_token import (
    PORTAL_TOKEN_EXPIRE_MINUTES,
    create_matter_portal_token,
)

logger = logging.getLogger(__name__)
settings = get_settings()
matter_file_store = MatterFileStore()

INVITE_TTL_DAYS = 14
CLIENT_PORTAL_COOKIE_NAME = "client_portal_token"

# A portal message list is a conversation, not a dataset. Cap the page so a
# long-running matter can never make the first paint unbounded.
MESSAGE_PAGE_DEFAULT = 50
MESSAGE_PAGE_MAX = 200

# Writing ``last_seen_at`` on every request would add a write to each poll, so
# only refresh it once the stored value is this stale.
LAST_SEEN_REFRESH_SECONDS = 300

# Client uploads land in the firm's document store and are handed back to staff
# on download, so the portal accepts documents and media only. Everything a
# browser or OS might treat as runnable is refused at the edge rather than
# stored and hoped about later.
ALLOWED_UPLOAD_EXTENSIONS = frozenset(
    {
        # Documents
        "pdf",
        "doc",
        "docx",
        "rtf",
        "odt",
        "txt",
        "md",
        "pages",
        # Spreadsheets / presentations
        "xls",
        "xlsx",
        "csv",
        "ods",
        "numbers",
        "ppt",
        "pptx",
        "odp",
        "key",
        # Images / scans
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "heic",
        "heif",
        "tif",
        "tiff",
        "bmp",
        # Correspondence exports
        "eml",
        "msg",
        # Media clients commonly send as evidence
        "mp3",
        "m4a",
        "wav",
        "mp4",
        "mov",
        # Bundles
        "zip",
    }
)

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
        expires_at: datetime | None = None,
        invite_expires_at: datetime | None = None,
        messages_seen_at: datetime | None = None,
        jti: str | None = None,
    ):
        self.tenant_id = tenant_id
        self.matter_id = matter_id
        self.contact_id = contact_id
        self.email = email
        self.invite_id = invite_id
        self.expires_at = expires_at
        self.invite_expires_at = invite_expires_at
        self.messages_seen_at = messages_seen_at
        self.jti = jti


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


async def _is_revoked_jti(request: Request, jti: str | None) -> bool:
    if not jti:
        return False
    redis = getattr(request.app.state, "redis", None)
    if redis:
        return bool(await redis.exists(f"jti:{jti}"))
    blacklist = getattr(request.app.state, "jti_blacklist", {})
    ts = blacklist.get(jti)
    return bool(ts and _time.time() < ts)


async def _revoke_jti(request: Request, jti: str | None, exp: int | None) -> None:
    """Blacklist a portal JTI until its natural expiry."""
    if not jti or not exp:
        return
    ttl = max(0, int(exp) - int(_time.time()))
    if ttl == 0:
        return
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(f"jti:{jti}", ttl, "1")
        return
    # Without Redis this is per-worker and dev-only; the invite-level revoked
    # flag checked on every request is the durable control.
    logger.warning(
        "Redis unavailable on portal logout: session revocation is per-worker "
        "(dev-only). The invite remains the authoritative revocation control."
    )
    blacklist = getattr(request.app.state, "jti_blacklist", None)
    if blacklist is None:
        blacklist = {}
        request.app.state.jti_blacklist = blacklist
    blacklist[jti] = _time.time() + ttl


def _aware(value: datetime | None) -> datetime | None:
    """Normalize a possibly naive DB timestamp to UTC-aware for comparison."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def get_client_portal_context(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ClientPortalContext:
    """Authenticate a client-portal request via the ``client_portal`` JWT."""
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

    # Check JTI revocation so revoking a portal invite — or the client signing
    # out — also kills active sessions.
    jti: str | None = payload.get("jti")
    if await _is_revoked_jti(request, jti):
        raise HTTPException(status_code=401, detail="Portal session has been revoked")

    tenant_claim = payload.get("tenant_id")
    matter_id = payload.get("matter_id")
    if not tenant_claim or not matter_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        tenant_id = uuid.UUID(str(tenant_claim))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Not authenticated")

    tenant = await db.scalar(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
    )
    if tenant is None:
        raise HTTPException(status_code=401, detail="Portal session unavailable")
    # Bind rather than set: portal endpoints commit (activity stamps, messages,
    # read receipts), and a transaction-local GUC would not survive that — every
    # query after the first commit would run with no tenant and RLS would
    # fail closed.
    await bind_tenant_context(db, str(tenant_id))

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
    now = datetime.now(timezone.utc)
    invite_expires_at = _aware(invite.expires_at)
    if invite_expires_at is not None and invite_expires_at < now:
        raise HTTPException(status_code=401, detail="Portal session has expired")

    await _touch_last_seen(db, invite, now)

    exp_claim = payload.get("exp")
    return ClientPortalContext(
        tenant_id=str(tenant_id),
        matter_id=str(matter_id),
        contact_id=payload.get("contact_id"),
        email=payload.get("email"),
        invite_id=str(invite_id) if invite_id else None,
        expires_at=(
            datetime.fromtimestamp(int(exp_claim), tz=timezone.utc)
            if exp_claim
            else None
        ),
        invite_expires_at=invite_expires_at,
        messages_seen_at=_aware(invite.messages_seen_at),
        jti=jti,
    )


async def _touch_last_seen(
    db: AsyncSession, invite: ClientPortalInvite, now: datetime
) -> None:
    """Record portal activity, but no more often than the refresh interval.

    Best-effort: a failure here must never cost the client access to a matter
    they are otherwise authorized to see.
    """
    last_seen = _aware(invite.last_seen_at)
    if last_seen and (now - last_seen).total_seconds() < LAST_SEEN_REFRESH_SECONDS:
        return
    try:
        invite.last_seen_at = now
        await db.commit()
    except Exception:  # pragma: no cover - activity tracking is not load-bearing
        logger.warning("Failed to record client portal activity", exc_info=True)
        await db.rollback()


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


async def portal_matter_dep(
    ctx: ClientPortalContext = Depends(get_client_portal_context),
    db: AsyncSession = Depends(get_db),
) -> tuple[ClientPortalContext, Matter]:
    """Resolve the portal identity and its matter in one dependency."""
    return ctx, await _load_matter(db, ctx)


# ── Invite acceptance ───────────────────────────────────────────────────────


@router.post("/accept", response_model=ClientPortalAcceptResponse)
async def accept_invite(
    body: ClientPortalAcceptRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
    invite = await resolve_active_portal_invite(
        db,
        ClientPortalInvite,
        token_hash,
    )
    if (
        invite is None
        or invite.revoked
        or _aware(invite.expires_at) < datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=PORTAL_INVITE_UNAVAILABLE_STATUS,
            detail=PORTAL_INVITE_UNAVAILABLE_DETAIL,
        )

    matter = await db.get(Matter, invite.matter_id)
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    now = datetime.now(timezone.utc)
    invite.accepted_at = now
    invite.last_seen_at = now
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


# ── Session ─────────────────────────────────────────────────────────────────


@router.get("/session", response_model=PortalSessionResponse)
async def portal_session(
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
):
    """Identity and remaining lifetime of the current portal session."""
    ctx, matter = resolved
    return PortalSessionResponse(
        matter_id=str(matter.id),
        matter_name=matter.matter_name,
        email=ctx.email,
        expires_at=ctx.expires_at
        or datetime.now(timezone.utc) + timedelta(minutes=PORTAL_TOKEN_EXPIRE_MINUTES),
        invite_expires_at=ctx.invite_expires_at or datetime.now(timezone.utc),
    )


@router.post("/logout", status_code=204)
async def portal_logout(request: Request):
    """End the portal session: expire the cookie and blacklist the JTI.

    Deliberately tolerant of a missing or unreadable token — signing out must
    always clear the cookie and report success, never leave a client stuck on a
    session they are trying to end.

    The cookie is cleared on the response this returns, not on an injected
    ``Response`` parameter: FastAPI discards the injected object's headers when
    a handler returns a ``Response`` of its own, so the browser would keep the
    portal JWT. The JTI blacklist alone is not enough to cover that — without
    Redis it is per-worker, and with Redis it lapses on eviction or restart.
    """
    token = _read_token(request)
    if token:
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": False},
            )
            if payload.get("client_portal") is True:
                await _revoke_jti(request, payload.get("jti"), payload.get("exp"))
        except JWTError:
            pass
    response = Response(status_code=204)
    # Attributes must mirror ``_set_cookie`` or the browser will not match the
    # cookie being expired.
    response.delete_cookie(
        CLIENT_PORTAL_COOKIE_NAME,
        httponly=True,
        secure=settings.BACKEND_URL.startswith("https://"),
        samesite="Lax",
        path="/",
    )
    return response


# ── Matter overview ─────────────────────────────────────────────────────────


_DATE_PATTERNS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y")


def _parse_key_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    # ISO timestamps are the common case; fall back to the handful of formats
    # firm staff actually type into the key-dates editor.
    candidate = re.split(r"[T ]", raw)[0]
    for fmt in _DATE_PATTERNS:
        for text in (candidate, raw):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _humanize_key(key: str) -> str:
    cleaned = re.sub(r"[_-]+", " ", str(key)).strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else str(key)


def _build_key_dates(raw: dict | None) -> list[PortalKeyDate]:
    """Turn the firm's free-form key-dates mapping into a sorted, dated list."""
    if not isinstance(raw, dict):
        return []
    today = date.today()
    entries: list[PortalKeyDate] = []
    for key, value in raw.items():
        if value in (None, "", []):
            continue
        text = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
        parsed = _parse_key_date(value)
        entries.append(
            PortalKeyDate(
                label=_humanize_key(key),
                value=text,
                iso_date=parsed,
                is_past=bool(parsed and parsed < today),
                days_away=(parsed - today).days if parsed else None,
            )
        )
    # Dated entries first, chronologically; undated firm notes trail behind.
    entries.sort(key=lambda e: (e.iso_date is None, e.iso_date or today, e.label))
    return entries


async def _unread_message_count(db: AsyncSession, ctx: ClientPortalContext) -> int:
    """Count firm messages posted after the client's read high-water mark."""
    stmt = select(func.count(CommunicationLog.id)).where(
        CommunicationLog.matter_id == ctx.matter_id,
        CommunicationLog.tenant_id == ctx.tenant_id,
        CommunicationLog.channel == "portal",
        CommunicationLog.direction == "outbound",
    )
    if ctx.messages_seen_at is not None:
        stmt = stmt.where(CommunicationLog.occurred_at > ctx.messages_seen_at)
    return int(await db.scalar(stmt) or 0)


def _signer_matches_portal(signer: SignatureSigner, ctx: ClientPortalContext) -> bool:
    """Contact first, then email — the same rule the e-signature portal uses.

    Reimplemented rather than imported because ``app.routers.esignature`` imports
    this module; ``signer_can_act_now`` below comes from the esign *service*,
    which imports no routers, so that one is safe to share.
    """
    if (
        ctx.contact_id
        and signer.contact_id
        and str(signer.contact_id) == str(ctx.contact_id)
    ):
        return True
    if ctx.email and signer.email:
        return signer.email.strip().lower() == ctx.email.strip().lower()
    return False


async def _pending_signature_count(db: AsyncSession, ctx: ClientPortalContext) -> int:
    """Signature requests this portal identity can act on *right now*.

    Must agree with what ``esignature.portal_list_signatures`` will actually
    show. Counting every pending signer would badge the tab for a client who is
    second in an enforced signing order — they would open Signatures and find
    nothing to do — so signing-order eligibility is applied here too.
    """
    if not ctx.contact_id and not ctx.email:
        return 0
    result = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.matter_id == ctx.matter_id,
            SignatureRequest.tenant_id == ctx.tenant_id,
            SignatureRequest.status.in_(("sent", "partially_signed")),
        )
    )
    return sum(
        1
        for request in result.scalars().all()
        if any(
            _signer_matches_portal(signer, ctx) and signer_can_act_now(request, signer)
            for signer in request.signers
        )
    )


@router.get("/matter", response_model=PortalMatterView)
async def portal_matter(
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    ctx, matter = resolved

    assignments = await db.execute(
        select(MatterAssignment.role, User.full_name, User.email)
        .join(User, User.id == MatterAssignment.user_id)
        .where(
            MatterAssignment.matter_id == ctx.matter_id,
            MatterAssignment.tenant_id == ctx.tenant_id,
        )
    )
    attorneys = [
        PortalAttorney(name=name or email or "Legal team", role=role, email=email)
        for role, name, email in assignments.all()
    ]

    key_date_list = _build_key_dates(matter.key_dates)
    next_key_date = next(
        (k for k in key_date_list if k.iso_date and not k.is_past), None
    )

    document_count = int(
        await db.scalar(
            select(func.count(MatterDocument.id)).where(
                MatterDocument.matter_id == ctx.matter_id,
                MatterDocument.tenant_id == ctx.tenant_id,
                MatterDocument.portal_visible.is_(True),
            )
        )
        or 0
    )
    last_activity_at = await db.scalar(
        select(func.max(CommunicationLog.occurred_at)).where(
            CommunicationLog.matter_id == ctx.matter_id,
            CommunicationLog.tenant_id == ctx.tenant_id,
            CommunicationLog.channel == "portal",
        )
    )
    open_invoices = await _visible_invoices(db, ctx)
    outstanding = sum(
        (inv.balance_due for inv in open_invoices if inv.balance_due > 0),
        Decimal("0"),
    )

    return PortalMatterView(
        matter_id=str(matter.id),
        matter_name=matter.matter_name,
        status=matter.status,
        stage=matter.stage,
        practice_area=matter.practice_area,
        description=matter.description,
        key_dates=matter.key_dates,
        key_date_list=key_date_list,
        next_key_date=next_key_date,
        attorneys=attorneys,
        unread_message_count=await _unread_message_count(db, ctx),
        document_count=document_count,
        pending_signature_count=await _pending_signature_count(db, ctx),
        open_invoice_count=sum(1 for inv in open_invoices if inv.balance_due > 0),
        outstanding_balance=outstanding,
        last_activity_at=_aware(last_activity_at),
    )


# ── Messages (CommunicationLog channel='portal') ────────────────────────────


@router.get("/messages", response_model=PortalMessageList)
async def portal_list_messages(
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(MESSAGE_PAGE_DEFAULT, ge=1, le=MESSAGE_PAGE_MAX),
    offset: int = Query(0, ge=0),
):
    ctx, _matter = resolved
    base = (
        CommunicationLog.matter_id == ctx.matter_id,
        CommunicationLog.tenant_id == ctx.tenant_id,
        CommunicationLog.channel == "portal",
    )
    total = int(
        await db.scalar(select(func.count(CommunicationLog.id)).where(*base)) or 0
    )
    # Page from the newest end so a long history still opens on what matters,
    # then present the page oldest-first the way a conversation reads.
    result = await db.execute(
        select(CommunicationLog)
        .where(*base)
        .order_by(CommunicationLog.occurred_at.desc(), CommunicationLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(result.scalars().all())
    rows.reverse()
    seen_at = ctx.messages_seen_at
    messages = [
        PortalMessageResponse(
            id=str(m.id),
            direction=m.direction,
            subject=m.subject,
            body=m.body,
            occurred_at=m.occurred_at,
            unread=(
                m.direction == "outbound"
                and (seen_at is None or _aware(m.occurred_at) > seen_at)
            ),
        )
        for m in rows
    ]
    return PortalMessageList(
        messages=messages,
        unread_count=await _unread_message_count(db, ctx),
        total=total,
        has_more=offset + len(rows) < total,
    )


@router.post("/messages", response_model=PortalMessageResponse, status_code=201)
async def portal_create_message(
    body: PortalMessageCreate,
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    ctx, matter = resolved
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
    await _notify_firm_of_message(db, ctx, matter, msg)
    return PortalMessageResponse(
        id=str(msg.id),
        direction=msg.direction,
        subject=msg.subject,
        body=msg.body,
        occurred_at=msg.occurred_at,
    )


@router.post("/messages/read", response_model=PortalMarkReadResponse)
async def portal_mark_messages_read(
    body: PortalMarkReadRequest | None = None,
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    """Advance the client's read high-water mark.

    Bounded by ``seen_through`` — the newest message the client was actually
    shown. Marking up to *now* would swallow anything the firm posted between
    the client's list request and this one: the badge would clear and the
    message would arrive already looking read. Falls back to now only when the
    client sends no bound, and never moves the mark backwards, so an out-of-order
    receipt from a stale tab cannot un-read newer messages.
    """
    ctx, _matter = resolved
    now = datetime.now(timezone.utc)
    invite = await db.get(ClientPortalInvite, uuid.UUID(ctx.invite_id))
    if invite is None:
        raise HTTPException(status_code=401, detail="Portal session has been revoked")

    target = _aware(body.seen_through) if body and body.seen_through else now
    target = min(target, now)
    existing = _aware(invite.messages_seen_at)
    if existing and existing > target:
        target = existing

    invite.messages_seen_at = target
    await db.commit()
    ctx.messages_seen_at = target
    return PortalMarkReadResponse(
        messages_seen_at=target, unread_count=await _unread_message_count(db, ctx)
    )


async def _notify_firm_of_message(
    db: AsyncSession,
    ctx: ClientPortalContext,
    matter: Matter,
    msg: CommunicationLog,
) -> None:
    """Email the assigned legal team that the client wrote in.

    Best-effort: a portal message is already persisted by the time this runs, so
    a mail failure must never surface as a failed send to the client.
    """
    try:
        recipients = list(
            (
                await db.scalars(
                    select(User.email)
                    .join(MatterAssignment, MatterAssignment.user_id == User.id)
                    .where(
                        MatterAssignment.matter_id == ctx.matter_id,
                        MatterAssignment.tenant_id == ctx.tenant_id,
                        User.email.isnot(None),
                        User.is_active.is_(True),
                    )
                )
            ).all()
        )
        recipients = sorted({e for e in recipients if e})
        if not recipients:
            return
        matter_url = (
            f"{settings.FRONTEND_URL.rstrip('/')}/matters/{matter.id}?tab=portal"
        )
        await send_client_portal_message_alert(
            to_emails=recipients,
            matter_name=matter.matter_name,
            sender=ctx.email or "Your client",
            body=msg.body or "",
            matter_url=matter_url,
        )
    except Exception:  # pragma: no cover - notification is best-effort
        logger.warning(
            "Failed to notify the legal team of a client portal message",
            exc_info=True,
        )


# ── Documents ───────────────────────────────────────────────────────────────


@router.get("/documents", response_model=List[PortalDocumentResponse])
async def portal_list_documents(
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    ctx, _matter = resolved
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


def _validate_upload_filename(filename: str) -> str:
    """Return a safe basename, rejecting anything outside the allowlist."""
    # Browsers on Windows can submit a full path; ``os.path.basename`` only
    # understands the server's separator, so strip both before trusting it.
    safe_filename = os.path.basename(filename.replace("\\", "/")).strip()
    if not safe_filename or safe_filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Filename is required")
    if len(safe_filename) > 255:
        raise HTTPException(
            status_code=400, detail="Filename is too long (255 characters maximum)"
        )
    _stem, _dot, extension = safe_filename.rpartition(".")
    extension = extension.lower()
    if not _dot or extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "That file type can't be uploaded through the portal. Documents, "
                "spreadsheets, images, emails, and PDFs are supported."
            ),
        )
    return safe_filename


@router.post(
    "/documents/upload", response_model=PortalDocumentResponse, status_code=201
)
async def portal_upload_document(
    request: Request,
    file: UploadFile = File(...),
    description: str | None = Form(None),
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    ctx, matter = resolved

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    safe_filename = _validate_upload_filename(file.filename)
    if description and len(description) > MAX_DOCUMENT_DESCRIPTION:
        raise HTTPException(
            status_code=400,
            detail=(
                "Description is too long "
                f"({MAX_DOCUMENT_DESCRIPTION} characters maximum)"
            ),
        )

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    reject_oversized_request(request, max_bytes, settings.MAX_FILE_SIZE_MB)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="That file is empty")
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    storage_result = await matter_file_store.store_matter_file_result(
        db=db,
        tenant_id=str(ctx.tenant_id),
        matter_slug=matter.slug,
        category="client-portal",
        filename=safe_filename,
        content=file_bytes,
        content_type=file.content_type or "application/octet-stream",
        matter_cloud_folder=matter.cloud_folder,
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
        storage_path=storage_result.storage_path,
        storage_provider=storage_result.provider,
        storage_backend=storage_result.backend,
        provider_object_id=storage_result.provider_item_id,
        provider_drive_id=storage_result.drive_id,
        provider_parent_id=storage_result.parent_id,
        storage_error=storage_result.error,
        description=(description or None),
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
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    ctx, _matter = resolved
    result = await db.execute(
        select(MatterDocument).where(
            MatterDocument.id == doc_id,
            MatterDocument.matter_id == ctx.matter_id,
            MatterDocument.tenant_id == ctx.tenant_id,
            MatterDocument.portal_visible.is_(True),
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = await matter_file_store.read_matter_file_bytes(
            db=db,
            tenant_id=str(ctx.tenant_id),
            document=doc,
        )
    except MatterFileTooLarge as exc:
        raise HTTPException(
            status_code=413, detail="File is too large to download"
        ) from exc
    except ProviderAuthError as exc:
        raise HTTPException(
            status_code=503,
            detail="The firm's document storage connection needs attention",
        ) from exc
    except (MatterFileNotFound, ProviderNotFound) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except MatterFileIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="The stored file failed its integrity check",
        ) from exc
    except (MatterFileAccessError, MatterFileMetadataError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail="The connected document provider is temporarily unavailable",
        ) from exc

    safe_name = (doc.filename or "document").replace("\r", "").replace("\n", "")
    fallback_name = safe_name.encode("ascii", "ignore").decode() or "document"
    fallback_name = fallback_name.replace('"', "'").replace("\\", "_")
    disposition = (
        f'attachment; filename="{fallback_name}"; '
        f"filename*=UTF-8''{quote(safe_name, safe='')}"
    )
    return Response(
        content=content,
        media_type=doc.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Invoices ────────────────────────────────────────────────────────────────


async def _visible_invoices(
    db: AsyncSession, ctx: ClientPortalContext
) -> list[PortalInvoiceResponse]:
    """Client-visible invoices with payment totals resolved per invoice."""
    result = await db.execute(
        select(Invoice)
        .where(
            Invoice.matter_id == ctx.matter_id,
            Invoice.tenant_id == ctx.tenant_id,
            # "overdue" is computed from due_date, never stored — an unpaid
            # overdue invoice still carries "sent"/"partially_paid" status.
            Invoice.status.in_(("sent", "partially_paid", "paid")),
        )
        .order_by(Invoice.issue_date.desc())
    )
    invoices = list(result.scalars().all())
    if not invoices:
        return []

    paid_rows = await db.execute(
        select(Payment.invoice_id, func.coalesce(func.sum(Payment.amount), 0))
        .where(
            Payment.tenant_id == ctx.tenant_id,
            Payment.invoice_id.in_([inv.id for inv in invoices]),
        )
        .group_by(Payment.invoice_id)
    )
    paid_by_invoice = {row[0]: Decimal(row[1]) for row in paid_rows.all()}

    today = date.today()
    out: list[PortalInvoiceResponse] = []
    for inv in invoices:
        total = Decimal(inv.total or 0)
        paid = paid_by_invoice.get(inv.id, Decimal("0"))
        # A recorded status of "paid" is authoritative even where the firm
        # settled the invoice outside the payments ledger (write-off, trust
        # transfer), so it must not show the client a phantom balance.
        if inv.status == "paid":
            paid = max(paid, total)
        balance = max(total - paid, Decimal("0"))
        overdue = balance > 0 and inv.due_date is not None and inv.due_date < today
        out.append(
            PortalInvoiceResponse(
                id=str(inv.id),
                invoice_number=inv.invoice_number,
                status=inv.status,
                issue_date=inv.issue_date,
                due_date=inv.due_date,
                total=total,
                amount_paid=paid,
                balance_due=balance,
                is_overdue=overdue,
                days_overdue=(today - inv.due_date).days if overdue else 0,
                payment_terms=inv.payment_terms,
                stripe_payment_link=inv.stripe_payment_link,
            )
        )
    return out


@router.get("/invoices", response_model=PortalInvoiceList)
async def portal_list_invoices(
    resolved: tuple[ClientPortalContext, Matter] = Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    ctx, _matter = resolved
    invoices = await _visible_invoices(db, ctx)
    return PortalInvoiceList(
        invoices=invoices,
        total_billed=sum((i.total for i in invoices), Decimal("0")),
        total_paid=sum((i.amount_paid for i in invoices), Decimal("0")),
        outstanding_balance=sum((i.balance_due for i in invoices), Decimal("0")),
        overdue_balance=sum(
            (i.balance_due for i in invoices if i.is_overdue), Decimal("0")
        ),
    )


# ── Firm-side invite management ─────────────────────────────────────────────


async def _get_matter_for_firm(db: AsyncSession, matter_id: str, tenant_id) -> Matter:
    result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


def _firm_invite_response(inv: ClientPortalInvite, **overrides) -> FirmInviteResponse:
    payload = dict(
        id=str(inv.id),
        matter_id=str(inv.matter_id),
        email=inv.email,
        invite_url=None,  # raw token is only returned at creation
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        revoked=inv.revoked,
        last_seen_at=inv.last_seen_at,
        created_at=inv.created_at,
    )
    payload.update(overrides)
    return FirmInviteResponse(**payload)


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

    if body.revoke_existing:
        existing = await db.execute(
            select(ClientPortalInvite).where(
                ClientPortalInvite.matter_id == matter.id,
                ClientPortalInvite.tenant_id == user.tenant_id,
                ClientPortalInvite.revoked.is_(False),
            )
        )
        for prior in existing.scalars().all():
            prior.revoked = True

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
    delivery_result = EmailDeliveryResult.FAILED
    try:
        delivery_result = await send_client_portal_invite(
            to_email=email,
            matter_name=matter.matter_name,
            invite_url=invite_url,
        )
    except Exception:  # pragma: no cover - email best-effort
        delivery_result = EmailDeliveryResult.FAILED

    email_sent = bool(delivery_result)
    delivery_error = None
    if not email_sent:
        _status_code, delivery_error = email_delivery_http_error(
            delivery_result,
            action="Client portal invitation",
        )
        delivery_error += " The invite remains valid; copy and share its link manually."

    return _firm_invite_response(
        invite,
        invite_url=invite_url,
        email_sent=email_sent,
        delivery_error=delivery_error,
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
    return [_firm_invite_response(inv) for inv in result.scalars().all()]


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
