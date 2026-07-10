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

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.document_template import DocumentTemplate
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.models.tenant import TenantSettings
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
from app.services.pdf_templates import (
    TemplatePdfError,
    discover_pdf_fields,
    fill_pdf_template,
)
from app.services.matter_file_store import MatterFileStore
from app.services.access_control import require_capability

router = APIRouter(prefix="/api/templates", tags=["document-templates"])
settings = get_settings()
matter_file_store = MatterFileStore()
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


def _validated_title(value: str | None) -> str | None:
    cleaned = _clean_optional_form_value(value)
    if cleaned and len(cleaned) > 300:
        raise HTTPException(
            status_code=422, detail="Template title may not exceed 300 characters"
        )
    return cleaned


def _safe_upload_filename(filename: str | None) -> str:
    name = os.path.basename((filename or "uploaded-template").replace("\\", "/"))
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return (cleaned or "uploaded-template")[:240]


def _safe_generated_filename(title: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", (title or "generated-document"))
    stem = stem.replace("..", ".").strip(" .")[:180] or "generated-document"
    return f"{stem}.{extension.lstrip('.')}"


def _normalized_media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _template_source_dir(tenant_id: str, template_id: uuid.UUID) -> str:
    return os.path.join(settings.UPLOAD_DIR, tenant_id, "templates", str(template_id))


async def _persist_template_source(
    *, tenant_id: str, template_id: uuid.UUID, filename: str, content: bytes
) -> str:
    directory = _template_source_dir(tenant_id, template_id)
    await asyncio.to_thread(
        Path(directory).mkdir, parents=True, exist_ok=True, mode=0o750
    )
    path = os.path.join(directory, _safe_upload_filename(filename))
    await asyncio.to_thread(Path(path).write_bytes, content)
    return path


def _safe_source_path(template: DocumentTemplate) -> Path | None:
    path = template.source_storage_path
    if not path:
        return None
    resolved = Path(path).resolve()
    expected_root = Path(
        _template_source_dir(str(template.tenant_id), template.id)
    ).resolve()
    if not resolved.is_relative_to(expected_root):
        return None
    return resolved


async def _verified_template_source(template: DocumentTemplate) -> bytes:
    path = _safe_source_path(template)
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=409, detail="The original template file is unavailable"
        )
    content = await asyncio.to_thread(path.read_bytes)
    digest = hashlib.sha256(content).hexdigest()
    if not template.source_sha256 or digest != template.source_sha256:
        raise HTTPException(
            status_code=409, detail="The original template failed its integrity check"
        )
    return content


def _storage_document_fields(result) -> dict:
    return {
        "storage_path": result.storage_path,
        "storage_provider": result.provider,
        "storage_backend": result.backend,
        "provider_object_id": result.provider_item_id,
        "provider_drive_id": result.drive_id,
        "provider_parent_id": result.parent_id,
        "storage_error": result.error,
    }


