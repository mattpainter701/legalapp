"""Security and persistence helpers for inbound matter email."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.inbound_email import InboundEmail
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.services.matter_file_store import MatterFileStore, StorageResult

settings = get_settings()
logger = logging.getLogger(__name__)
matter_file_store = MatterFileStore()

ALIAS_LOCAL_PART_RE = re.compile(r"^m-[a-z2-7]{26}$")
MAX_HEADER_VALUE_CHARS = 2_000
MAX_PREVIEW_CHARS = 4_000


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def generate_alias_local_part() -> str:
    """Return a lowercase 128-bit opaque local part suitable for email."""
    token = base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
    return f"m-{token.lower()}"


def alias_lookup_hash(local_part: str) -> str:
    return hashlib.sha256(local_part.strip().lower().encode("ascii")).hexdigest()


def delivery_signature_payload(
    timestamp: str, envelope_sender: str, recipient: str, raw_message: bytes
) -> bytes:
    body_sha256 = hashlib.sha256(raw_message).hexdigest()
    return (
        f"v1:{timestamp}\n{envelope_sender.strip().lower()}\n"
        f"{recipient.strip().lower()}\n{body_sha256}"
    ).encode("utf-8")


def delivery_signature(
    secret: str,
    timestamp: str,
    envelope_sender: str,
    recipient: str,
    raw_message: bytes,
) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        delivery_signature_payload(timestamp, envelope_sender, recipient, raw_message),
        hashlib.sha256,
    ).hexdigest()
    return f"v1={digest}"


def verify_delivery_signature(
    *,
    supplied_signature: str,
    secret: str,
    timestamp: str,
    envelope_sender: str,
    recipient: str,
    raw_message: bytes,
    now: datetime | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify the Worker HMAC and reject stale/replayed timestamp windows."""
    if not supplied_signature or not secret or not timestamp:
        return False
    try:
        delivered_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    current = now or datetime.now(timezone.utc)
    if abs((current - delivered_at).total_seconds()) > tolerance_seconds:
        return False
    expected = delivery_signature(
        secret, timestamp, envelope_sender, recipient, raw_message
    )
    return hmac.compare_digest(expected, supplied_signature.strip().lower())


def _decoded_text(part: Message) -> str:
    try:
        value = part.get_content()
        if isinstance(value, str):
            return value
    except Exception:
        pass
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _message_preview(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_decoded_text(part))
        elif content_type == "text/html":
            html.append(_decoded_text(part))
    text = "\n".join(plain).strip()
    if not text and html:
        extractor = _HTMLTextExtractor()
        extractor.feed("\n".join(html))
        text = " ".join(extractor.parts)
    return re.sub(r"\s+", " ", text).strip()[:MAX_PREVIEW_CHARS]


def _header_addresses(message: Message, name: str) -> list[str]:
    values = [str(v) for v in message.get_all(name, [])]
    addresses = []
    for _, address in getaddresses(values):
        normalized = address.strip().lower()
        if normalized and normalized not in addresses:
            addresses.append(normalized[:320])
    return addresses


def parse_raw_email(raw_message: bytes) -> dict:
    """Parse bounded metadata for review while retaining the original bytes."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    from_addresses = _header_addresses(message, "from")
    occurred_at = datetime.now(timezone.utc)
    date_header = message.get("date")
    if date_header:
        try:
            parsed = parsedate_to_datetime(str(date_header))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            occurred_at = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass

    auth_values = [
        str(v)[:MAX_HEADER_VALUE_CHARS]
        for v in message.get_all("authentication-results", [])
    ]
    received_spf = str(message.get("received-spf") or "")[:MAX_HEADER_VALUE_CHARS]
    return {
        "subject": str(message.get("subject") or "(no subject)")[:500],
        "body_preview": _message_preview(message) or None,
        "participants": {
            "from": from_addresses[0] if from_addresses else "",
            "to": _header_addresses(message, "to"),
            "cc": _header_addresses(message, "cc"),
        },
        "authentication_results": {
            "authentication_results": auth_values,
            "received_spf": received_spf or None,
        },
        "message_id": str(message.get("message-id") or "")[:500] or None,
        "occurred_at": occurred_at,
    }


def quarantine_path(tenant_id: uuid.UUID, inbound_id: uuid.UUID) -> Path:
    root = (Path(settings.UPLOAD_DIR) / str(tenant_id) / "inbound-email").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{inbound_id}.eml"


def write_quarantined_message(path: Path, raw_message: bytes) -> None:
    """Create a private file without following or overwriting an existing path."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(raw_message)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_quarantined_message(item: InboundEmail) -> bytes:
    if not item.raw_storage_path:
        raise FileNotFoundError("Inbound email content is no longer available")
    root = (Path(settings.UPLOAD_DIR) / str(item.tenant_id) / "inbound-email").resolve()
    path = Path(item.raw_storage_path).resolve()
    if path.parent != root or path.suffix.lower() != ".eml":
        raise PermissionError("Inbound email path is outside tenant quarantine")
    content = path.read_bytes()
    if len(content) != item.raw_size:
        raise ValueError("Inbound email size does not match its audit record")
    if hashlib.sha256(content).hexdigest() != item.message_sha256:
        raise ValueError("Inbound email content failed integrity verification")
    return content


