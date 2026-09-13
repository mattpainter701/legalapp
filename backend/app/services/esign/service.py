"""Orchestration for the portal e-signature flow.

``record_portal_signature`` captures one signer's in-document signature (typed
legal name plus the form values they entered); ``record_uploaded_copy`` records
a signed copy the client uploaded instead, which waits for staff to accept.
Once every signer has signed, ``complete_request_if_done`` renders the
executed PDF (or adopts the accepted upload), generates the evidence
certificate, files both to the matter, writes a timeline event and marks the
request completed.

Filing can fail when the tenant's cloud storage is unreachable. That must never
fail the client's signing action: the signature stays recorded, the failure is
noted on the request for staff, and ``retry_pending_completions`` (run by the
scheduler) finishes the job once storage is back.
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, set_tenant_context
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.models.signature import SignatureRequest, SignatureSigner
from app.services.esign.certificate import (
    build_certificate,
    immutable_certificate_filename,
)
from app.services.esign.followups import close_signature_followup, matter_timezone
from app.services.esign.plan import (
    SigningPlan,
    build_plan,
    initials_for,
    signer_refs,
)
from app.services.esign.render import Stamp, render_executed_pdf
from app.services.matter_file_store import (
    MatterFileReadError,
    MatterFileStore,
    MatterFileStoragePolicyError,
    StorageResult,
)
from app.services.matter_document_organization import autofile_folder_id
from app.services.provider_http import ProviderError

logger = logging.getLogger(__name__)
_file_store = MatterFileStore()

OPEN_STATUSES = ("sent", "partially_signed")
#: How long a failed filing attempt blocks the next automatic retry.
COMPLETION_RETRY_INTERVAL = timedelta(minutes=5)
STORAGE_FAILURE_EVENT_TITLE = "Signed copy could not be filed — storage unavailable"


class CompletionStorageError(RuntimeError):
    """Filing the executed copy or certificate failed; retry later."""


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def request_is_expired(
    request: SignatureRequest,
    *,
    now: datetime | None = None,
) -> bool:
    if not request.expires_at or request.status not in OPEN_STATUSES:
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


def all_signed(request: SignatureRequest) -> bool:
    signers = list(request.signers or [])
    return bool(signers) and all(s.status == "signed" for s in signers)


def submission_accepted(request: SignatureRequest) -> bool:
    """Staff accepted the client's uploaded signed copy."""
    return bool(request.submitted_document_id) and any(
        (s.audit or {}).get("accepted_at") for s in request.signers or []
    )


def awaiting_review(request: SignatureRequest) -> bool:
    """A signed copy was uploaded and staff have not decided on it yet."""
    return bool(request.submitted_document_id) and not submission_accepted(request)


def completion_pending(request: SignatureRequest) -> bool:
    """Everyone signed but the executed copy has not been filed yet."""
    return (
        request.status in OPEN_STATUSES
        and all_signed(request)
        and not awaiting_review(request)
    )


async def record_portal_signature(
    signer: SignatureSigner,
    *,
    typed_signature: str,
    ip: str | None,
    consent_text_version: str,
    user_agent: str | None = None,
    field_values: dict[str, str] | None = None,
    drawn_signature_png: bytes | None = None,
) -> None:
    """Mark a single signer as signed in the document (does not commit)."""
    now = datetime.now(timezone.utc)
    signer.status = "signed"
    signer.signed_at = now
    signer.signed_ip = ip
    signer.typed_signature = typed_signature
    signer.drawn_signature_png = drawn_signature_png or None
    signer.field_values = dict(field_values or {})
    signer.method = "portal_inline"
    signer.audit = {
        **(signer.audit or {}),
        "signed_at": now.isoformat(),
        "ip": ip,
        "typed_signature": typed_signature,
        # The drawing is evidence too: its hash binds the stamped image to
        # this signing, and the certificate carries the audit verbatim.
        "signature_method": "drawn" if drawn_signature_png else "typed",
        "drawn_signature_sha256": (
            hashlib.sha256(drawn_signature_png).hexdigest()
            if drawn_signature_png
            else None
        ),
        "method": "portal_inline",
        "field_count": len(signer.field_values),
        "consent_to_electronic_signature": True,
        "consent_text_version": consent_text_version,
        "user_agent": user_agent,
    }


