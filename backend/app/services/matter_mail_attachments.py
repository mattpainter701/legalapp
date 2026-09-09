"""Review and deliver bounded matter documents using their exact content digest."""

import hashlib
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from app.models.matter_document import MatterDocument
from app.database import async_session_maker, set_tenant_context
from app.services.mail_attachment import MailAttachment, MAX_ATTACHMENT_BYTES
from app.services.matter_file_store import MatterFileStore
from app.services.matter_document_revisions import (
    assert_no_legacy_assistant_derivative_release,
    DocumentRevisionServiceError,
)


async def reviewed_attachment(db, tenant_id, matter_id, document_id, digest=None):
    try:
        document_id = uuid.UUID(str(document_id))
    except ValueError as exc:
        raise HTTPException(422, "Invalid document ID") from exc
    doc = await db.scalar(
        select(MatterDocument).where(
            MatterDocument.id == document_id,
            MatterDocument.tenant_id == tenant_id,
            MatterDocument.matter_id == matter_id,
        )
    )
    if doc is None:
        raise HTTPException(404, "Matter document not found")
    try:
        await assert_no_legacy_assistant_derivative_release(
            db, tenant_id=tenant_id, matter_id=matter_id, document_id=document_id
        )
    except DocumentRevisionServiceError as exc:
        raise HTTPException(
            409, "This document requires its separate release workflow"
        ) from exc
    filename, content_type = (
        doc.filename,
        doc.content_type or "application/octet-stream",
    )
    try:
        async with async_session_maker() as storage_db:
            await set_tenant_context(storage_db, str(tenant_id))
            content = await MatterFileStore().read_matter_file_bytes(
                db=storage_db,
                tenant_id=str(tenant_id),
                document=doc,
                max_bytes=MAX_ATTACHMENT_BYTES,
            )
        attachment = MailAttachment(
            filename=filename, content=content, content_type=content_type
        )
    except Exception as exc:
        raise HTTPException(
            422, "The attachment could not be read or exceeds the 2 MiB limit"
        ) from exc
    current = hashlib.sha256(content).hexdigest()
    if digest is not None and digest != current:
        raise HTTPException(
            412, "A document changed after review. Preview it again before sending."
        )
    return attachment, current


async def collect_reviewed_attachments(db, tenant_id, matter_id, selections):
    if not isinstance(selections, list) or len(selections) > 10:
        raise HTTPException(422, "Choose at most ten attachments")
    result, seen = [], set()
    for item in selections:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
        ):
            raise HTTPException(422, "Preview each attachment before sending")
        identifier = item.get("document_id")
        if not isinstance(identifier, str):
            raise HTTPException(422, "Invalid document ID")
        if identifier in seen:
            raise HTTPException(422, "Duplicate attachment")
        seen.add(identifier)
        attachment, _ = await reviewed_attachment(
            db, tenant_id, matter_id, identifier, item["sha256"]
        )
        result.append(attachment)
        if sum(len(value.content) for value in result) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "Attachments must total at most 2 MiB")
    return result
