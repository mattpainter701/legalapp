"""Recording and reading immutable document-template versions.

Every version number names the exact state stored in its immutable row.  The
mutable template row is an authoring convenience; ``current_version_no`` is
only advanced after that resulting state has been snapshotted here.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_template import DocumentTemplate
from app.models.document_template_version import DocumentTemplateVersion

#: Fields whose change is worth a new version. Purely presentational edits
#: (description, jurisdiction tags) still update the template but do not
#: manufacture history that says nothing.
VERSIONED_FIELDS = (
    "title",
    "body",
    "variable_schema",
    "format",
    "category",
    "source_sha256",
    "is_active",
)


def body_sha256(body: str | None) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def snapshot_differs(template: DocumentTemplate, updates: dict) -> bool:
    """Return whether ``updates`` would change anything a version records."""

    return any(
        key in updates and updates[key] != getattr(template, key, None)
        for key in VERSIONED_FIELDS
    )


async def record_version(
    db: AsyncSession,
    *,
    template: DocumentTemplate,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    change_summary: str | None = None,
) -> DocumentTemplateVersion:
    """Append the template's current state as the next version.

    Call this *after* applying an edit (but before committing): the immutable
    row and ``current_version_no`` then identify the same exact state.

    The next number is read from the stored counter rather than counted, so
    concurrent edits under the row lock the caller already holds cannot mint
    the same number twice.
    """

    next_number = int(template.current_version_no or 0) + 1
    if next_number == 1:
        # A template that predates versioning may already have rows if its
        # counter was never advanced; keep numbering monotonic regardless.
        highest = await db.scalar(
            select(func.max(DocumentTemplateVersion.version_no)).where(
                DocumentTemplateVersion.tenant_id == tenant_id,
                DocumentTemplateVersion.template_id == template.id,
            )
        )
        next_number = int(highest or 0) + 1

    version = DocumentTemplateVersion(
        tenant_id=tenant_id,
        template_id=template.id,
        version_no=next_number,
        title=template.title,
        body=template.body or "",
        body_sha256=body_sha256(template.body),
        variable_schema=template.variable_schema,
        format=template.format,
        category=template.category,
        source_sha256=template.source_sha256,
        source_filename=template.source_filename,
        is_active=bool(template.is_active),
        change_summary=(change_summary or None),
        created_by_user_id=user_id,
    )
    db.add(version)
    template.current_version_no = next_number
    await db.flush()
    return version


async def list_versions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DocumentTemplateVersion], int]:
    """Return a page of versions, newest first, with the total count."""

    total = await db.scalar(
        select(func.count(DocumentTemplateVersion.id)).where(
            DocumentTemplateVersion.tenant_id == tenant_id,
            DocumentTemplateVersion.template_id == template_id,
        )
    )
    result = await db.execute(
        select(DocumentTemplateVersion)
        .where(
            DocumentTemplateVersion.tenant_id == tenant_id,
            DocumentTemplateVersion.template_id == template_id,
        )
        .order_by(DocumentTemplateVersion.version_no.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), int(total or 0)


async def get_version(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    version_no: int,
) -> DocumentTemplateVersion | None:
    return await db.scalar(
        select(DocumentTemplateVersion).where(
            DocumentTemplateVersion.tenant_id == tenant_id,
            DocumentTemplateVersion.template_id == template_id,
            DocumentTemplateVersion.version_no == version_no,
        )
    )
