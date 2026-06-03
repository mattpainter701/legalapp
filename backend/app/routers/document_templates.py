"""
Document Templates router — CRUD + variable substitution rendering.

  GET    /api/templates              list active templates
  POST   /api/templates              create template
  GET    /api/templates/{id}         get template detail
  PATCH  /api/templates/{id}         update template fields
  DELETE /api/templates/{id}         delete template
  POST   /api/templates/{id}/render  render template with variables
"""

import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.document_template import DocumentTemplate
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.schemas.document_template import (
    CATEGORIES,
    DocumentTemplateCreate,
    DocumentTemplateListResponse,
    DocumentTemplateRenderRequest,
    DocumentTemplateRenderResponse,
    DocumentTemplateResponse,
    DocumentTemplateUpdate,
)

router = APIRouter(prefix="/api/templates", tags=["document-templates"])
settings = get_settings()


def render_template(template_body: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders with values."""

    def replacer(match):
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return re.sub(r"\{\{(.+?)\}\}", replacer, template_body)


@router.get("", response_model=DocumentTemplateListResponse)
async def list_templates(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    stmt = (
        select(DocumentTemplate)
        .where(
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
            DocumentTemplate.is_active.is_(True),
        )
        .order_by(DocumentTemplate.created_at.desc())
    )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    result = await db.execute(stmt)
    templates = result.scalars().all()

    return DocumentTemplateListResponse(
        items=[DocumentTemplateResponse.model_validate(t) for t in templates],
        total=total,
    )


@router.post("", response_model=DocumentTemplateResponse, status_code=201)
async def create_template(
    payload: DocumentTemplateCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    if payload.category not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}",
        )

    template = DocumentTemplate(
        tenant_id=uuid.UUID(tenant_id),
        title=payload.title,
        body=payload.body,
        category=payload.category,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return DocumentTemplateResponse.model_validate(template)


@router.get("/{template_id}", response_model=DocumentTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return DocumentTemplateResponse.model_validate(template)


@router.patch("/{template_id}", response_model=DocumentTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    payload: DocumentTemplateUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    updates = payload.model_dump(exclude_none=True)
    if "category" in updates and updates["category"] not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}",
        )

    for field, value in updates.items():
        setattr(template, field, value)

    await db.commit()
    await db.refresh(template)
    return DocumentTemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(template)
    await db.commit()


@router.post("/{template_id}/render", response_model=DocumentTemplateRenderResponse)
async def render_template_endpoint(
    template_id: uuid.UUID,
    payload: DocumentTemplateRenderRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user["tenant_id"]
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    rendered = render_template(template.body, payload.variables)

    matter_document_id = None
    if payload.matter_id:
        matter_result = await db.execute(
            select(Matter).where(
                Matter.id == uuid.UUID(payload.matter_id),
                Matter.tenant_id == uuid.UUID(tenant_id),
            )
        )
        matter = matter_result.scalar_one_or_none()
        if not matter:
            raise HTTPException(status_code=404, detail="Matter not found")

        doc_id = uuid.uuid4()
        storage_dir = os.path.join(
            settings.UPLOAD_DIR,
            tenant_id,
            "matters",
            payload.matter_id,
            str(doc_id),
        )
        os.makedirs(storage_dir, exist_ok=True)
        safe_filename = f"{template.title}.md"
        storage_path = os.path.join(storage_dir, safe_filename)

        rendered_bytes = rendered.encode("utf-8")
        with open(storage_path, "w", encoding="utf-8") as out_file:
            out_file.write(rendered)

        doc = MatterDocument(
            id=doc_id,
            matter_id=uuid.UUID(payload.matter_id),
            tenant_id=uuid.UUID(tenant_id),
            uploaded_by_user_id=uuid.UUID(current_user["user_id"]),
            filename=safe_filename,
            content_type="text/markdown",
            file_size=len(rendered_bytes),
            storage_path=storage_path,
            description=f"Generated from template: {template.title}",
            document_category="generated",
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        matter_document_id = str(doc.id)

    return DocumentTemplateRenderResponse(
        rendered=rendered,
        matter_document_id=matter_document_id,
    )
