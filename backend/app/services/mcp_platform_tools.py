"""Platform-native MCP tools (document pipeline).

Unlike the read-only CourtListener research tools (proxied to the sidecar),
these tools execute in the main backend against tenant data: matters, tasks,
and documents. They let an external AI client (BYO Claude/GPT/OpenCode) create
and list matter documents through the MCP product surface.

All tools enforce tenant isolation via the resolved product key's tenant and
honor the same quota/burst/metering path as research tools.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import TenantSettings
from app.services.matter_file_store import MatterFileStore

PLATFORM_TOOL_NAMES: list[str] = [
    "list_matters",
    "list_matter_documents",
    "create_document",
]

_matter_file_store = MatterFileStore()
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(title: str, extension: str) -> str:
    base = _SLUG_RE.sub("-", title.strip())[:120].strip("-.") or "document"
    return f"{base}.{extension}"


def _requested_filename(filename: str | None, title: str) -> str:
    """Reject paths and header-breaking characters in MCP document filenames."""
    if not filename:
        return _safe_filename(title, "md")
    if (
        len(filename) > 255
        or filename in {".", ".."}
        or any(char in filename for char in '\\/:*?"<>|\r\n\x00')
    ):
        raise HTTPException(
            status_code=400, detail="filename must be a single file name"
        )
    return filename


def _storage_category(category: str) -> str:
    """Normalize a caller-controlled category into one safe storage segment."""
    normalized = _SLUG_RE.sub("-", category).strip("-.")[:100]
    return normalized or "generated"


def platform_tool_definitions() -> list[dict[str, Any]]:
    """Manifest entries for platform tools (merged into the MCP catalog)."""
    return [
        {
            "name": "list_matters",
            "description": (
                "List the tenant's matters (id, name, status) so you can "
                "reference matter_id in create_document. Optionally filter "
                "by a case-insensitive substring of the matter name."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional substring filter on matter name",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matters to return (1-50, default 25)",
                    },
                },
            },
        },
        {
            "name": "list_matter_documents",
            "description": (
                "List documents attached to a matter (id, filename, category, "
                "size, created_at). Use to check what already exists before "
                "creating new documents."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "matter_id": {
                        "type": "string",
                        "description": "UUID of the matter",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max documents to return (1-100, default 50)",
                    },
                },
                "required": ["matter_id"],
            },
        },
        {
            "name": "create_document",
            "description": (
                "Create a markdown document on a matter, optionally linked to "
                "a task. The document is stored in the tenant's configured "
                "file store (cloud or local) and appears in the matter's "
                "document list. Returns the document id and download path."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "matter_id": {
                        "type": "string",
                        "description": "UUID of the matter to attach the document to",
                    },
                    "title": {
                        "type": "string",
                        "description": "Document title (used for the filename)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full document content in Markdown",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Optional UUID of a task in the matter",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional explicit filename (defaults to title)",
                    },
                    "document_category": {
                        "type": "string",
                        "description": (
                            "Category folder (e.g. generated, correspondence, "
                            "pleading). Defaults to 'generated'."
                        ),
                    },
                },
                "required": ["matter_id", "title", "content"],
            },
        },
    ]


async def execute_platform_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Execute a platform-native MCP tool. Returns the tool result payload."""
    if name == "list_matters":
        return await _list_matters(arguments, db=db, tenant_id=tenant_id)
    if name == "list_matter_documents":
        return await _list_matter_documents(arguments, db=db, tenant_id=tenant_id)
    if name == "create_document":
        return await _create_document(
            arguments, db=db, tenant_id=tenant_id, user_id=user_id
        )
    raise HTTPException(status_code=400, detail=f"Unknown platform tool: {name}")