async def record_uploaded_copy(
    request: SignatureRequest,
    signer: SignatureSigner,
    *,
    document: MatterDocument,
    ip: str | None,
    user_agent: str | None = None,
) -> None:
    """Record a signed copy the client uploaded; staff still have to accept it."""
    now = datetime.now(timezone.utc)
    signer.status = "signed"
    signer.signed_at = now
    signer.signed_ip = ip
    signer.method = "uploaded_copy"
    signer.audit = {
        **(signer.audit or {}),
        "method": "uploaded_copy",
        "uploaded_document_id": str(document.id),
        "uploaded_sha256": document.document_sha256,
        "ip": ip,
        "user_agent": user_agent,
        "signed_at": now.isoformat(),
    }
    request.submitted_document_id = document.id
    request.submitted_at = now
    if request.status == "sent":
        request.status = "partially_signed"


def accept_submission(request: SignatureRequest, *, user_id) -> None:
    """Staff accept the uploaded copy as the executed document (no commit)."""
    now = datetime.now(timezone.utc).isoformat()
    for signer in request.signers:
        if (
            signer.method == "uploaded_copy"
            or (signer.audit or {}).get("method") == "uploaded_copy"
        ):
            signer.audit = {
                **(signer.audit or {}),
                "accepted_by_user_id": str(user_id) if user_id else None,
                "accepted_at": now,
            }


def reject_submission(
    request: SignatureRequest, matter: Matter, *, reason: str, user_id
) -> tuple[list[SignatureSigner], MatterEvent]:
    """Send the uploaded copy back: the signer(s) who uploaded it sign again."""
    now = datetime.now(timezone.utc)
    clean_reason = reason.strip()
    reopened: list[SignatureSigner] = []
    for signer in request.signers:
        if (
            signer.method != "uploaded_copy"
            and (signer.audit or {}).get("method") != "uploaded_copy"
        ):
            continue
        audit = dict(signer.audit or {})
        rejections = list(audit.get("rejections") or [])
        rejections.append(
            {
                "reason": clean_reason,
                "rejected_at": now.isoformat(),
                "rejected_by_user_id": str(user_id) if user_id else None,
                "uploaded_document_id": audit.get("uploaded_document_id"),
            }
        )
        audit["rejections"] = rejections
        audit.pop("accepted_at", None)
        audit.pop("accepted_by_user_id", None)
        audit.pop("uploaded_document_id", None)
        audit.pop("signed_at", None)
        audit["method"] = None
        signer.audit = audit
        signer.status = "pending"
        signer.signed_at = None
        signer.signed_ip = None
        signer.method = None
        reopened.append(signer)
    request.submitted_document_id = None
    request.submitted_at = None
    request.status = (
        "partially_signed"
        if any(s.status == "signed" for s in request.signers)
        else "sent"
    )
    label = request.source_document_filename or "document"
    event = MatterEvent(
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        event_type="signature",
        title=f"Uploaded signed copy returned: {label}",
        content=(
            f"The uploaded signed copy of {label} was not accepted. "
            f"Reason: {clean_reason}. The client has been asked to sign again."
        ),
        note_type="system",
        created_by=user_id or request.created_by_user_id or matter.user_id,
    )
    return reopened, event


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


def decline_event(request: SignatureRequest, matter: Matter) -> MatterEvent:
    """Matter timeline entry for a portal decline.

    The client action is attributed the same way a completion is: the staff
    member who requested the signature, else the matter's responsible user. The
    reason is included when the signer supplied one so the firm sees *why*
    without opening the request.
    """
    label = request.source_document_filename or "document"
    reason = (request.decline_reason or "").strip()
    return MatterEvent(
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        event_type="signature",
        title=f"Signature declined: {label}",
        content=(
            f"The client declined to sign {label}."
            + (f" Reason: {reason}." if reason else "")
            + " The intake packet stays open; revise the document and resend it."
        ),
        note_type="system",
        created_by=request.created_by_user_id or matter.user_id,
    )


# ── Completion ──────────────────────────────────────────────────────────────


def executed_filename(document_name: str | None, request_id: str) -> str:
    base = (document_name or "document").rsplit(".", 1)[0]
    safe_base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("._-")[:80] or "document"
    short = re.sub(r"[^A-Fa-f0-9]+", "", str(request_id)).lower()[:8] or "request"
    return f"{safe_base}-signed-{short}.pdf"


def _signed_date_text(signed_at: datetime | None, timezone_name: str) -> str:
    moment = _as_aware_utc(signed_at or datetime.now(timezone.utc))
    try:
        local = moment.astimezone(ZoneInfo(timezone_name or "UTC"))
    except Exception:
        local = moment
    return local.strftime("%B %d, %Y")


