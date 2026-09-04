"""Folder-tree and tag operations for a matter's case documents.

The router keeps HTTP concerns; every rule about what a valid tree looks like —
sibling name collisions, depth, cycles, protected system folders, and keeping
the materialized ``path`` consistent after a rename or move — lives here so the
firm UI, the client portal, and any future importer all enforce the same shape.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_document import MatterDocument
from app.models.matter_document_folder import (
    MAX_FOLDER_DEPTH,
    MAX_FOLDER_NAME_LENGTH,
    MatterDocumentFolder,
)
from app.models.matter_document_tag import (
    DEFAULT_TAG_COLOR,
    MAX_TAG_NAME_LENGTH,
    TAG_COLORS,
    MatterDocumentTag,
    MatterDocumentTagLink,
)

# System folders the product owns. Their names are protected from rename and
# delete so integrations (the client portal, most importantly) keep a stable
# destination for the files they file automatically.
SYSTEM_FOLDER_CLIENT_UPLOADS = "client_uploads"
SYSTEM_FOLDER_NAMES = {SYSTEM_FOLDER_CLIENT_UPLOADS: "Client Uploads"}

# Control characters and the provider-hostile set; slashes are already barred by
# a table check constraint but are repeated here for a friendly error message.
_INVALID_NAME_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')
_RESERVED_NAMES = {".", ".."}

MAX_TAGS_PER_DOCUMENT = 25


class DocumentOrganizationError(RuntimeError):
    """An API-safe organization error with an explicit HTTP disposition."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# ── Names ────────────────────────────────────────────────────────────────────


def normalize_folder_name(raw: str | None) -> str:
    """Return a storable folder name or raise with a user-facing reason."""
    name = " ".join((raw or "").split())
    if not name:
        raise DocumentOrganizationError(
            400, "folder_name_required", "Folder name is required."
        )
    if len(name) > MAX_FOLDER_NAME_LENGTH:
        raise DocumentOrganizationError(
            400,
            "folder_name_too_long",
            f"Folder name is too long ({MAX_FOLDER_NAME_LENGTH} characters maximum).",
        )
    if _INVALID_NAME_CHARS.search(name) or name in _RESERVED_NAMES:
        raise DocumentOrganizationError(
            400,
            "folder_name_invalid",
            'Folder names cannot contain / \\ : * ? " < > | characters.',
        )
    # A trailing dot or space is legal in Postgres but is silently rewritten by
    # Windows and by OneDrive, which would desynchronize the cloud mirror.
    if name != name.strip(" ."):
        raise DocumentOrganizationError(
            400,
            "folder_name_invalid",
            "Folder names cannot start or end with a dot or a space.",
        )
    return name


def normalize_tag_name(raw: str | None) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise DocumentOrganizationError(
            400, "tag_name_required", "Tag name is required."
        )
    if len(name) > MAX_TAG_NAME_LENGTH:
        raise DocumentOrganizationError(
            400,
            "tag_name_too_long",
            f"Tag name is too long ({MAX_TAG_NAME_LENGTH} characters maximum).",
        )
    return name


def normalize_tag_color(raw: str | None) -> str:
    color = (raw or DEFAULT_TAG_COLOR).strip().lower()
    if color not in TAG_COLORS:
        raise DocumentOrganizationError(
            400,
            "tag_color_invalid",
            f"Tag color must be one of: {', '.join(TAG_COLORS)}.",
        )
    return color


# ── Folder reads ─────────────────────────────────────────────────────────────


async def get_folder_or_404(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    folder_id: uuid.UUID,
) -> MatterDocumentFolder:
    result = await db.execute(
        select(MatterDocumentFolder).where(
            MatterDocumentFolder.id == folder_id,
            MatterDocumentFolder.tenant_id == tenant_id,
            MatterDocumentFolder.matter_id == matter_id,
        )
    )
    folder = result.scalar_one_or_none()
    if folder is None:
        raise DocumentOrganizationError(404, "folder_not_found", "Folder not found.")
    return folder


