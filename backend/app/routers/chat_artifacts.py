"""Router for AI-generated chat artifacts (document work products).

Artifacts are markdown documents produced by the assistant during a chat
conversation. They can be iteratively edited, saved to a matter (optionally
linked to a task) as a MatterDocument, and exported as markdown/PDF/DOCX.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.chat_artifact import ChatArtifact
from app.models.conversation import Conversation
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import TenantSettings
from app.schemas.chat_artifact import (
    ChatArtifactCreate,
    ChatArtifactListResponse,
    ChatArtifactResponse,
    ChatArtifactUpdate,
    ExportArtifactRequest,
    SaveArtifactToMatterRequest,
    SaveArtifactToMatterResponse,
)
from app.services.document_export import (
    markdown_to_docx_bytes,
    markdown_to_pdf_bytes,
)
from app.services.matter_file_store import MatterFileStore

router = APIRouter(prefix="/conversations", tags=["chat-artifacts"])
matter_file_store = MatterFileStore()
logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(title: str, extension: str) -> str:
    base = _SLUG_RE.sub("-", title.strip())[:120].strip("-.") or "artifact"
    return f"{base}.{extension}"


def _requested_filename(
    filename: str | None, title: str, *, extension: str = "md"
) -> str:
    """Validate a user supplied filename before handing it to a storage backend."""
    if not filename or not filename.strip():
        return _safe_filename(title, extension)

    candidate = filename.strip()
    if (
        len(candidate) > 255
        or candidate in {".", ".."}
        or any(char in candidate for char in '\\/:*?"<>|\r\n\x00')
    ):
        raise HTTPException(
            status_code=422, detail="Filename must be a single file name"
        )
    return candidate


async def _get_matter_or_404(
    matter_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> Matter:
    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _get_conversation_or_404(
    conversation_id: str, user, db: AsyncSession
) -> Conversation:
    """Conversations are private to their creator (matches chat.py)."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