def stamps_for(
    plan: SigningPlan,
    signers: list[SignatureSigner],
    *,
    request_id: str,
    timezone_name: str,
) -> list[Stamp]:
    """One stamp per signature-kind field, from the signer holding its role."""
    by_role: dict[str, SignatureSigner] = {}
    for signer in sorted(signers, key=lambda s: s.sign_order):
        by_role.setdefault((signer.role or "signer").strip() or "signer", signer)
    stamps: list[Stamp] = []
    for item in plan.signature_fields:
        signer = by_role.get(item.role or "")
        if signer is None:
            continue
        name = (signer.typed_signature or signer.name or "").strip()
        signed_at = signer.signed_at or datetime.now(timezone.utc)
        if item.kind == "date":
            text = _signed_date_text(signed_at, timezone_name)
            caption = None
        elif item.kind == "initials":
            text = initials_for(name)
            caption = None
        else:
            text = name
            caption = (
                "Signed electronically "
                f"{_as_aware_utc(signed_at).strftime('%Y-%m-%dT%H:%M:%SZ')} · "
                f"{str(request_id).replace('-', '')[:8]}"
            )
        if text:
            stamps.append(
                Stamp(
                    item.page,
                    item.rect,
                    item.kind,
                    text,
                    caption,
                    image=(
                        signer.drawn_signature_png if item.kind == "signature" else None
                    ),
                )
            )
    return stamps


def merged_field_values(signers: list[SignatureSigner]) -> dict[str, str]:
    values: dict[str, str] = {}
    for signer in sorted(signers, key=lambda s: s.sign_order):
        for key, value in (signer.field_values or {}).items():
            if value not in (None, "") or key not in values:
                values[key] = "" if value is None else str(value)
    return values


async def _store_signed(
    matter: Matter, *, filename: str, content: bytes, content_type: str
) -> StorageResult:
    # Token refresh can commit its session. Keep it separate from uncommitted
    # signer evidence so failed storage cannot persist half a signing action.
    async with async_session_maker() as storage_db:
        await set_tenant_context(storage_db, str(matter.tenant_id))
        result = await _file_store.store_matter_file_result(
            db=storage_db,
            tenant_id=str(matter.tenant_id),
            matter_slug=matter.slug,
            category="signed",
            filename=filename,
            content=content,
            content_type=content_type,
            matter_cloud_folder=matter.cloud_folder,
        )
    if not result.succeeded or not result.storage_path:
        raise CompletionStorageError(
            result.error or "Signed document storage is unavailable"
        )
    return result


async def _signed_document(
    db: AsyncSession,
    matter: Matter,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    description: str,
    storage: StorageResult,
) -> MatterDocument:
    document = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        uploaded_by_user_id=None,
        filename=filename,
        content_type=content_type,
        file_size=len(content),
        storage_path=storage.storage_path,
        storage_provider=storage.provider,
        storage_backend=storage.backend,
        provider_object_id=storage.provider_item_id,
        provider_drive_id=storage.drive_id,
        provider_parent_id=storage.parent_id,
        storage_error=storage.error,
        description=description,
        document_category="signed",
        document_sha256=hashlib.sha256(content).hexdigest(),
        folder_id=await autofile_folder_id(
            db,
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            document_category="signed",
        ),
        portal_visible=True,
    )
    db.add(document)
    return document


async def _read_source(
    db: AsyncSession, request: SignatureRequest
) -> tuple[MatterDocument | None, bytes | None]:
    """The exact bytes the request was bound to, or None if the row is gone.

    Read failures are raised: they mean storage is unavailable (retry) or the
    document changed under the request (staff must look), and neither should
    produce an executed copy of the wrong bytes.
    """
    if not request.document_id:
        return None, None
    source = await db.get(MatterDocument, request.document_id)
    if source is None or str(source.tenant_id) != str(request.tenant_id):
        return None, None
    content = await _file_store.read_matter_file_bytes(
        db=db,
        tenant_id=str(request.tenant_id),
        document=source,
        expected_sha256=request.source_document_sha256,
        expected_size=request.source_document_size,
    )
    return source, content


def _record_completion_failure(
    db: AsyncSession, request: SignatureRequest, matter: Matter, message: str
) -> None:
    """Note a filing failure for staff; the client's signature stays recorded."""
    first_failure = not request.completion_error
    request.completion_error = message[:2000]
    request.completion_attempted_at = datetime.now(timezone.utc)
    logger.warning(
        "Signature request %s could not be completed: %s", request.id, message
    )
    if first_failure:
        label = request.source_document_filename or "document"
        db.add(
            MatterEvent(
                tenant_id=matter.tenant_id,
                matter_id=matter.id,
                event_type="signature",
                title=STORAGE_FAILURE_EVENT_TITLE,
                content=(
                    f"Every party has signed {label}, but the executed copy could "
                    f"not be filed to the matter: {message} The signatures are "
                    "recorded and filing is retried automatically."
                ),
                note_type="system",
                created_by=request.created_by_user_id or matter.user_id,
            )
        )