async def _list_matters(
    arguments: dict[str, Any], *, db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    limit = min(max(int(arguments.get("limit") or 25), 1), 50)
    query = (arguments.get("query") or "").strip()

    stmt = select(Matter).where(Matter.tenant_id == tenant_id)
    if query:
        stmt = stmt.where(Matter.matter_name.ilike(f"%{query}%"))
    stmt = stmt.order_by(Matter.updated_at.desc()).limit(limit)

    result = await db.execute(stmt)
    matters = result.scalars().all()
    return {
        "content": [
            {
                "type": "json",
                "json": {
                    "matters": [
                        {
                            "id": str(m.id),
                            "name": m.matter_name,
                            "status": getattr(m, "status", None),
                        }
                        for m in matters
                    ],
                    "count": len(matters),
                },
            }
        ]
    }


async def _list_matter_documents(
    arguments: dict[str, Any], *, db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    matter_id = arguments.get("matter_id")
    if not matter_id:
        raise HTTPException(status_code=400, detail="matter_id is required")

    limit = min(max(int(arguments.get("limit") or 50), 1), 100)

    matter = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    if matter.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    result = await db.execute(
        select(MatterDocument)
        .where(
            MatterDocument.matter_id == matter_id,
            MatterDocument.tenant_id == tenant_id,
        )
        .order_by(MatterDocument.created_at.desc())
        .limit(limit)
    )
    docs = result.scalars().all()
    return {
        "content": [
            {
                "type": "json",
                "json": {
                    "documents": [
                        {
                            "id": str(d.id),
                            "filename": d.filename,
                            "category": d.document_category,
                            "file_size": d.file_size,
                            "task_id": str(d.task_id) if d.task_id else None,
                            "created_at": d.created_at.isoformat()
                            if d.created_at
                            else None,
                        }
                        for d in docs
                    ],
                    "count": len(docs),
                },
            }
        ]
    }


async def _create_document(
    arguments: dict[str, Any],
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    matter_id = arguments.get("matter_id")
    title = (arguments.get("title") or "").strip()
    content = arguments.get("content") or ""
    task_id = arguments.get("task_id")
    filename_arg = (arguments.get("filename") or "").strip()
    category = _storage_category(
        (arguments.get("document_category") or "generated").strip()
    )

    if not matter_id:
        raise HTTPException(status_code=400, detail="matter_id is required")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    matter_result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = matter_result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    if task_id:
        task_result = await db.execute(
            select(Task).where(
                Task.id == task_id,
                Task.matter_id == matter_id,
                Task.tenant_id == tenant_id,
            )
        )
        if task_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=400, detail="task_id does not belong to this matter"
            )

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    preferred_provider = ts.primary_cloud_provider if ts else None

    filename = _requested_filename(filename_arg, title)
    content_bytes = content.encode("utf-8")

    storage_result = await _matter_file_store.store_matter_file_result(
        db=db,
        tenant_id=str(tenant_id),
        matter_slug=matter.slug,
        category=category,
        filename=filename,
        content=content_bytes,
        content_type="text/markdown",
        matter_cloud_folder=matter.cloud_folder,
        preferred_provider=preferred_provider,
    )

    doc = MatterDocument(
        tenant_id=tenant_id,
        matter_id=matter.id,
        task_id=uuid.UUID(task_id) if task_id else None,
        uploaded_by_user_id=user_id,
        filename=filename,
        content_type="text/markdown",
        file_size=len(content_bytes),
        document_category=category,
        storage_path=storage_result.storage_path,
        storage_provider=storage_result.provider,
        storage_backend=storage_result.backend,
        provider_object_id=storage_result.provider_item_id,
        provider_drive_id=storage_result.drive_id,
        provider_parent_id=storage_result.parent_id,
        storage_error=storage_result.error,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return {
        "content": [
            {
                "type": "json",
                "json": {
                    "document_id": str(doc.id),
                    "matter_id": str(matter.id),
                    "task_id": task_id,
                    "filename": filename,
                    "file_size": len(content_bytes),
                    "document_category": category,
                    "download_url": (
                        f"/api/matters/{matter.id}/documents/{doc.id}/download"
                    ),
                },
            }
        ]
    }
