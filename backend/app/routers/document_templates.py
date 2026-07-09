"""
Document Templates router — CRUD + variable substitution rendering.

  GET    /api/templates              list active templates
  POST   /api/templates              create template
  GET    /api/templates/{id}         get template detail
  PATCH  /api/templates/{id}         update template fields
  DELETE /api/templates/{id}         delete template
  POST   /api/templates/{id}/smart-fill-preview
  POST   /api/templates/{id}/render  render template with variables
"""

import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    DocumentTemplateSmartFillRequest,
    DocumentTemplateSmartFillResponse,
    DocumentTemplateUploadAnalysisResponse,
    DocumentTemplateUpdate,
    DocumentTemplateVariableSuggestion,
)
from app.services.template_intake import analyze_template_upload

router = APIRouter(prefix="/api/templates", tags=["document-templates"])
settings = get_settings()
VARIABLE_PATTERN = re.compile(r"\{\{(.+?)\}\}")
_ALLOWED_TEMPLATE_UPLOAD_EXTENSIONS = (".docx", ".pdf", ".txt")
_ALLOWED_TEMPLATE_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


def _clean_optional_form_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _safe_upload_filename(filename: str | None) -> str:
    return os.path.basename(filename or "uploaded-template")


def _is_allowed_template_sample(filename: str | None, content_type: str | None) -> bool:
    fn_lower = (filename or "").lower()
    if fn_lower.endswith(_ALLOWED_TEMPLATE_UPLOAD_EXTENSIONS):
        return True
    return (
        bool(content_type)
        and content_type.lower() in _ALLOWED_TEMPLATE_UPLOAD_CONTENT_TYPES
    )


async def _read_template_sample(file: UploadFile) -> tuple[bytes, str]:
    filename = _safe_upload_filename(file.filename)
    if not _is_allowed_template_sample(filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: DOCX, PDF, TXT.",
        )
    file_bytes = await file.read()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )
    return file_bytes, filename