async def _existing_completion(
    db: AsyncSession, request: SignatureRequest
) -> MatterDocument:
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


async def _executed_copy(
    db: AsyncSession,
    request: SignatureRequest,
    matter: Matter,
    *,
    source_document: MatterDocument | None,
    source: bytes | None,
    signers: list[SignatureSigner],
) -> tuple[MatterDocument | None, str | None]:
    """The executed document for this request, filing a rendered copy if needed.

    Returns ``(document, render_error)``. A rendering problem is not a storage
    outage: the request still completes on the strength of the certificate,
    and staff see why no executed copy exists.
    """
    if request.executed_document_id:
        existing = await db.get(MatterDocument, request.executed_document_id)
        if existing is not None:
            return existing, None
    if request.submitted_document_id and submission_accepted(request):
        uploaded = await db.get(MatterDocument, request.submitted_document_id)
        if uploaded is not None:
            return uploaded, None
        return None, "The accepted uploaded copy is no longer on the matter."
    if source is None:
        return None, "The source document is no longer on the matter."
    plan = build_plan(
        source,
        signers=signer_refs(signers),
        positioned_fields=request.positioned_fields or [],
    )
    if not plan.fill_supported:
        return None, plan.error or "The source PDF could not be read for rendering."
    from app.services.pdf_templates import TemplatePdfError

    timezone_name = await matter_timezone(db, request.tenant_id, request.matter_id)
    try:
        content = render_executed_pdf(
            source,
            field_values=merged_field_values(signers),
            stamps=stamps_for(
                plan, signers, request_id=str(request.id), timezone_name=timezone_name
            ),
        )
    except TemplatePdfError as exc:
        return None, f"The executed copy could not be rendered: {exc}"
    filename = executed_filename(
        (source_document.filename if source_document else None)
        or request.source_document_filename,
        str(request.id),
    )
    storage = await _store_signed(
        matter, filename=filename, content=content, content_type="application/pdf"
    )
    document = await _signed_document(
        db,
        matter,
        filename=filename,
        content=content,
        content_type="application/pdf",
        description="Executed copy: filled and signed in the client portal",
        storage=storage,
    )
    return document, None


async def complete_request_if_done(
    db: AsyncSession,
    request: SignatureRequest,
    matter: Matter,
) -> MatterDocument | None:
    """Finalize the request once every signer has signed.

    Returns the evidence certificate document when the request is (or already
    was) completed, else None: either signers are still pending, an uploaded
    copy awaits staff review, or filing failed and will be retried. Filing
    failures are recorded on the request and never raised. Caller commits.
    """
    if request.status == "completed":
        return await _existing_completion(db, request)
    signers = list(request.signers)
    if not signers or any(s.status != "signed" for s in signers):
        request.status = "partially_signed"
        return None
    # Every signer has signed: the request is no longer merely "sent", even
    # if filing below fails and completion has to wait for a retry.
    request.status = "partially_signed"
    if awaiting_review(request):
        return None
    try:
        return await _finalize(db, request, matter, signers)
    except (
        CompletionStorageError,
        MatterFileStoragePolicyError,
        MatterFileReadError,
        ProviderError,
    ) as exc:
        _record_completion_failure(db, request, matter, str(exc) or type(exc).__name__)
        return None
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        _record_completion_failure(db, request, matter, str(exc.detail))
        return None