def _reviewed_variable_schema(raw: str | None, discovered: dict) -> dict:
    if raw is None or not raw.strip():
        return discovered
    if len(raw) > 100_000:
        raise HTTPException(status_code=422, detail="variable_schema is too large")
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422, detail="variable_schema must be valid JSON"
        ) from exc
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        raise HTTPException(
            status_code=422, detail="variable_schema.fields must be an array"
        )
    if len(schema["fields"]) > 200:
        raise HTTPException(
            status_code=422, detail="A template may contain at most 200 fields"
        )
    discovered_pdf_names = {
        str(field.get("pdf_field_name"))
        for field in (discovered.get("fields") or [])
        if isinstance(field, dict) and field.get("pdf_field_name")
    }
    discovered_by_pdf_name = {
        str(field.get("pdf_field_name")): field
        for field in (discovered.get("fields") or [])
        if isinstance(field, dict) and field.get("pdf_field_name")
    }
    seen_names: set[str] = set()
    seen_pdf_names: set[str] = set()
    for field in schema["fields"]:
        if not isinstance(field, dict):
            raise HTTPException(
                status_code=422, detail="Every variable field must be an object"
            )
        name = str(field.get("name") or "").strip()
        if (
            not name
            or len(name) > 100
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name)
        ):
            raise HTTPException(
                status_code=422, detail=f"Invalid variable name: {name!r}"
            )
        if name in seen_names:
            raise HTTPException(
                status_code=422, detail=f"Duplicate variable name: {name}"
            )
        seen_names.add(name)
        pdf_name = field.get("pdf_field_name")
        if discovered_pdf_names and pdf_name is None:
            raise HTTPException(
                status_code=422,
                detail=f"PDF variable {name!r} is missing pdf_field_name",
            )
        if pdf_name is not None:
            pdf_name = str(pdf_name)
            if pdf_name not in discovered_pdf_names:
                raise HTTPException(
                    status_code=422, detail=f"Unknown PDF field mapping: {pdf_name}"
                )
            if pdf_name in seen_pdf_names:
                raise HTTPException(
                    status_code=422, detail=f"Duplicate PDF field mapping: {pdf_name}"
                )
            seen_pdf_names.add(pdf_name)
            authoritative = discovered_by_pdf_name[pdf_name]
            for key in (
                "field_type",
                "required",
                "multiline",
                "options",
                "page",
                "rect",
            ):
                field[key] = authoritative.get(key)
    if discovered_pdf_names and seen_pdf_names != discovered_pdf_names:
        raise HTTPException(
            status_code=422,
            detail="Reviewed PDF schema must preserve every discovered pdf_field_name mapping",
        )
    try:
        schema["version"] = max(1, int(schema.get("version") or 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="variable_schema.version must be an integer"
        ) from exc
    schema["source"] = "reviewed_upload"
    return schema


def _is_allowed_template_sample(filename: str | None, content_type: str | None) -> bool:
    fn_lower = (filename or "").lower()
    if fn_lower.endswith(_ALLOWED_TEMPLATE_UPLOAD_EXTENSIONS):
        return True
    return (
        bool(content_type)
        and _normalized_media_type(content_type)
        in _ALLOWED_TEMPLATE_UPLOAD_CONTENT_TYPES
    )


async def _read_template_sample(file: UploadFile) -> tuple[bytes, str]:
    filename = _safe_upload_filename(file.filename)
    if not _is_allowed_template_sample(filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: DOCX, PDF, TXT.",
        )
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded template file is empty")
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
    schema = getattr(template, "variable_schema", None) or {}
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
    if requested_variables is not None:
        variables = requested_variables
    else:
        variables = []
        for variable in [
            *extract_template_variables(template.body),
            *extract_schema_variables(template),
        ]:
            if variable not in variables:
                variables.append(variable)
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
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    if payload.category not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}",
        )

    if (payload.format or "").lower() == "pdf":
        raise HTTPException(
            status_code=422,
            detail="PDF templates require multipart /api/templates/intake/create so the source PDF is retained.",
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
    try:
        analysis = await asyncio.to_thread(
            analyze_template_upload,
            file_bytes=file_bytes,
            filename=filename,
            content_type=file.content_type,
            title=_validated_title(title),
        )
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DocumentTemplateUploadAnalysisResponse(**analysis.as_dict())


@router.post(
    "/intake/create",
    response_model=DocumentTemplateResponse,
    status_code=201,
)
async def create_template_from_sample(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    reviewed_body: str | None = Form(None),
    variable_schema: str | None = Form(None),
    category: str = Form("other"),
    module: str | None = Form(None),
    stage: str | None = Form(None),
    jurisdiction: str | None = Form(None),
    kind: str | None = Form(None),
    current_user=Depends(require_capability("manage_documents")),
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
    try:
        analysis = await asyncio.to_thread(
            analyze_template_upload,
            file_bytes=file_bytes,
            filename=filename,
            content_type=file.content_type,
            title=_validated_title(title),
        )
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if analysis.format == "pdf" and not any(
        isinstance(field, dict) and field.get("pdf_field_name")
        for field in (analysis.variable_schema.get("fields") or [])
    ):
        raise HTTPException(
            status_code=422,
            detail="This PDF has no fillable AcroForm fields. Add fillable fields, then upload it again.",
        )
    body = analysis.body
    if reviewed_body is not None:
        if len(reviewed_body) > 20_000:
            raise HTTPException(
                status_code=422, detail="reviewed_body exceeds 20,000 characters"
            )
        body = reviewed_body.strip()
        if not body:
            raise HTTPException(
                status_code=422, detail="reviewed_body may not be empty"
            )
    reviewed_schema = _reviewed_variable_schema(
        variable_schema, analysis.variable_schema
    )
    if analysis.format == "pdf":
        mapped_variables = {
            str(field.get("name"))
            for field in (reviewed_schema.get("fields") or [])
            if isinstance(field, dict) and field.get("pdf_field_name")
        }
        unknown_body_variables = (
            set(extract_template_variables(body)) - mapped_variables
        )
        if unknown_body_variables:
            raise HTTPException(
                status_code=422,
                detail=(
                    "PDF reviewed_body contains variables without AcroForm mappings: "
                    + ", ".join(sorted(unknown_body_variables))
                ),
            )
    template_id = uuid.uuid4()
    source_path = await _persist_template_source(
        tenant_id=tenant_id,
        template_id=template_id,
        filename=filename,
        content=file_bytes,
    )
    template = DocumentTemplate(
        id=template_id,
        tenant_id=uuid.UUID(tenant_id),
        title=analysis.title,
        body=body,
        category=category,
        description=f"Draft created from uploaded sample: {filename}",
        status="draft",
        format=analysis.format,
        module=_clean_optional_form_value(module),
        stage=_clean_optional_form_value(stage),
        jurisdiction=_clean_optional_form_value(jurisdiction),
        kind=_clean_optional_form_value(kind),
        variable_schema=reviewed_schema,
        branding_profile=analysis.branding_profile,
        source_storage_path=source_path,
        source_filename=filename,
        source_content_type=(
            "application/pdf"
            if analysis.format == "pdf"
            else _normalized_media_type(file.content_type) or "application/octet-stream"
        ),
        source_sha256=hashlib.sha256(file_bytes).hexdigest(),
        source_file_size=len(file_bytes),
        is_active=False,
    )
    try:
        db.add(template)
        await db.commit()
    except Exception:
        await asyncio.to_thread(Path(source_path).unlink, missing_ok=True)
        raise
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


@router.get("/{template_id}/source")
async def download_template_source(
    template_id: uuid.UUID,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    template = await db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == tenant_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    content = await _verified_template_source(template)
    filename = _safe_upload_filename(template.source_filename or "template-source")
    return Response(
        content=content,
        media_type=template.source_content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
    )


@router.post("/{template_id}/preview-file", include_in_schema=False)
@router.post("/{template_id}/render-file")
async def preview_template_file(
    template_id: uuid.UUID,
    payload: DocumentTemplateRenderRequest,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Render a PDF without storing a matter document."""
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    template = await db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == tenant_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.format != "pdf":
        raise HTTPException(
            status_code=409, detail="Binary preview is available only for PDF templates"
        )
    source = await _verified_template_source(template)
    try:
        output = await asyncio.to_thread(
            fill_pdf_template,
            source,
            variable_schema=template.variable_schema,
            variables=payload.variables,
            flatten=payload.flatten_pdf,
            enforce_required=False,
        )
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = _safe_generated_filename(template.title, "pdf")
    disposition = f'inline; filename="{filename}"'
    return Response(
        content=output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
    )


@router.patch("/{template_id}", response_model=DocumentTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    payload: DocumentTemplateUpdate,
    current_user=Depends(require_capability("manage_documents")),
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

    current_format = str(template.format or "").lower()
    requested_format = str(updates.get("format") or current_format).lower()
    if current_format == "pdf" and requested_format != "pdf":
        raise HTTPException(
            status_code=422,
            detail="A source-backed PDF template cannot be converted to another format in place.",
        )
    if requested_format == "pdf" and current_format != "pdf":
        raise HTTPException(
            status_code=422,
            detail="PDF templates require multipart intake with the original source file.",
        )
    if "format" in updates:
        updates["format"] = requested_format

    # A PDF field map is only trustworthy relative to the immutable retained
    # source. Re-discover the AcroForm whenever its map changes or the template
    # is activated, and require a complete, one-to-one reviewed mapping.
    validate_pdf_contract = current_format == "pdf" and (
        "variable_schema" in updates
        or "body" in updates
        or updates.get("is_active") is True
    )
    if validate_pdf_contract:
        source = await _verified_template_source(template)
        try:
            discovered_fields = await asyncio.to_thread(discover_pdf_fields, source)
        except TemplatePdfError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not discovered_fields:
            raise HTTPException(
                status_code=422,
                detail="A PDF template must contain at least one mapped AcroForm field before activation.",
            )
        discovered_schema = {
            "version": 1,
            "source": "pdf_acroform",
            "fields": discovered_fields,
        }
        effective_schema = updates.get("variable_schema", template.variable_schema)
        updates["variable_schema"] = _reviewed_variable_schema(
            json.dumps(effective_schema), discovered_schema
        )
        mapped_variables = {
            str(field.get("name"))
            for field in (updates["variable_schema"].get("fields") or [])
            if isinstance(field, dict) and field.get("pdf_field_name")
        }
        effective_body = str(updates.get("body", template.body) or "")
        unknown_body_variables = (
            set(extract_template_variables(effective_body)) - mapped_variables
        )
        if unknown_body_variables:
            raise HTTPException(
                status_code=422,
                detail=(
                    "PDF body contains variables without AcroForm mappings: "
                    + ", ".join(sorted(unknown_body_variables))
                ),
            )

    for field, value in updates.items():
        setattr(template, field, value)

    await db.commit()
    await db.refresh(template)
    return DocumentTemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    current_user=Depends(require_capability("manage_documents")),
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

    source_path = _safe_source_path(template)
    await db.delete(template)
    await db.commit()
    if source_path:
        await asyncio.to_thread(source_path.unlink, missing_ok=True)


@router.post(
    "/{template_id}/smart-fill-preview",
    response_model=DocumentTemplateSmartFillResponse,
)
async def smart_fill_preview(
    template_id: uuid.UUID,
    payload: DocumentTemplateSmartFillRequest,
    current_user=Depends(require_capability("manage_documents")),
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
    current_user=Depends(require_capability("manage_documents")),
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
    if payload.matter_id and not template.is_active:
        raise HTTPException(
            status_code=409,
            detail="Only active templates can be saved to a matter. Preview and activate this template first.",
        )

    rendered = render_template(template.body, payload.variables)
    output_format = "pdf" if template.format == "pdf" else "markdown"
    output_filename = _safe_generated_filename(
        template.title, "pdf" if output_format == "pdf" else "md"
    )
    if output_format == "pdf":
        source = await _verified_template_source(template)
        try:
            output_bytes = await asyncio.to_thread(
                fill_pdf_template,
                source,
                variable_schema=template.variable_schema,
                variables=payload.variables,
                flatten=payload.flatten_pdf,
                enforce_required=bool(payload.matter_id),
            )
        except TemplatePdfError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rendered = (
            f'PDF ready: "{output_filename}"\n'
            f"Filled {sum(1 for value in payload.variables.values() if value)} reviewed field(s)."
        )
    else:
        output_bytes = rendered.encode("utf-8")
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
    download_url = None
    storage_backend = None
    storage_provider = None
    storage_warning = None
    if payload.matter_id:
        try:
            parsed_matter_id = uuid.UUID(payload.matter_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid matter_id") from exc
        matter_result = await db.execute(
            select(Matter).where(
                Matter.id == parsed_matter_id,
                Matter.tenant_id == parsed_tenant_id,
            )
        )
        matter = matter_result.scalar_one_or_none()
        if not matter:
            raise HTTPException(status_code=404, detail="Matter not found")

        doc_id = uuid.uuid4()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_filename = _safe_generated_filename(
            f"{template.title}-{timestamp}-{doc_id.hex[:8]}",
            "pdf" if output_format == "pdf" else "md",
        )
        if output_format == "pdf":
            rendered = (
                f'PDF ready: "{output_filename}"\n'
                f"Filled {sum(1 for value in payload.variables.values() if value)} reviewed field(s)."
            )

        tenant_settings = await db.scalar(
            select(TenantSettings).where(TenantSettings.tenant_id == parsed_tenant_id)
        )
        preferred_provider = (
            tenant_settings.primary_cloud_provider if tenant_settings else None
        )
        content_type = "application/pdf" if output_format == "pdf" else "text/markdown"
        storage_result = await matter_file_store.store_matter_file_result(
            db=db,
            tenant_id=tenant_id,
            matter_slug=matter.slug,
            category="generated",
            filename=output_filename,
            content=output_bytes,
            content_type=content_type,
            matter_cloud_folder=matter.cloud_folder,
            preferred_provider=preferred_provider,
        )
        storage_backend = storage_result.backend
        storage_provider = storage_result.provider
        storage_warning = storage_result.error

        doc = MatterDocument(
            id=doc_id,
            matter_id=parsed_matter_id,
            tenant_id=parsed_tenant_id,
            uploaded_by_user_id=current_user.id,
            filename=output_filename,
            content_type=content_type,
            file_size=len(output_bytes),
            description=f"Generated from template: {template.title}",
            document_category="generated",
            **_storage_document_fields(storage_result),
        )
        db.add(doc)
        db.add(
            MatterEvent(
                tenant_id=parsed_tenant_id,
                matter_id=parsed_matter_id,
                event_type="document_generated",
                title=f"Generated document: {output_filename}",
                content=f"Generated from template {template.title}.",
                note_type="system",
                metadata_json={
                    "template_id": str(template.id),
                    "template_title": template.title,
                    "template_source_sha256": template.source_sha256,
                    "output_document_id": str(doc.id),
                    "output_filename": output_filename,
                    "output_format": output_format,
                    "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
                    "filled_variables": sorted(
                        name for name, value in payload.variables.items() if value
                    ),
                    "flatten_pdf": payload.flatten_pdf
                    if output_format == "pdf"
                    else None,
                    "renderer_version": "pdf-acroform-v1"
                    if output_format == "pdf"
                    else "markdown-v1",
                },
                created_by=current_user.id,
            )
        )
        await db.commit()
        await db.refresh(doc)
        matter_document_id = str(doc.id)
        download_url = f"/api/matters/{parsed_matter_id}/documents/{doc.id}/download"

    return DocumentTemplateRenderResponse(
        rendered=rendered,
        matter_document_id=matter_document_id,
        variable_suggestions=variable_suggestions,
        output_format=output_format,
        output_filename=output_filename,
        download_url=download_url,
        storage_backend=storage_backend,
        storage_provider=storage_provider,
        storage_warning=storage_warning,
    )