def render_template(template_body: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders with values."""

    def replacer(match):
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return VARIABLE_PATTERN.sub(replacer, template_body)


def extract_template_variables(template_body: str) -> list[str]:
    variables: list[str] = []
    seen: set[str] = set()
    for match in VARIABLE_PATTERN.finditer(template_body):
        variable = match.group(1).strip()
        if variable and variable not in seen:
            variables.append(variable)
            seen.add(variable)
    return variables


def extract_schema_variables(template: DocumentTemplate) -> list[str]:
    schema = template.variable_schema or {}
    fields = schema.get("fields") if isinstance(schema, dict) else None
    variables: list[str] = []
    seen: set[str] = set()
    if not isinstance(fields, list):
        return variables
    for field in fields:
        if not isinstance(field, dict):
            continue
        variable = field.get("name") or field.get("variable") or field.get("key")
        if not variable:
            continue
        variable = str(variable).strip()
        if variable and variable not in seen:
            variables.append(variable)
            seen.add(variable)
    return variables


def _normalize_variable_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _stringify_suggestion(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    text = str(value).strip()
    return text or None


def _add_candidate(
    candidates: dict[str, DocumentTemplateVariableSuggestion],
    alias: str,
    value: Any,
    *,
    source_type: str,
    source_field: str,
    record_id: uuid.UUID | str | None = None,
    confidence: float = 1.0,
    review_required: bool = False,
) -> None:
    suggested_value = _stringify_suggestion(value)
    if suggested_value is None:
        return
    key = _normalize_variable_name(alias)
    candidates.setdefault(
        key,
        DocumentTemplateVariableSuggestion(
            variable=alias,
            suggested_value=suggested_value,
            source_type=source_type,
            source_field=source_field,
            provenance={
                "source_type": source_type,
                "source_field": source_field,
                "record_id": str(record_id) if record_id else None,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            },
            confidence=confidence,
            review_required=review_required,
        ),
    )


def _address_value(address: dict | None, key: str) -> str | None:
    if not isinstance(address, dict):
        return None
    return _stringify_suggestion(address.get(key))


def _collect_smart_fill_candidates(
    *,
    matter: Matter | None,
    current_user,
) -> dict[str, DocumentTemplateVariableSuggestion]:
    candidates: dict[str, DocumentTemplateVariableSuggestion] = {}

    _add_candidate(
        candidates,
        "current_user_name",
        getattr(current_user, "full_name", None),
        source_type="current_user",
        source_field="full_name",
        record_id=getattr(current_user, "id", None),
    )
    _add_candidate(
        candidates,
        "current_user_email",
        getattr(current_user, "email", None),
        source_type="current_user",
        source_field="email",
        record_id=getattr(current_user, "id", None),
    )
    _add_candidate(
        candidates,
        "prepared_by",
        getattr(current_user, "full_name", None)
        or getattr(current_user, "email", None),
        source_type="current_user",
        source_field="full_name",
        record_id=getattr(current_user, "id", None),
    )

    if not matter:
        return candidates

    matter_fields = {
        "matter_id": matter.id,
        "matter_name": matter.matter_name,
        "matter_type": matter.matter_type,
        "matter_description": matter.description,
        "matter_status": matter.status,
        "matter_stage": matter.stage,
        "matter_jurisdiction": matter.jurisdiction,
        "jurisdiction": matter.jurisdiction,
        "case_number": matter.case_number,
        "court": matter.court,
        "judge": matter.judge,
        "billing_method": matter.billing_method,
        "billing_cycle": matter.billing_cycle,
        "hourly_rate": matter.hourly_rate,
        "budget_amount": matter.budget_amount,
        "counterparty": matter.counterparty,
    }
    for alias, value in matter_fields.items():
        _add_candidate(
            candidates,
            alias,
            value,
            source_type="matter",
            source_field=alias,
            record_id=matter.id,
        )

    client = getattr(matter, "client", None)
    if client:
        _add_candidate(
            candidates,
            "client_name",
            client.display_name,
            source_type="contact",
            source_field="display_name",
            record_id=client.id,
        )
        _add_candidate(
            candidates,
            "client_email",
            client.email,
            source_type="contact",
            source_field="email",
            record_id=client.id,
        )
        _add_candidate(
            candidates,
            "client_phone",
            client.phone,
            source_type="contact",
            source_field="phone",
            record_id=client.id,
        )
        address = client.address
        for alias, key in {
            "client_street": "street",
            "client_city": "city",
            "client_state": "state",
            "client_zip": "zip",
            "client_country": "country",
        }.items():
            _add_candidate(
                candidates,
                alias,
                _address_value(address, key),
                source_type="contact",
                source_field=f"address.{key}",
                record_id=client.id,
            )

    attorney = getattr(matter, "attorney_of_record", None)
    if attorney:
        _add_candidate(
            candidates,
            "attorney_name",
            attorney.full_name,
            source_type="user",
            source_field="full_name",
            record_id=attorney.id,
        )
        _add_candidate(
            candidates,
            "attorney_email",
            attorney.email,
            source_type="user",
            source_field="email",
            record_id=attorney.id,
        )

    return candidates


async def _load_matter_context(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    matter_id: str | None,
) -> Matter | None:
    if not matter_id:
        return None
    try:
        parsed_matter_id = uuid.UUID(matter_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid matter_id") from exc

    result = await db.execute(
        select(Matter)
        .options(
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(
            Matter.id == parsed_matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def build_variable_suggestions(
    *,
    template: DocumentTemplate,
    requested_variables: list[str] | None,
    matter_id: str | None,
    tenant_id: uuid.UUID,
    current_user,
    db: AsyncSession,
) -> tuple[str | None, list[DocumentTemplateVariableSuggestion]]:
    matter = await _load_matter_context(db=db, tenant_id=tenant_id, matter_id=matter_id)
    variables = (
        requested_variables
        or extract_template_variables(template.body)
        or extract_schema_variables(template)
    )
    candidates = _collect_smart_fill_candidates(
        matter=matter, current_user=current_user
    )

    suggestions: list[DocumentTemplateVariableSuggestion] = []
    for variable in variables:
        candidate = candidates.get(_normalize_variable_name(variable))
        if candidate:
            suggestions.append(candidate.model_copy(update={"variable": variable}))
            continue
        suggestions.append(
            DocumentTemplateVariableSuggestion(
                variable=variable,
                provenance={"status": "no_deterministic_source"},
                review_required=True,
            )
        )

    return str(matter.id) if matter else None, suggestions


@router.get("", response_model=DocumentTemplateListResponse)
async def list_templates(
    include_inactive: bool = Query(False),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    filters = [DocumentTemplate.tenant_id == uuid.UUID(tenant_id)]
    if not include_inactive:
        filters.append(DocumentTemplate.is_active.is_(True))

    stmt = (
        select(DocumentTemplate)
        .where(*filters)
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
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    if payload.category not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}",
        )

    template = DocumentTemplate(
        tenant_id=uuid.UUID(tenant_id),
        **payload.model_dump(exclude_none=True),
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return DocumentTemplateResponse.model_validate(template)


@router.post(
    "/intake/analyze",
    response_model=DocumentTemplateUploadAnalysisResponse,
)
async def analyze_template_sample(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    file_bytes, filename = await _read_template_sample(file)
    analysis = analyze_template_upload(
        file_bytes=file_bytes,
        filename=filename,
        content_type=file.content_type,
        title=_clean_optional_form_value(title),
    )
    return DocumentTemplateUploadAnalysisResponse(**analysis.as_dict())


@router.post(
    "/intake/create",
    response_model=DocumentTemplateResponse,
    status_code=201,
)
async def create_template_from_sample(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    category: str = Form("other"),
    module: str | None = Form(None),
    stage: str | None = Form(None),
    jurisdiction: str | None = Form(None),
    kind: str | None = Form(None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    if category not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}",
        )

    file_bytes, filename = await _read_template_sample(file)
    analysis = analyze_template_upload(
        file_bytes=file_bytes,
        filename=filename,
        content_type=file.content_type,
        title=_clean_optional_form_value(title),
    )
    template = DocumentTemplate(
        tenant_id=uuid.UUID(tenant_id),
        title=analysis.title,
        body=analysis.body,
        category=category,
        description=f"Draft created from uploaded sample: {filename}",
        status="draft",
        format=analysis.format,
        module=_clean_optional_form_value(module),
        stage=_clean_optional_form_value(stage),
        jurisdiction=_clean_optional_form_value(jurisdiction),
        kind=_clean_optional_form_value(kind),
        variable_schema=analysis.variable_schema,
        branding_profile=analysis.branding_profile,
        is_active=False,
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
    tenant_id = str(current_user.tenant_id)
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
    tenant_id = str(current_user.tenant_id)
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
    tenant_id = str(current_user.tenant_id)
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


@router.post(
    "/{template_id}/smart-fill-preview",
    response_model=DocumentTemplateSmartFillResponse,
)
async def smart_fill_preview(
    template_id: uuid.UUID,
    payload: DocumentTemplateSmartFillRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    parsed_tenant_id = uuid.UUID(tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == parsed_tenant_id,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    resolved_matter_id, suggestions = await build_variable_suggestions(
        template=template,
        requested_variables=payload.variables,
        matter_id=payload.matter_id,
        tenant_id=parsed_tenant_id,
        current_user=current_user,
        db=db,
    )

    return DocumentTemplateSmartFillResponse(
        template_id=str(template.id),
        matter_id=resolved_matter_id,
        variables=suggestions,
    )


@router.post(
    "/{template_id}/render",
    response_model=DocumentTemplateRenderResponse,
    response_model_exclude_none=True,
)
async def render_template_endpoint(
    template_id: uuid.UUID,
    payload: DocumentTemplateRenderRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    parsed_tenant_id = uuid.UUID(tenant_id)
    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == parsed_tenant_id,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    rendered = render_template(template.body, payload.variables)
    variable_suggestions = None
    if payload.include_suggestions:
        _, variable_suggestions = await build_variable_suggestions(
            template=template,
            requested_variables=None,
            matter_id=payload.matter_id,
            tenant_id=parsed_tenant_id,
            current_user=current_user,
            db=db,
        )

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
            uploaded_by_user_id=current_user.id,
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
        variable_suggestions=variable_suggestions,
    )