def remove_quarantined_message(item: InboundEmail) -> None:
    if not item.raw_storage_path:
        return
    root = (Path(settings.UPLOAD_DIR) / str(item.tenant_id) / "inbound-email").resolve()
    path = Path(item.raw_storage_path).resolve()
    if path.parent != root or path.suffix.lower() != ".eml":
        raise PermissionError("Inbound email path is outside tenant quarantine")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def inbound_filename(item: InboundEmail) -> str:
    safe_subject = re.sub(r"[^a-zA-Z0-9]+", "-", item.subject).strip("-").lower()
    safe_subject = safe_subject[:60] or "no-subject"
    return f"{item.occurred_at:%Y-%m-%d}_{safe_subject}_{str(item.id)[:8]}.eml"


async def file_inbound_email(
    db: AsyncSession,
    *,
    item: InboundEmail,
    matter: Matter,
    reviewed_by_user_id: uuid.UUID,
) -> CommunicationLog:
    """Move a reviewed message into matter storage and create its log entry."""
    tenant_id = item.tenant_id
    raw_message = read_quarantined_message(item)
    settings_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == item.tenant_id)
    )
    tenant_settings = settings_result.scalar_one_or_none()
    storage_result: StorageResult | None = None
    try:
        storage_result = await matter_file_store.store_matter_file_result(
            db=db,
            tenant_id=str(item.tenant_id),
            matter_slug=matter.slug,
            category="correspondence",
            filename=inbound_filename(item),
            content=raw_message,
            content_type="message/rfc822",
            matter_cloud_folder=matter.cloud_folder,
            preferred_provider=(
                tenant_settings.primary_cloud_provider if tenant_settings else None
            ),
        )
        document = MatterDocument(
            tenant_id=item.tenant_id,
            matter_id=matter.id,
            uploaded_by_user_id=reviewed_by_user_id,
            filename=inbound_filename(item),
            content_type="message/rfc822",
            file_size=len(raw_message),
            storage_path=storage_result.storage_path,
            storage_provider=storage_result.provider,
            storage_backend=storage_result.backend,
            provider_object_id=storage_result.provider_item_id,
            provider_drive_id=storage_result.drive_id,
            provider_parent_id=storage_result.parent_id,
            storage_error=storage_result.error,
            description=f"Inbound email: {item.subject[:400]}",
            document_category="correspondence",
        )
        db.add(document)
        await db.flush()

        communication = CommunicationLog(
            tenant_id=item.tenant_id,
            direction="inbound",
            channel="email",
            status="received",
            subject=item.subject,
            body=item.body_preview,
            matter_id=matter.id,
            created_by_user_id=reviewed_by_user_id,
            occurred_at=item.occurred_at,
            external_ref=f"inbound-email:{item.id}",
            document_id=document.id,
            thread_ref=item.provider_message_id,
            participants=item.participants,
        )
        db.add(communication)
        await db.flush()
        item.status = "accepted"
        item.reviewed_by_user_id = reviewed_by_user_id
        item.reviewed_at = datetime.now(timezone.utc)
        item.communication_log_id = communication.id
        await db.commit()
    except Exception:
        await db.rollback()
        if storage_result is not None:
            try:
                await set_tenant_context(db, str(tenant_id))
                await matter_file_store.delete_stored_result(
                    db=db,
                    tenant_id=str(tenant_id),
                    result=storage_result,
                )
            except Exception:
                logger.exception("Could not clean up staged inbound matter email")
        raise

    try:
        remove_quarantined_message(item)
        item.raw_storage_path = None
        await set_tenant_context(db, str(tenant_id))
        await db.commit()
    except Exception:
        logger.exception("Could not remove accepted inbound email quarantine copy")
        await db.rollback()
    return communication
