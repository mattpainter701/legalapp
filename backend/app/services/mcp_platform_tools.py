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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import TenantSettings
from app.services.matter_file_store import MatterFileStore
from app.utils.sql_filters import escape_like

PLATFORM_TOOL_NAMES: list[str] = [
    "list_matters",
    "list_matter_documents",
    "create_document",
]

_matter_file_store = MatterFileStore()
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# The nginx transport locations cap an MCP body at 256 KiB. Bounding the
# document body well inside that keeps the failure a readable 400 from this
# module rather than a 413 the caller cannot attribute to a field.
#
# The bound is on encoded bytes, not characters, because that is what the
# transport measures: 70,000 emoji satisfy a 200,000-character limit and still
# encode to ~280 KB, and ordinary accented or CJK legal text costs 2-3 bytes a
# character. A character ceiling can never exceed the byte ceiling in UTF-8, so
# max_length stays as the cheap first check.
MAX_DOCUMENT_CONTENT_BYTES = 200_000
MAX_DOCUMENT_CONTENT_CHARS = MAX_DOCUMENT_CONTENT_BYTES


class _PlatformToolArgs(BaseModel):
    """Base contract for platform-tool arguments.

    ``extra="forbid"`` matters here for the same reason it does on the chat
    action models: these arguments are authored by an external language model,
    and an invented argument should fail loudly rather than be dropped.
    """

    model_config = ConfigDict(extra="forbid")


class ListMattersArgs(_PlatformToolArgs):
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=25, ge=1, le=50)


class ListMatterDocumentsArgs(_PlatformToolArgs):
    matter_id: uuid.UUID
    limit: int = Field(default=50, ge=1, le=100)


class CreateDocumentArgs(_PlatformToolArgs):
    matter_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=MAX_DOCUMENT_CONTENT_CHARS)
    task_id: uuid.UUID | None = None
    filename: str | None = Field(default=None, max_length=255)
    document_category: str = Field(default="generated", max_length=100)

    @field_validator("content")
    @classmethod
    def bound_encoded_size(cls, value: str) -> str:
        """Measure the body the way the transport does, in encoded bytes."""
        if len(value.encode("utf-8")) > MAX_DOCUMENT_CONTENT_BYTES:
            raise ValueError(
                f"must be at most {MAX_DOCUMENT_CONTENT_BYTES} bytes when "
                "UTF-8 encoded"
            )
        return value


_TOOL_ARG_MODELS: dict[str, type[_PlatformToolArgs]] = {
    "list_matters": ListMattersArgs,
    "list_matter_documents": ListMatterDocumentsArgs,
    "create_document": CreateDocumentArgs,
}


def _describe_validation_error(exc: ValidationError) -> str:
    """Name the offending field and why it failed, bounded to one short line.

    The message reaches an external MCP client, so it stays specific enough for
    a model to correct itself on the next call and short enough not to become a
    channel for echoing arbitrary input back.
    """
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "arguments"
    return f"{location}: {str(first.get('msg', 'is invalid'))[:200]}"


def parse_platform_tool_args(name: str, arguments: dict[str, Any]) -> _PlatformToolArgs:
    """Validate raw MCP tool arguments into a typed model, or raise HTTP 400.

    These arguments arrive from an external MCP client and are shaped by a
    language model, so a matter *name* where a UUID belongs is an ordinary
    mistake rather than an attack. Without this, such a value reached a UUID
    column comparison and surfaced as a 500 that also skipped usage metering,
    because the dispatcher only meters ``HTTPException``.
    """
    model = _TOOL_ARG_MODELS.get(name)
    if model is None:
        raise HTTPException(status_code=400, detail=f"Unknown platform tool: {name}")
    if not isinstance(arguments, dict):
        raise HTTPException(
            status_code=400, detail="Tool arguments must be a JSON object"
        )
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid arguments for {name} — {_describe_validation_error(exc)}",
        ) from exc


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
                        "maxLength": 200,
                        "description": "Optional substring filter on matter name",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Max matters to return (1-50, default 25)",
                    },
                },
                # The runtime models use extra="forbid"; say so in the advertised
                # contract too, so the protocol path's jsonschema and any
                # schema-driven client reject an invented argument here rather
                # than letting it through to a 400 further in.
                "additionalProperties": False,
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
                        "format": "uuid",
                        "description": (
                            "UUID of the matter. Call list_matters first to "
                            "resolve a matter name into its id."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Max documents to return (1-100, default 50)",
                    },
                },
                "required": ["matter_id"],
                "additionalProperties": False,
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
                        "format": "uuid",
                        "description": (
                            "UUID of the matter to attach the document to. Call "
                            "list_matters first to resolve a name into its id."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 300,
                        "description": "Document title (used for the filename)",
                    },
                    "content": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_DOCUMENT_CONTENT_CHARS,
                        "description": (
                            "Full document content in Markdown, at most "
                            f"{MAX_DOCUMENT_CONTENT_BYTES} bytes UTF-8 encoded"
                        ),
                    },
                    "task_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Optional UUID of a task in the matter",
                    },
                    "filename": {
                        "type": "string",
                        "maxLength": 255,
                        "description": "Optional explicit filename (defaults to title)",
                    },
                    "document_category": {
                        "type": "string",
                        "maxLength": 100,
                        "description": (
                            "Category folder (e.g. generated, correspondence, "
                            "pleading). Defaults to 'generated'."
                        ),
                    },
                },
                "required": ["matter_id", "title", "content"],
                "additionalProperties": False,
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
    args = parse_platform_tool_args(name, arguments)
    if isinstance(args, ListMattersArgs):
        return await _list_matters(args, db=db, tenant_id=tenant_id)
    if isinstance(args, ListMatterDocumentsArgs):
        return await _list_matter_documents(args, db=db, tenant_id=tenant_id)
    return await _create_document(args, db=db, tenant_id=tenant_id, user_id=user_id)


async def _list_matters(
    args: ListMattersArgs, *, db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    query = (args.query or "").strip()

    stmt = select(Matter).where(Matter.tenant_id == tenant_id)
    if query:
        stmt = stmt.where(
            Matter.matter_name.ilike(f"%{escape_like(query)}%", escape="\\")
        )
    stmt = stmt.order_by(Matter.updated_at.desc()).limit(args.limit)

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
    args: ListMatterDocumentsArgs, *, db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    matter_id = args.matter_id

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
        .limit(args.limit)
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
    args: CreateDocumentArgs,
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    matter_id = args.matter_id
    title = args.title.strip()
    content = args.content
    task_id = args.task_id
    filename_arg = (args.filename or "").strip()
    category = _storage_category(args.document_category.strip())

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
        task_id=task_id,
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
                    "task_id": str(task_id) if task_id else None,
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