async def _get_artifact_or_404(
    artifact_id: str, conversation_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> ChatArtifact:
    result = await db.execute(
        select(ChatArtifact).where(
            ChatArtifact.id == artifact_id,
            ChatArtifact.conversation_id == conversation_id,
            ChatArtifact.tenant_id == tenant_id,
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


async def _validate_task_in_matter(
    task_id: str, matter_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> None:
    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.matter_id == matter_id,
            Task.tenant_id == tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=400, detail="Task does not belong to the given matter"
        )


@router.get(
    "/{conversation_id}/artifacts",
    response_model=ChatArtifactListResponse,
)
async def list_artifacts(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)

    total_result = await db.execute(
        select(func.count(ChatArtifact.id)).where(
            ChatArtifact.conversation_id == conversation_id,
            ChatArtifact.tenant_id == user.tenant_id,
        )
    )
    total = total_result.scalar_one()

    result = await db.execute(
        select(ChatArtifact)
        .where(
            ChatArtifact.conversation_id == conversation_id,
            ChatArtifact.tenant_id == user.tenant_id,
        )
        .order_by(ChatArtifact.created_at.desc())
    )
    items = result.scalars().all()
    return ChatArtifactListResponse(items=items, total=total)


@router.post(
    "/{conversation_id}/artifacts",
    response_model=ChatArtifactResponse,
    status_code=201,
)
async def create_artifact(
    conversation_id: str,
    payload: ChatArtifactCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)

    if payload.task_id and not payload.matter_id:
        raise HTTPException(status_code=400, detail="task_id requires matter_id")
    if payload.matter_id:
        await _get_matter_or_404(str(payload.matter_id), user.tenant_id, db)
    if payload.task_id:
        await _validate_task_in_matter(
            str(payload.task_id), str(payload.matter_id), user.tenant_id, db
        )

    artifact = ChatArtifact(
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        created_by_user_id=user.id,
        title=payload.title.strip(),
        content=payload.content,
        format=payload.format,
        matter_id=payload.matter_id,
        task_id=payload.task_id,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


@router.get(
    "/{conversation_id}/artifacts/{artifact_id}",
    response_model=ChatArtifactResponse,
)
async def get_artifact(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)
    return await _get_artifact_or_404(artifact_id, conversation_id, user.tenant_id, db)


@router.patch(
    "/{conversation_id}/artifacts/{artifact_id}",
    response_model=ChatArtifactResponse,
)
async def update_artifact(
    conversation_id: str,
    artifact_id: str,
    payload: ChatArtifactUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)
    artifact = await _get_artifact_or_404(
        artifact_id, conversation_id, user.tenant_id, db
    )

    if artifact.saved_to_matter and any(
        field in payload.model_fields_set
        for field in ("content", "matter_id", "task_id")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Artifact already saved to matter; create a new artifact version "
                "before changing its content or destination"
            ),
        )

    provided_fields = payload.model_fields_set
    effective_matter_id = (
        payload.matter_id if "matter_id" in provided_fields else artifact.matter_id
    )
    effective_task_id = (
        payload.task_id if "task_id" in provided_fields else artifact.task_id
    )

    if effective_task_id and not effective_matter_id:
        raise HTTPException(status_code=400, detail="task_id requires matter_id")
    if effective_matter_id:
        await _get_matter_or_404(str(effective_matter_id), user.tenant_id, db)
    if effective_task_id:
        await _validate_task_in_matter(
            str(effective_task_id),
            str(effective_matter_id),
            user.tenant_id,
            db,
        )

    if payload.title is not None:
        artifact.title = payload.title.strip()
    if payload.content is not None:
        artifact.content = payload.content
        artifact.version += 1
    if "matter_id" in provided_fields:
        artifact.matter_id = payload.matter_id
    if "task_id" in provided_fields:
        artifact.task_id = payload.task_id

    artifact.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(artifact)
    return artifact


@router.delete(
    "/{conversation_id}/artifacts/{artifact_id}",
    status_code=204,
)
async def delete_artifact(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)
    artifact = await _get_artifact_or_404(
        artifact_id, conversation_id, user.tenant_id, db
    )
    await db.delete(artifact)
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/{conversation_id}/artifacts/{artifact_id}/save",
    response_model=SaveArtifactToMatterResponse,
)
async def save_artifact_to_matter(
    conversation_id: str,
    artifact_id: str,
    payload: SaveArtifactToMatterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Persist the artifact as a MatterDocument (optionally linked to a task)."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id
    user_id = user.id
    await set_tenant_context(db, str(tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)
    artifact = await _get_artifact_or_404(
        artifact_id, conversation_id, user.tenant_id, db
    )
    if artifact.saved_to_matter:
        raise HTTPException(
            status_code=409,
            detail="Artifact already saved to matter",
        )

    matter = await _get_matter_or_404(str(payload.matter_id), user.tenant_id, db)

    if payload.task_id:
        await _validate_task_in_matter(
            str(payload.task_id), str(payload.matter_id), user.tenant_id, db
        )

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    preferred_provider = ts.primary_cloud_provider if ts else None

    filename = _requested_filename(payload.filename, artifact.title)
    document_category = payload.document_category.strip()
    content_bytes = artifact.content.encode("utf-8")

    storage_result = await matter_file_store.store_matter_file_result(
        db=db,
        tenant_id=str(user.tenant_id),
        matter_slug=matter.slug,
        category=document_category,
        filename=filename,
        content=content_bytes,
        content_type="text/markdown",
        matter_cloud_folder=matter.cloud_folder,
        preferred_provider=preferred_provider,
    )

    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        task_id=payload.task_id,
        uploaded_by_user_id=user_id,
        filename=filename,
        content_type="text/markdown",
        file_size=len(content_bytes),
        document_category=document_category,
        storage_path=storage_result.storage_path,
        storage_provider=storage_result.provider,
        storage_backend=storage_result.backend,
        provider_object_id=storage_result.provider_item_id,
        provider_drive_id=storage_result.drive_id,
        provider_parent_id=storage_result.parent_id,
        storage_error=storage_result.error,
    )
    db.add(doc)
    # The artifact stores the new document ID but there is no ORM relationship
    # for SQLAlchemy to use when ordering the INSERT and UPDATE. Flush the
    # document first so its FK target exists before updating the artifact.
    await db.flush([doc])

    artifact.saved_to_matter = True
    artifact.saved_document_id = doc.id
    artifact.matter_id = matter.id
    artifact.task_id = payload.task_id
    artifact.updated_at = datetime.now(timezone.utc)

    try:
        await db.commit()
        await db.refresh(doc)
    except Exception as exc:
        await db.rollback()
        try:
            await set_tenant_context(db, str(tenant_id))
            await matter_file_store.delete_stored_result(
                db=db,
                tenant_id=str(tenant_id),
                result=storage_result,
            )
        except Exception:
            logger.exception(
                "Failed to compensate staged artifact file after database failure"
            )
        raise HTTPException(
            status_code=500,
            detail="Document metadata could not be saved; the operation was rolled back",
        ) from exc

    return SaveArtifactToMatterResponse(
        artifact_id=artifact.id,
        document_id=doc.id,
        matter_id=matter.id,
        task_id=payload.task_id,
        filename=filename,
        download_url=f"/api/matters/{matter.id}/documents/{doc.id}/download",
        storage_backend=storage_result.backend,
        storage_warning=storage_result.error,
    )


@router.post("/{conversation_id}/artifacts/{artifact_id}/export")
async def export_artifact(
    conversation_id: str,
    artifact_id: str,
    payload: ExportArtifactRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Export the artifact as markdown, PDF, or DOCX."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_conversation_or_404(conversation_id, user, db)
    artifact = await _get_artifact_or_404(
        artifact_id, conversation_id, user.tenant_id, db
    )

    if payload.format == "markdown":
        filename = _requested_filename(payload.filename, artifact.title, extension="md")
        return PlainTextResponse(
            content=artifact.content,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if payload.format == "pdf":
        pdf_bytes = markdown_to_pdf_bytes(artifact.content, title=artifact.title)
        filename = _requested_filename(
            payload.filename, artifact.title, extension="pdf"
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if payload.format == "docx":
        docx_bytes = markdown_to_docx_bytes(artifact.content, title=artifact.title)
        filename = _requested_filename(
            payload.filename, artifact.title, extension="docx"
        )
        return Response(
            content=docx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    raise HTTPException(status_code=400, detail="Unsupported export format")
