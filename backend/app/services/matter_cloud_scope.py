"""Tenant-and-matter-bound cloud references for sync and retrieval.

Matter document folders are product folders.  A cloud upload can create an
additional provider folder for one of those paths, so its durable provider
parent ID belongs on the document record rather than in the provisioned matter
folder binding.  This module exposes that bounded set of durable references to
the consumers which need to find the uploaded file again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_document import MatterDocument


MAX_MATTER_DOCUMENT_CLOUD_REFS = 50

_INDEX_KEY_BY_BACKEND = {
    "google_drive": ("google", "file"),
    "onedrive": ("microsoft", "file"),
    "sharepoint": ("microsoft", "sharepoint_file"),
}


@dataclass
class MatterCloudDocumentScope:
    """Provider IDs from durable documents that the selected matter owns."""

    folder_ids: dict[str, list[str]] = field(default_factory=dict)
    object_ids: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    sharepoint_folder_refs: list[tuple[str, str]] = field(default_factory=list)


def _document_backend(
    storage_provider: str | None, storage_backend: str | None
) -> str | None:
    """Normalize persisted storage labels without instantiating ORM documents."""
    aliases = {
        "google": "google_drive",
        "gdrive": "google_drive",
        "drive": "google_drive",
        "microsoft": "onedrive",
        "microsoft_graph": "onedrive",
        "ms_graph": "onedrive",
        "one_drive": "onedrive",
        "share_point": "sharepoint",
    }
    for value in (storage_backend, storage_provider):
        normalized = str(value or "").strip().lower().replace("-", "_")
        normalized = aliases.get(normalized, normalized)
        if normalized in _INDEX_KEY_BY_BACKEND:
            return normalized
    return None


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


async def load_matter_document_cloud_scope(
    db: AsyncSession,
    *,
    tenant_id: str,
    matter_id: str | None,
) -> MatterCloudDocumentScope:
    """Load up to ``MAX_MATTER_DOCUMENT_CLOUD_REFS`` cloud references.

    Both tenant and matter predicates are deliberate authorization boundaries.
    The cap keeps a matter sync and a retrieval fallback bounded even for a
    large historic matter.
    """
    scope = MatterCloudDocumentScope()
    if not matter_id:
        return scope
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
        matter_uuid = uuid.UUID(str(matter_id))
    except (TypeError, ValueError):
        return scope

    stmt = (
        select(
            MatterDocument.storage_provider,
            MatterDocument._storage_backend,
            MatterDocument.provider_object_id,
            MatterDocument.provider_parent_id,
            MatterDocument.provider_drive_id,
        )
        .where(
            MatterDocument.tenant_id == tenant_uuid,
            MatterDocument.matter_id == matter_uuid,
            MatterDocument.provider_object_id.is_not(None),
        )
        .order_by(MatterDocument.updated_at.desc())
        .limit(MAX_MATTER_DOCUMENT_CLOUD_REFS)
    )
    rows = (await db.execute(stmt)).all()
    for storage_provider, storage_backend, object_id, parent_id, drive_id in rows:
        backend = _document_backend(storage_provider, storage_backend)
        index_key = _INDEX_KEY_BY_BACKEND.get(backend or "")
        if not backend or not index_key:
            continue

        object_id = str(object_id or "").strip() or None
        parent_id = str(parent_id or "").strip() or None
        if not object_id:
            continue
        if backend == "sharepoint":
            # The sync/index identity includes the document-library drive. A
            # raw SharePoint item ID is not safe to use across drives.
            if not drive_id:
                continue
            object_id = f"{drive_id}:{object_id}"
        _append_unique(scope.object_ids.setdefault(index_key, []), object_id)
        if not parent_id:
            continue
        if backend in {"google_drive", "onedrive"}:
            _append_unique(scope.folder_ids.setdefault(backend, []), parent_id)
        elif backend == "sharepoint":
            ref = (str(drive_id), parent_id)
            if ref not in scope.sharepoint_folder_refs:
                scope.sharepoint_folder_refs.append(ref)
    return scope