async def list_folders(
    db: AsyncSession, *, tenant_id: uuid.UUID, matter_id: uuid.UUID
) -> list[MatterDocumentFolder]:
    """Every folder in the matter, ordered so a client can render a tree."""
    result = await db.execute(
        select(MatterDocumentFolder)
        .where(
            MatterDocumentFolder.tenant_id == tenant_id,
            MatterDocumentFolder.matter_id == matter_id,
        )
        .order_by(
            MatterDocumentFolder.depth,
            func.lower(MatterDocumentFolder.path),
        )
    )
    return list(result.scalars().all())


async def folder_document_counts(
    db: AsyncSession, *, tenant_id: uuid.UUID, matter_id: uuid.UUID
) -> dict[uuid.UUID | None, int]:
    """Direct (non-recursive) document count per folder; ``None`` is the root."""
    result = await db.execute(
        select(MatterDocument.folder_id, func.count())
        .where(
            MatterDocument.tenant_id == tenant_id,
            MatterDocument.matter_id == matter_id,
        )
        .group_by(MatterDocument.folder_id)
    )
    return {row[0]: row[1] for row in result.all()}


def _subtree_predicate(folder: MatterDocumentFolder):
    """Match the folder and everything beneath it via its materialized path."""
    prefix = f"{folder.path}/"
    return or_(
        MatterDocumentFolder.id == folder.id,
        MatterDocumentFolder.path.startswith(prefix),
    )


async def subtree_folders(
    db: AsyncSession, folder: MatterDocumentFolder
) -> list[MatterDocumentFolder]:
    result = await db.execute(
        select(MatterDocumentFolder).where(
            MatterDocumentFolder.tenant_id == folder.tenant_id,
            MatterDocumentFolder.matter_id == folder.matter_id,
            _subtree_predicate(folder),
        )
    )
    return list(result.scalars().all())


async def descendant_folder_ids(
    db: AsyncSession, folder: MatterDocumentFolder
) -> list[uuid.UUID]:
    """Folder ids for the whole subtree, including the folder itself."""
    return [node.id for node in await subtree_folders(db, folder)]


# ── Folder writes ────────────────────────────────────────────────────────────


