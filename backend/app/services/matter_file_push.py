"""Attach externally authored matter artifacts pushed by Workspace MCP clients.

This is the counterpart to the generated-artifact lifecycle. That path exists
for Word work product a reviewer edits and approves; this one exists for the
evidence and correspondence that arrives alongside it — a screenshot, a scanned
exhibit, a saved email, an export the assistant produced.

Such a file is never work product the firm is approving, so it is not routed
through staff-then-attorney artifact review. It is attached to the matter as a
document that is explicitly not client-visible and carries an ``in_review``
status plus its MCP provenance, so a human decides what it is and whether the
client may ever see it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.schemas.workspace_mcp import MAX_DOCUMENT_BYTES, ProposeMatterFileArgs
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.matter_file_store import MatterFileStore

settings = get_settings()
matter_file_store = MatterFileStore()

#: Artifact kinds an assistant legitimately produces or forwards for a matter.
#: Every entry is inert data LawHand only ever stores and serves back: no
#: archives, no executables, no Office macro containers.
ALLOWED_MATTER_FILE_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".svg": "image/svg+xml",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".ics": "text/calendar",
    ".vcf": "text/vcard",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
}

#: Magic-byte prefixes for the formats whose container we can cheaply confirm.
#: A mismatch means the extension is lying about the bytes.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06"),
    ".xlsx": (b"PK\x03\x04", b"PK\x05\x06"),
    ".pptx": (b"PK\x03\x04", b"PK\x05\x06"),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".wav": (b"RIFF",),
}

#: An OLE compound file is the Outlook .msg container. It is also the legacy
#: Office/macro container, so it is accepted only where .msg expects it.
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


@dataclass(frozen=True, slots=True)
class PushedMatterFile:
    content: bytes
    filename: str
    extension: str
    content_type: str
    sha256: str


def _decoded(args: ProposeMatterFileArgs) -> bytes:
    try:
        content = base64.b64decode(args.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CapabilityError(
            "invalid_file_encoding", "content_base64 is not valid standard base64"
        ) from exc
    if not content:
        raise CapabilityError("empty_file", "The uploaded file is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise CapabilityError(
            "file_too_large",
            f"The uploaded file exceeds the {MAX_DOCUMENT_BYTES}-byte limit",
        )
    return content


def resolve_pushed_file(args: ProposeMatterFileArgs) -> PushedMatterFile:
    """Validate the bytes, the name, and that the two agree with each other."""

    filename = os.path.basename(args.filename).strip()
    extension = os.path.splitext(filename)[1].casefold()
    content_type = ALLOWED_MATTER_FILE_TYPES.get(extension)
    if content_type is None:
        raise CapabilityError(
            "unsupported_file_type",
            "Supported matter file types are: "
            + ", ".join(sorted(ALLOWED_MATTER_FILE_TYPES)),
        )

    content = _decoded(args)
    digest = hashlib.sha256(content).hexdigest()
    if args.content_sha256 and args.content_sha256 != digest:
        raise CapabilityError(
            "file_integrity_failed",
            "The uploaded file does not match content_sha256",
        )

    expected = _MAGIC_PREFIXES.get(extension)
    if expected and not content.startswith(expected):
        raise CapabilityError(
            "file_content_mismatch",
            f"The uploaded bytes are not a valid {extension} file",
        )
    if extension == ".msg" and not content.startswith(_OLE_MAGIC):
        raise CapabilityError(
            "file_content_mismatch", "The uploaded bytes are not a valid .msg file"
        )
    if extension != ".msg" and content.startswith(_OLE_MAGIC):
        # Legacy binary Office containers carry macros; they are never accepted
        # under a modern extension.
        raise CapabilityError(
            "file_content_mismatch",
            "Legacy binary Office files cannot be attached; save a modern format",
        )

    return PushedMatterFile(
        content=content,
        filename=filename,
        extension=extension,
        content_type=content_type,
        sha256=digest,
    )


def _derived_document_id(
    *, tenant_id: uuid.UUID, matter_id: uuid.UUID, client_request_id: uuid.UUID
) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"lawhand:matter-file:{tenant_id}:{matter_id}:{client_request_id}",
    )


async def _require_matter(context: CapabilityContext, matter_id: uuid.UUID) -> Matter:
    matter = await context.db.scalar(
        select(Matter).where(
            Matter.id == matter_id, Matter.tenant_id == context.tenant_id
        )
    )
    if matter is None:
        raise CapabilityError("matter_not_found", "Matter not found")
    return matter


def _description(args: ProposeMatterFileArgs, context: CapabilityContext) -> str:
    """Say where the file came from, since a reviewer did not create it."""

    origin = f"Attached by connected assistant {context.client_id or 'client'}"
    supplied = (args.description or "").strip()
    return (f"{supplied} ({origin})" if supplied else origin)[:500]


def _response(
    document: MatterDocument,
    pushed: PushedMatterFile,
    *,
    matter_id: uuid.UUID,
    created: bool,
) -> dict[str, Any]:
    backend = settings.BACKEND_URL.rstrip("/")
    return {
        "document_id": str(document.id),
        "matter_id": str(matter_id),
        "filename": document.filename,
        "content_type": document.content_type,
        "file_size": document.file_size,
        "document_sha256": pushed.sha256,
        "document_category": document.document_category,
        "document_status": document.document_status,
        "storage_backend": document.storage_backend,
        "storage_error": document.storage_error,
        "portal_visible": bool(document.portal_visible),
        "created": created,
        "idempotent_replay": not created,
        "document_open_url": (f"/api/matters/{matter_id}/documents/{document.id}/open"),
        "document_download_url": (
            f"/api/matters/{matter_id}/documents/{document.id}/download"
        ),
        "document_absolute_open_url": (
            f"{backend}/api/matters/{matter_id}/documents/{document.id}/open"
        ),
        "document_absolute_download_url": (
            f"{backend}/api/matters/{matter_id}/documents/{document.id}/download"
        ),
        "approval_effect": (
            "The file is attached to the matter and is not visible in the client "
            "portal. A LawHand user decides its category and whether it is ever "
            "released. Attaching it neither approves nor delivers anything."
        ),
    }


async def push_matter_file(
    context: CapabilityContext, args: ProposeMatterFileArgs
) -> dict[str, Any]:
    """Store a pushed artifact in the matter's cloud folder and record it."""

    pushed = resolve_pushed_file(args)
    matter = await _require_matter(context, args.matter_id)

    client_request_id = args.client_request_id
    if client_request_id is None and context.idempotency_key:
        client_request_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lawhand:{context.tenant_id}:{context.channel}:"
            f"{context.idempotency_key.strip()}",
        )
    document_id = (
        _derived_document_id(
            tenant_id=context.tenant_id,
            matter_id=args.matter_id,
            client_request_id=client_request_id,
        )
        if client_request_id is not None
        else uuid.uuid4()
    )

    existing = await context.db.scalar(
        select(MatterDocument).where(
            MatterDocument.id == document_id,
            MatterDocument.tenant_id == context.tenant_id,
            MatterDocument.matter_id == args.matter_id,
        )
    )
    if existing is not None:
        if existing.document_sha256 != pushed.sha256:
            raise CapabilityError(
                "idempotency_conflict",
                "A different file already exists for this client_request_id",
            )
        return _response(existing, pushed, matter_id=args.matter_id, created=False)

    tenant_settings = await context.db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == context.tenant_id)
    )
    category = args.document_category or "general"
    try:
        storage = await matter_file_store.store_matter_file_result(
            db=context.db,
            tenant_id=str(context.tenant_id),
            matter_slug=matter.slug,
            category=category,
            filename=pushed.filename,
            content=pushed.content,
            content_type=pushed.content_type,
            matter_cloud_folder=matter.cloud_folder,
            preferred_provider=(
                tenant_settings.primary_cloud_provider if tenant_settings else None
            ),
        )
    except Exception as exc:  # noqa: BLE001 - provider errors are not the caller's
        raise CapabilityError(
            "matter_file_storage_failed",
            "The file could not be stored in the matter's document storage",
        ) from exc

    document = MatterDocument(
        id=document_id,
        tenant_id=context.tenant_id,
        matter_id=args.matter_id,
        uploaded_by_user_id=context.actor_user_id,
        filename=pushed.filename,
        content_type=pushed.content_type,
        file_size=len(pushed.content),
        document_sha256=pushed.sha256,
        storage_path=storage.storage_path,
        storage_provider=storage.provider,
        storage_backend=storage.backend,
        provider_object_id=storage.provider_item_id,
        provider_drive_id=storage.drive_id,
        provider_parent_id=storage.parent_id,
        provider_etag=storage.provider_etag,
        provider_version_id=storage.provider_version_id,
        provider_checksum=storage.provider_checksum,
        storage_error=storage.error,
        description=_description(args, context),
        document_category=category,
        document_role="attachment",
        # Not firm work product a reviewer approves, but not silently accepted
        # either: a human still triages what this is.
        document_status="in_review",
        portal_visible=False,
    )
    context.db.add(document)
    await context.db.flush()
    return _response(document, pushed, matter_id=args.matter_id, created=True)


__all__ = [
    "ALLOWED_MATTER_FILE_TYPES",
    "push_matter_file",
    "resolve_pushed_file",
]