async def _finalize(
    db: AsyncSession,
    request: SignatureRequest,
    matter: Matter,
    signers: list[SignatureSigner],
) -> MatterDocument:
    source_document, source = await _read_source(db, request)
    document_name = (
        source_document.filename if source_document else None
    ) or request.source_document_filename
    executed, render_error = await _executed_copy(
        db,
        request,
        matter,
        source_document=source_document,
        source=source,
        signers=signers,
    )
    if executed is not None:
        request.executed_document_id = executed.id
    ordered = sorted(signers, key=lambda row: row.sign_order)
    evidence_payload = {
        "request_id": str(request.id),
        "source_document_sha256": request.source_document_sha256,
        "positioned_fields": request.positioned_fields or [],
        "signers": [s.audit for s in ordered],
        "field_values": merged_field_values(ordered),
        "executed_document_sha256": executed.document_sha256 if executed else None,
    }
    evidence_sha256 = hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    content, _suggested_filename, content_type = build_certificate(
        matter_name=matter.matter_name,
        document_name=document_name or "document",
        signers=ordered,
        request_id=str(request.id),
        source_sha256=request.source_document_sha256,
        evidence_sha256=evidence_sha256,
        positioned_fields=request.positioned_fields or [],
        executed_sha256=executed.document_sha256 if executed else None,
    )
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    filename = immutable_certificate_filename(
        document_name=document_name or "document",
        request_id=str(request.id),
        artifact_sha256=artifact_sha256,
        content_type=content_type,
    )
    storage = await _store_signed(
        matter, filename=filename, content=content, content_type=content_type
    )
    # Bind the evidence hashes only once the bytes are durable. A failed upload
    # must leave the request byte-for-byte as the client's signature left it so
    # a retry cannot observe a half-completed certificate.
    request.evidence_sha256 = evidence_sha256
    request.completion_artifact_sha256 = artifact_sha256
    certificate = await _signed_document(
        db,
        matter,
        filename=filename,
        content=content,
        content_type=content_type,
        description=(
            "Signature evidence certificate; cryptographically bound to the "
            "source document and the executed copy"
        ),
        storage=storage,
    )

    executed_note = (
        f" Executed copy filed: {executed.filename}."
        if executed
        else f" No executed copy was filed: {render_error}"
        if render_error
        else ""
    )
    db.add(
        MatterEvent(
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            event_type="signature",
            title=f"Signing completed: {document_name or filename}",
            content=(
                "All parties signed in the client portal."
                f"{executed_note} Evidence certificate stored: {filename}; "
                f"source SHA-256 {request.source_document_sha256}."
            ),
            note_type="system",
            # A portal signer is not a firm user, so the timeline attributes the
            # completion to the staff member who requested the signature, and
            # otherwise to the matter's responsible user. The column is NOT NULL.
            created_by=request.created_by_user_id or matter.user_id,
        )
    )

    now = datetime.now(timezone.utc)
    request.status = "completed"
    request.completed_at = now
    request.completion_error = render_error
    request.completion_attempted_at = now
    # The certificate is the completion artifact the intake packet reconciles
    # against; the executed copy is referenced separately.
    request.provider_envelope_id = str(certificate.id)
    return certificate


async def after_completion(db: AsyncSession, request: SignatureRequest) -> None:
    """Close the chase task and let the intake packet see the signature."""
    from app.services import matter_intake

    await close_signature_followup(db, request, "Document signed")
    await db.flush()
    packet = await matter_intake.get_packet(
        db, request.tenant_id, request.matter_id, lock=True
    )
    if packet is not None:
        await matter_intake.reconcile(db, packet)


async def retry_pending_completions(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Finish requests whose signers all signed but whose filing failed.

    Runs inside the scheduler's tenant context. A request is retried at most
    once per ``COMPLETION_RETRY_INTERVAL`` so an outage does not turn into a
    storm of uploads. Returns the number of requests completed.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - COMPLETION_RETRY_INTERVAL
    pending_signer = (
        select(SignatureSigner.id)
        .where(
            SignatureSigner.request_id == SignatureRequest.id,
            SignatureSigner.status != "signed",
        )
        .exists()
    )
    any_signer = (
        select(SignatureSigner.id)
        .where(SignatureSigner.request_id == SignatureRequest.id)
        .exists()
    )
    rows = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.status.in_(OPEN_STATUSES),
            any_signer,
            ~pending_signer,
            (SignatureRequest.completion_attempted_at.is_(None))
            | (SignatureRequest.completion_attempted_at <= cutoff),
        )
        .order_by(SignatureRequest.created_at)
    )
    completed = 0
    for request in rows.scalars().unique():
        if awaiting_review(request):
            continue
        matter = await db.get(Matter, request.matter_id)
        if matter is None:
            continue
        try:
            await complete_request_if_done(db, request, matter)
            if request.status == "completed":
                await after_completion(db, request)
                completed += 1
            await db.commit()
        except Exception:
            logger.exception(
                "Retrying completion of signature request %s failed", request.id
            )
            await db.rollback()
    return completed


__all__ = [
    "COMPLETION_RETRY_INTERVAL",
    "CompletionStorageError",
    "accept_submission",
    "after_completion",
    "all_signed",
    "awaiting_review",
    "complete_request_if_done",
    "completion_pending",
    "decline_event",
    "executed_filename",
    "mark_request_expired_if_needed",
    "merged_field_values",
    "next_pending_signers",
    "record_portal_decline",
    "record_portal_signature",
    "record_uploaded_copy",
    "reject_submission",
    "request_is_expired",
    "retry_pending_completions",
    "signer_can_act_now",
    "stamps_for",
    "submission_accepted",
]
