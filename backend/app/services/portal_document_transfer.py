"""Paths and explicitly shared upload links for matter document transfers."""

from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.models.matter_document_folder import MatterDocumentFolder
from app.models.plugin import MatterEvent
from app.services.matter_document_organization import create_folder
from app.services.matter_import_manifest import safe_path

PORTAL_UPLOAD_LINK_KEY = "portal_upload_link"


async def shared_upload_link(db, *, tenant_id, matter_id):
    event = await db.scalar(
        select(MatterEvent)
        .where(
            MatterEvent.tenant_id == tenant_id,
            MatterEvent.matter_id == matter_id,
            MatterEvent.event_type == PORTAL_UPLOAD_LINK_KEY,
        )
        .order_by(MatterEvent.created_at.desc(), MatterEvent.id.desc())
        .limit(1)
    )
    return upload_link((event.metadata_json or {}).get("url")) if event else None


def upload_link(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if (
        len(value) > 2000
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(ord(c) < 32 or c.isspace() for c in value)
        or "\\" in value
    ):
        raise ValueError("Use an HTTPS sharing link without embedded credentials.")
    return value


def upload_path(value: str | None, filename: str) -> str | None:
    if not value:
        return None
    path = safe_path(value)
    if len(path.split("/")) > 9:
        raise ValueError("Client uploads support at most eight source folder levels.")
    if path.split("/")[-1] != filename:
        raise ValueError("The source path must end with the uploaded filename.")
    return path


async def destination_folder(db, *, tenant_id, matter_id, root, path):
    """Keep client folders underneath this matter's protected upload folder."""
    parent = root
    for name in (path or "").split("/")[:-1]:
        existing = await db.scalar(
            select(MatterDocumentFolder).where(
                MatterDocumentFolder.tenant_id == tenant_id,
                MatterDocumentFolder.matter_id == matter_id,
                MatterDocumentFolder.parent_id == parent.id,
                func.lower(MatterDocumentFolder.name) == name.lower(),
            )
        )
        parent = existing or await create_folder(
            db,
            tenant_id=tenant_id,
            matter_id=matter_id,
            parent_id=parent.id,
            name=name,
        )
    return parent