async def _assert_sibling_name_free(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(MatterDocumentFolder.id).where(
        MatterDocumentFolder.tenant_id == tenant_id,
        MatterDocumentFolder.matter_id == matter_id,
        func.lower(MatterDocumentFolder.name) == name.lower(),
    )
    stmt = stmt.where(
        MatterDocumentFolder.parent_id == parent_id
        if parent_id is not None
        else MatterDocumentFolder.parent_id.is_(None)
    )
    if exclude_id is not None:
        stmt = stmt.where(MatterDocumentFolder.id != exclude_id)
    if (await db.execute(stmt)).first() is not None:
        raise DocumentOrganizationError(
            409,
            "folder_name_taken",
            f'A folder named "{name}" already exists here.',
        )


async def create_folder(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    name: str,
    parent_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
    kind: str = "user",
    system_key: str | None = None,
) -> MatterDocumentFolder:
    clean_name = normalize_folder_name(name)

    parent: MatterDocumentFolder | None = None
    if parent_id is not None:
        parent = await get_folder_or_404(
            db, tenant_id=tenant_id, matter_id=matter_id, folder_id=parent_id
        )
        if parent.depth + 1 > MAX_FOLDER_DEPTH:
            raise DocumentOrganizationError(
                400,
                "folder_depth_exceeded",
                f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep.",
            )

    await _assert_sibling_name_free(
        db,
        tenant_id=tenant_id,
        matter_id=matter_id,
        parent_id=parent_id,
        name=clean_name,
    )

    folder = MatterDocumentFolder(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter_id,
        parent_id=parent_id,
        name=clean_name,
        path=f"{parent.path}/{clean_name}" if parent else clean_name,
        depth=(parent.depth + 1) if parent else 0,
        kind=kind,
        system_key=system_key,
        created_by_user_id=created_by_user_id,
    )
    db.add(folder)
    await db.flush()
    return folder


async def _repath_subtree(
    db: AsyncSession,
    folder: MatterDocumentFolder,
    *,
    old_path: str,
    old_depth: int,
) -> None:
    """Rewrite descendants after ``folder`` itself has a new path and depth."""
    if folder.path == old_path and folder.depth == old_depth:
        return
    prefix = f"{old_path}/"
    result = await db.execute(
        select(MatterDocumentFolder).where(
            MatterDocumentFolder.tenant_id == folder.tenant_id,
            MatterDocumentFolder.matter_id == folder.matter_id,
            MatterDocumentFolder.id != folder.id,
            MatterDocumentFolder.path.startswith(prefix),
        )
    )
    depth_delta = folder.depth - old_depth
    for node in result.scalars().all():
        node.path = f"{folder.path}/{node.path[len(prefix):]}"
        node.depth = node.depth + depth_delta
        if node.depth > MAX_FOLDER_DEPTH:
            raise DocumentOrganizationError(
                400,
                "folder_depth_exceeded",
                f"That move would nest folders more than {MAX_FOLDER_DEPTH} "
                "levels deep.",
            )


async def rename_folder(
    db: AsyncSession, folder: MatterDocumentFolder, *, name: str
) -> MatterDocumentFolder:
    if folder.kind == "system":
        raise DocumentOrganizationError(
            409,
            "folder_is_system",
            f'"{folder.name}" is managed by LawHand and cannot be renamed.',
        )
    clean_name = normalize_folder_name(name)
    if clean_name == folder.name:
        return folder

    await _assert_sibling_name_free(
        db,
        tenant_id=folder.tenant_id,
        matter_id=folder.matter_id,
        parent_id=folder.parent_id,
        name=clean_name,
        exclude_id=folder.id,
    )

    old_path, old_depth = folder.path, folder.depth
    folder.name = clean_name
    parent_path = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
    folder.path = f"{parent_path}/{clean_name}" if parent_path else clean_name
    await _repath_subtree(db, folder, old_path=old_path, old_depth=old_depth)
    await db.flush()
    return folder


async def move_folder(
    db: AsyncSession,
    folder: MatterDocumentFolder,
    *,
    new_parent_id: uuid.UUID | None,
) -> MatterDocumentFolder:
    if folder.kind == "system":
        raise DocumentOrganizationError(
            409,
            "folder_is_system",
            f'"{folder.name}" is managed by LawHand and cannot be moved.',
        )
    if new_parent_id == folder.id:
        raise DocumentOrganizationError(
            400, "folder_move_into_self", "A folder cannot be moved into itself."
        )

    parent: MatterDocumentFolder | None = None
    if new_parent_id is not None:
        parent = await get_folder_or_404(
            db,
            tenant_id=folder.tenant_id,
            matter_id=folder.matter_id,
            folder_id=new_parent_id,
        )
        # Path containment is the cycle check: a descendant's path always
        # begins with the folder's own path.
        if parent.path == folder.path or parent.path.startswith(f"{folder.path}/"):
            raise DocumentOrganizationError(
                400,
                "folder_move_into_descendant",
                "A folder cannot be moved into one of its own subfolders.",
            )

    if folder.parent_id == new_parent_id:
        return folder

    await _assert_sibling_name_free(
        db,
        tenant_id=folder.tenant_id,
        matter_id=folder.matter_id,
        parent_id=new_parent_id,
        name=folder.name,
        exclude_id=folder.id,
    )

    old_path, old_depth = folder.path, folder.depth
    folder.parent_id = new_parent_id
    folder.path = f"{parent.path}/{folder.name}" if parent else folder.name
    folder.depth = (parent.depth + 1) if parent else 0
    if folder.depth > MAX_FOLDER_DEPTH:
        raise DocumentOrganizationError(
            400,
            "folder_depth_exceeded",
            f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep.",
        )
    await _repath_subtree(db, folder, old_path=old_path, old_depth=old_depth)
    await db.flush()
    return folder


async def delete_folder(
    db: AsyncSession,
    folder: MatterDocumentFolder,
    *,
    move_documents_to_parent: bool,
) -> int:
    """Delete a folder subtree. Returns how many documents were re-filed.

    Documents are never deleted with their folder. Either the subtree must
    already be empty, or the caller opts in to re-filing its documents into
    this folder's parent.
    """
    if folder.kind == "system":
        raise DocumentOrganizationError(
            409,
            "folder_is_system",
            f'"{folder.name}" is managed by LawHand and cannot be deleted.',
        )

    subtree_ids = await descendant_folder_ids(db, folder)

    # A system folder anywhere inside the subtree keeps its guarantee too.
    # Checked before anything is written, so a refused delete never leaves
    # re-filed documents behind for a caller that commits anyway.
    protected = await db.execute(
        select(MatterDocumentFolder.name).where(
            MatterDocumentFolder.tenant_id == folder.tenant_id,
            MatterDocumentFolder.id.in_(subtree_ids),
            MatterDocumentFolder.kind == "system",
        )
    )
    protected_name = protected.scalars().first()
    if protected_name:
        raise DocumentOrganizationError(
            409,
            "folder_is_system",
            f'"{protected_name}" is managed by LawHand and cannot be deleted.',
        )

    count_result = await db.execute(
        select(func.count())
        .select_from(MatterDocument)
        .where(
            MatterDocument.tenant_id == folder.tenant_id,
            MatterDocument.folder_id.in_(subtree_ids),
        )
    )
    document_count = int(count_result.scalar() or 0)

    if document_count and not move_documents_to_parent:
        raise DocumentOrganizationError(
            409,
            "folder_not_empty",
            f"That folder still holds {document_count} document"
            f"{'' if document_count == 1 else 's'}. Move them out first, or "
            "confirm moving them to the parent folder.",
        )

    if document_count:
        result = await db.execute(
            select(MatterDocument).where(
                MatterDocument.tenant_id == folder.tenant_id,
                MatterDocument.folder_id.in_(subtree_ids),
            )
        )
        for document in result.scalars().all():
            document.folder_id = folder.parent_id
        # The folder FK is RESTRICT, so the re-filing must reach the database
        # before the DELETE below.
        await db.flush()

    await db.execute(
        delete(MatterDocumentFolder).where(
            MatterDocumentFolder.tenant_id == folder.tenant_id,
            MatterDocumentFolder.id.in_(subtree_ids),
        )
    )
    await db.flush()
    return document_count


def storage_routing_for_folder(
    folder: MatterDocumentFolder | None,
) -> tuple[str | None, list[str] | None]:
    """Return ``(category_override, folder_path)`` for storing into a folder.

    A system folder is the explorer's name for a subfolder the matter's cloud
    share was already provisioned with (``client_uploads``), so files filed
    there keep routing to that canonical folder instead of creating a
    second, differently-cased copy beside it. A user folder is mirrored by
    path, hanging off the matter folder itself.
    """
    if folder is None:
        return None, None
    if folder.kind == "system" and folder.system_key:
        return folder.system_key, None
    return None, folder.path_segments


async def ensure_system_folder(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    system_key: str,
) -> MatterDocumentFolder:
    """Return the matter's system folder for ``system_key``, creating it once.

    Used by the client portal so customer uploads land somewhere predictable
    instead of at the root of a firm's explorer.
    """
    display_name = SYSTEM_FOLDER_NAMES.get(system_key)
    if not display_name:
        raise DocumentOrganizationError(
            400, "unknown_system_folder", f"Unknown system folder: {system_key}"
        )

    result = await db.execute(
        select(MatterDocumentFolder).where(
            MatterDocumentFolder.tenant_id == tenant_id,
            MatterDocumentFolder.matter_id == matter_id,
            MatterDocumentFolder.system_key == system_key,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    # A firm may already have hand-made a folder with the same name at the
    # root; adopt it rather than colliding on the sibling-name index.
    result = await db.execute(
        select(MatterDocumentFolder).where(
            MatterDocumentFolder.tenant_id == tenant_id,
            MatterDocumentFolder.matter_id == matter_id,
            MatterDocumentFolder.parent_id.is_(None),
            func.lower(MatterDocumentFolder.name) == display_name.lower(),
        )
    )
    adopted = result.scalar_one_or_none()
    if adopted is not None:
        adopted.kind = "system"
        adopted.system_key = system_key
        await db.flush()
        return adopted

    return await create_folder(
        db,
        tenant_id=tenant_id,
        matter_id=matter_id,
        name=display_name,
        kind="system",
        system_key=system_key,
    )


# ── Tags ─────────────────────────────────────────────────────────────────────


async def list_tags(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[MatterDocumentTag]:
    result = await db.execute(
        select(MatterDocumentTag)
        .where(MatterDocumentTag.tenant_id == tenant_id)
        .order_by(func.lower(MatterDocumentTag.name))
    )
    return list(result.scalars().all())


async def get_tag_or_404(
    db: AsyncSession, *, tenant_id: uuid.UUID, tag_id: uuid.UUID
) -> MatterDocumentTag:
    result = await db.execute(
        select(MatterDocumentTag).where(
            MatterDocumentTag.id == tag_id,
            MatterDocumentTag.tenant_id == tenant_id,
        )
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise DocumentOrganizationError(404, "tag_not_found", "Tag not found.")
    return tag


async def _assert_tag_name_free(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(MatterDocumentTag.id).where(
        MatterDocumentTag.tenant_id == tenant_id,
        func.lower(MatterDocumentTag.name) == name.lower(),
    )
    if exclude_id is not None:
        stmt = stmt.where(MatterDocumentTag.id != exclude_id)
    if (await db.execute(stmt)).first() is not None:
        raise DocumentOrganizationError(
            409, "tag_name_taken", f'A tag named "{name}" already exists.'
        )


async def create_tag(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    color: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> MatterDocumentTag:
    clean_name = normalize_tag_name(name)
    clean_color = normalize_tag_color(color)
    await _assert_tag_name_free(db, tenant_id=tenant_id, name=clean_name)
    tag = MatterDocumentTag(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=clean_name,
        color=clean_color,
        created_by_user_id=created_by_user_id,
    )
    db.add(tag)
    await db.flush()
    return tag


async def update_tag(
    db: AsyncSession,
    tag: MatterDocumentTag,
    *,
    name: str | None = None,
    color: str | None = None,
) -> MatterDocumentTag:
    if name is not None:
        clean_name = normalize_tag_name(name)
        await _assert_tag_name_free(
            db, tenant_id=tag.tenant_id, name=clean_name, exclude_id=tag.id
        )
        tag.name = clean_name
    if color is not None:
        tag.color = normalize_tag_color(color)
    await db.flush()
    return tag


async def tags_for_documents(
    db: AsyncSession, *, tenant_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[MatterDocumentTag]]:
    """Tags per document, ordered by name, for a page of documents."""
    if not document_ids:
        return {}
    result = await db.execute(
        select(MatterDocumentTagLink.document_id, MatterDocumentTag)
        .join(
            MatterDocumentTag,
            MatterDocumentTag.id == MatterDocumentTagLink.tag_id,
        )
        .where(
            MatterDocumentTagLink.tenant_id == tenant_id,
            MatterDocumentTagLink.document_id.in_(document_ids),
        )
        .order_by(func.lower(MatterDocumentTag.name))
    )
    grouped: dict[uuid.UUID, list[MatterDocumentTag]] = defaultdict(list)
    for document_id, tag in result.all():
        grouped[document_id].append(tag)
    return dict(grouped)


async def set_document_tags(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    document: MatterDocument,
    tag_ids: list[uuid.UUID],
    actor_user_id: uuid.UUID | None = None,
) -> list[MatterDocumentTag]:
    """Replace a document's tags with exactly ``tag_ids``."""
    requested = list(dict.fromkeys(tag_ids))
    if len(requested) > MAX_TAGS_PER_DOCUMENT:
        raise DocumentOrganizationError(
            400,
            "too_many_tags",
            f"A document can carry at most {MAX_TAGS_PER_DOCUMENT} tags.",
        )

    resolved: list[MatterDocumentTag] = []
    if requested:
        result = await db.execute(
            select(MatterDocumentTag).where(
                MatterDocumentTag.tenant_id == tenant_id,
                MatterDocumentTag.id.in_(requested),
            )
        )
        resolved = list(result.scalars().all())
        if len(resolved) != len(requested):
            raise DocumentOrganizationError(
                404, "tag_not_found", "One or more tags no longer exist."
            )

    await db.execute(
        delete(MatterDocumentTagLink).where(
            MatterDocumentTagLink.tenant_id == tenant_id,
            MatterDocumentTagLink.document_id == document.id,
        )
    )
    for tag in resolved:
        db.add(
            MatterDocumentTagLink(
                tenant_id=tenant_id,
                document_id=document.id,
                tag_id=tag.id,
                created_by_user_id=actor_user_id,
            )
        )
    await db.flush()
    resolved.sort(key=lambda tag: tag.name.lower())
    return resolved
