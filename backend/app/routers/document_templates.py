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
import hmac
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import async_session_maker, get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.document_template import DocumentTemplate
from app.models.document_template_preview import DocumentTemplatePreview
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
    fill_pdf_template,
    pdf_review_evidence,
    validate_representative_pdf_variables,
)
from app.services.docx_templates import TemplateDocxError, fill_docx_template
from app.services.template_ocr import TemplateOcrError
from app.services.matter_file_store import MatterFileStore
from app.services.access_control import require_capability

router = APIRouter(prefix="/api/templates", tags=["document-templates"])
logger = logging.getLogger(__name__)
settings = get_settings()
matter_file_store = MatterFileStore()
VARIABLE_PATTERN = re.compile(r"\{\{(.+?)\}\}")
_ALLOWED_TEMPLATE_UPLOAD_EXTENSIONS = (".docx", ".pdf", ".txt")
_ALLOWED_TEMPLATE_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}
_PDF_RENDERER_VERSION = "pdf-source-v4-ocr-preview-bound"
_MAX_PERSISTED_PREVIEWS_PER_USER_PURPOSE = 50
_PDF_PREVIEW_TTLS = {
    "draft": timedelta(hours=1),
    "activation": timedelta(hours=24),
    "generation": timedelta(minutes=30),
}
_COMMIT_OUTCOME_DELAYS = (0, 0.1, 0.3, 0.6, 1.0)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _pdf_contract_sha256(
    template: DocumentTemplate,
    *,
    variable_schema: dict | None = None,
    body: str | None = None,
) -> str:
    """Fingerprint every template input that can affect a PDF render contract."""
    schema = template.variable_schema if variable_schema is None else variable_schema
    # Intake/activation may update descriptive schema provenance (for example,
    # ``upload_analysis`` to ``reviewed_upload``). Only the mapped fields affect
    # PDF output and required/review semantics, so do not invalidate a preview
    # for metadata that cannot change the rendered artifact.
    schema_contract = {
        "fields": (schema or {}).get("fields") or [],
    }
    return _canonical_sha256(
        {
            "renderer_version": _PDF_RENDERER_VERSION,
            "template_id": str(template.id),
            "format": str(template.format or "").lower(),
            "source_sha256": template.source_sha256,
            "body": template.body if body is None else body,
            "variable_schema": schema_contract,
        }
    )


def _pdf_values_hmac_sha256(
    *,
    variables: dict[str, str],
    flatten_pdf: bool,
    matter_id: uuid.UUID | None,
) -> str:
    """Bind preview evidence to exact values, output mode, and destination."""
    encoded = _canonical_json_bytes(
        {
            "variables": variables,
            "flatten_pdf": flatten_pdf,
            "matter_id": str(matter_id) if matter_id else None,
        }
    )
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"clarity-pdf-preview-values-v1\0" + encoded,
        hashlib.sha256,
    ).hexdigest()


async def _load_render_matter(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: str | None,
) -> Matter | None:
    if not matter_id:
        return None
    try:
        parsed_matter_id = uuid.UUID(matter_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid matter_id") from exc
    matter = await db.scalar(
        select(Matter).where(
            Matter.id == parsed_matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _trim_preview_evidence(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    user_id: uuid.UUID,
    purpose: str,
) -> None:
    now = datetime.now(timezone.utc)
    if purpose == "generation":
        # A failed save releases its row locks before compensation can persist
        # a reconciliation marker. Never trim a live/recent generation preview
        # in that gap. Old, expired, non-terminal attempts are safe to remove;
        # consumed and reconciliation rows remain records-lifecycle evidence.
        await db.execute(
            delete(DocumentTemplatePreview).where(
                DocumentTemplatePreview.tenant_id == tenant_id,
                DocumentTemplatePreview.template_id == template_id,
                DocumentTemplatePreview.previewed_by_user_id == user_id,
                DocumentTemplatePreview.purpose == purpose,
                DocumentTemplatePreview.consumed_at.is_(None),
                DocumentTemplatePreview.reconciliation_required_at.is_(None),
                DocumentTemplatePreview.expires_at < now - timedelta(hours=1),
            )
        )
        return
    retained_ids = (
        select(DocumentTemplatePreview.id)
        .where(
            DocumentTemplatePreview.tenant_id == tenant_id,
            DocumentTemplatePreview.template_id == template_id,
            DocumentTemplatePreview.previewed_by_user_id == user_id,
            DocumentTemplatePreview.purpose == purpose,
            DocumentTemplatePreview.consumed_at.is_(None),
            DocumentTemplatePreview.reconciliation_required_at.is_(None),
        )
        # Preserve every consumed record under the document lifecycle. Among
        # unconsumed attempts, retain live evidence before expired evidence,
        # then keep the newest attempts within the bounded bucket.
        .order_by(
            case(
                (DocumentTemplatePreview.expires_at > now, 1),
                else_=0,
            ).desc(),
            DocumentTemplatePreview.created_at.desc(),
        )
        .limit(_MAX_PERSISTED_PREVIEWS_PER_USER_PURPOSE)
    )
    await db.execute(
        delete(DocumentTemplatePreview).where(
            DocumentTemplatePreview.tenant_id == tenant_id,
            DocumentTemplatePreview.template_id == template_id,
            DocumentTemplatePreview.previewed_by_user_id == user_id,
            DocumentTemplatePreview.purpose == purpose,
            DocumentTemplatePreview.consumed_at.is_(None),
            DocumentTemplatePreview.reconciliation_required_at.is_(None),
            DocumentTemplatePreview.id.not_in(retained_ids),
        )
    )


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


async def _matter_document_commit_outcome(
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
) -> bool | None:
    """Resolve an ambiguous commit through a fresh tenant-scoped connection.

    ``True`` means the atomic document/event/evidence transaction committed;
    ``False`` means it definitively did not. ``None`` is deliberately treated as
    unknown so callers preserve staged bytes for operator reconciliation.
    """
    # This path is rare and data-loss-sensitive. Observe for two seconds before
    # treating absence as confirmed; a dropped acknowledgement can race a slow
    # WAL flush even though the eventual COMMIT succeeds.
    for delay_seconds in _COMMIT_OUTCOME_DELAYS:
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            async with async_session_maker() as verification_db:
                await set_tenant_context(verification_db, str(tenant_id))
                persisted_id = await verification_db.scalar(
                    select(MatterDocument.id).where(
                        MatterDocument.id == document_id,
                        MatterDocument.tenant_id == tenant_id,
                    )
                )
                if persisted_id is not None:
                    return True
        except Exception:
            logger.critical(
                "Unable to resolve generated-document commit outcome tenant=%s "
                "document=%s; staged bytes must be preserved for reconciliation",
                tenant_id,
                document_id,
                exc_info=True,
            )
            return None
    return False


async def _template_commit_outcome(
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> tuple[bool | None, DocumentTemplateResponse | None]:
    """Resolve an upload/create COMMIT through fresh tenant-scoped sessions."""
    for delay_seconds in _COMMIT_OUTCOME_DELAYS:
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            async with async_session_maker() as verification_db:
                await set_tenant_context(verification_db, str(tenant_id))
                template = await verification_db.scalar(
                    select(DocumentTemplate).where(
                        DocumentTemplate.id == template_id,
                        DocumentTemplate.tenant_id == tenant_id,
                    )
                )
                if template is not None:
                    return True, DocumentTemplateResponse.model_validate(template)
        except Exception:
            logger.critical(
                "Unable to resolve template-create commit outcome tenant=%s "
                "template=%s; retained source must be preserved for reconciliation",
                tenant_id,
                template_id,
                exc_info=True,
            )
            return None, None
    return False, None


async def _remove_uncommitted_template_source(
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    source_path: str,
) -> bool:
    """Delete only the exact regular source staged for a confirmed rollback."""
    candidate = Path(source_path)
    expected_root = Path(_template_source_dir(str(tenant_id), template_id)).resolve()
    resolved = candidate.resolve()
    if candidate.is_symlink() or not resolved.is_relative_to(expected_root):
        logger.critical(
            "ACTION REQUIRED: refused unsafe uncommitted template-source cleanup "
            "tenant=%s template=%s",
            tenant_id,
            template_id,
        )
        return False
    try:
        await asyncio.to_thread(resolved.unlink, missing_ok=True)
        return True
    except Exception:
        logger.critical(
            "ACTION REQUIRED: uncommitted template-source cleanup failed "
            "tenant=%s template=%s",
            tenant_id,
            template_id,
            exc_info=True,
        )
        return False


async def _mark_preview_reconciliation_required(
    *,
    tenant_id: uuid.UUID,
    preview_id: uuid.UUID | None,
    reason: str,
    storage_result,
    output_filename: str,
    output_sha256: str,
    document_id: uuid.UUID,
) -> bool:
    """Persist a terminal retry block after storage/DB outcome diverges."""
    if preview_id is None or reason not in {
        "cleanup_failed",
        "commit_outcome_unknown",
    }:
        return False
    backend = str(storage_result.backend or "").strip().lower()[:50] or None
    local_path = None
    if backend == "local" and storage_result.storage_path:
        candidate = Path(str(storage_result.storage_path)).resolve()
        tenant_root = (Path(settings.UPLOAD_DIR) / str(tenant_id)).resolve()
        if candidate.is_relative_to(tenant_root):
            local_path = str(candidate)[:1000]
    try:
        async with async_session_maker() as reconciliation_db:
            await set_tenant_context(reconciliation_db, str(tenant_id))
            evidence = await reconciliation_db.scalar(
                select(DocumentTemplatePreview)
                .where(
                    DocumentTemplatePreview.id == preview_id,
                    DocumentTemplatePreview.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if not evidence:
                return False
            if evidence.consumed_at is not None:
                logger.critical(
                    "Preview reconciliation marker skipped because evidence is "
                    "already consumed tenant=%s preview=%s document=%s",
                    tenant_id,
                    preview_id,
                    evidence.consumed_by_document_id,
                )
                return False
            if evidence.reconciliation_required_at is not None:
                return True
            evidence.reconciliation_required_at = datetime.now(timezone.utc)
            evidence.reconciliation_reason = reason
            evidence.reconciliation_storage_backend = backend
            evidence.reconciliation_provider_item_id = (
                str(storage_result.provider_item_id)[:500]
                if storage_result.provider_item_id
                else None
            )
            evidence.reconciliation_provider_drive_id = (
                str(storage_result.drive_id)[:500] if storage_result.drive_id else None
            )
            evidence.reconciliation_local_path = local_path
            evidence.reconciliation_output_filename = output_filename[:500]
            evidence.reconciliation_output_sha256 = output_sha256
            evidence.reconciliation_document_id = document_id
            await reconciliation_db.commit()
            return True
    except Exception:
        logger.critical(
            "ACTION REQUIRED: failed to persist preview reconciliation block "
            "tenant=%s preview=%s reason=%s backend=%s provider_item_id=%s",
            tenant_id,
            preview_id,
            reason,
            backend,
            storage_result.provider_item_id,
            exc_info=True,
        )
        return False


async def _rollback_quietly(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
) -> bool:
    try:
        await db.rollback()
        return True
    except Exception:
        logger.exception(
            "Database rollback failed while finalizing generated document "
            "tenant=%s matter=%s",
            tenant_id,
            matter_id,
        )
        return False


async def _compensate_staged_document(
    db: AsyncSession,
    *,
    tenant_id: str,
    matter_id: uuid.UUID,
    storage_result,
) -> bool:
    """Return True only when the staged bytes are confirmed removed."""
    try:
        if str(storage_result.backend or "").lower() != "local":
            # Rollback clears transaction-local RLS GUCs; cloud token lookup
            # must re-enter the tenant context before provider cleanup.
            await set_tenant_context(db, tenant_id)
        await matter_file_store.delete_stored_result(
            db=db,
            tenant_id=tenant_id,
            result=storage_result,
        )
        return True
    except Exception:
        logger.critical(
            "ACTION REQUIRED: staged generated document cleanup failed "
            "tenant=%s matter=%s backend=%s provider_item_id=%s",
            tenant_id,
            matter_id,
            storage_result.backend,
            storage_result.provider_item_id,
            exc_info=True,
        )
        return False


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
    discovered_overlay_keys = {
        str(field.get("pdf_source_key"))
        for field in (discovered.get("fields") or [])
        if isinstance(field, dict)
        and (field.get("pdf_overlay") or field.get("pdf_overlays"))
        and field.get("pdf_source_key")
    }
    discovered_by_overlay_key = {
        str(field.get("pdf_source_key")): field
        for field in (discovered.get("fields") or [])
        if isinstance(field, dict)
        and (field.get("pdf_overlay") or field.get("pdf_overlays"))
        and field.get("pdf_source_key")
    }
    seen_names: set[str] = set()
    seen_pdf_names: set[str] = set()
    seen_overlay_keys: set[str] = set()
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
        overlay_key = field.get("pdf_source_key")
        if discovered_overlay_keys:
            if overlay_key is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"PDF variable {name!r} is missing pdf_source_key",
                )
            overlay_key = str(overlay_key)
            if overlay_key not in discovered_overlay_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown PDF overlay mapping: {overlay_key}",
                )
            if overlay_key in seen_overlay_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"Duplicate PDF overlay mapping: {overlay_key}",
                )
            seen_overlay_keys.add(overlay_key)
            authoritative = discovered_by_overlay_key[overlay_key]
            for key in (
                "field_type",
                "required",
                "multiline",
                "page",
                "rect",
                "pdf_overlay",
                "pdf_overlays",
                "source_text",
            ):
                field[key] = authoritative.get(key)
    if discovered_pdf_names and seen_pdf_names != discovered_pdf_names:
        raise HTTPException(
            status_code=422,
            detail="Reviewed PDF schema must preserve every discovered pdf_field_name mapping",
        )
    if discovered_overlay_keys and seen_overlay_keys != discovered_overlay_keys:
        raise HTTPException(
            status_code=422,
            detail="Reviewed PDF schema must preserve every detected overlay mapping",
        )
    try:
        schema["version"] = max(1, int(schema.get("version") or 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="variable_schema.version must be an integer"
        ) from exc
    if isinstance(discovered.get("detection"), dict):
        schema["detection"] = discovered["detection"]
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
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    file_bytes = await file.read(max_bytes + 1)
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded template file is empty")
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

    if (payload.format or "").lower() in {"pdf", "docx"}:
        raise HTTPException(
            status_code=422,
            detail="PDF and DOCX templates require multipart /api/templates/intake/create so the original source is retained.",
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
    except (TemplatePdfError, TemplateDocxError, TemplateOcrError) as exc:
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
    except (TemplatePdfError, TemplateDocxError, TemplateOcrError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if analysis.format == "pdf" and not any(
        isinstance(field, dict)
        and (
            field.get("pdf_field_name")
            or field.get("pdf_overlay")
            or field.get("pdf_overlays")
        )
        for field in (analysis.variable_schema.get("fields") or [])
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "No reusable PDF fields were detected. Try a clearer source or add "
                "visible labels next to the values that should change."
            ),
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
    if analysis.format == "docx":
        seen_source_text: dict[str, str] = {}
        for field in reviewed_schema.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            source_text = str(
                field.get("source_text") or field.get("example") or ""
            ).strip()
            if not source_text:
                raise HTTPException(
                    status_code=422,
                    detail=f"Word variable {name!r} needs the exact source text it replaces.",
                )
            if source_text not in analysis.extracted_text:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Word variable {name!r} no longer matches text in the uploaded document. "
                        "Select the source again and review the detected details."
                    ),
                )
            previous_name = seen_source_text.get(source_text)
            if previous_name and previous_name != name:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"The same Word source text cannot map to both {previous_name!r} "
                        f"and {name!r}."
                    ),
                )
            seen_source_text[source_text] = name
    if analysis.format in {"pdf", "docx"}:
        mapped_variables = {
            str(field.get("name"))
            for field in (reviewed_schema.get("fields") or [])
            if isinstance(field, dict) and field.get("name")
        }
        unknown_body_variables = (
            set(extract_template_variables(body)) - mapped_variables
        )
        if unknown_body_variables:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{analysis.format.upper()} reviewed_body contains variables without source mappings: "
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
    except Exception as exc:
        logger.error(
            "Template-create commit acknowledgement failed tenant=%s template=%s; "
            "checking outcome independently",
            tenant_id,
            template_id,
            exc_info=True,
        )
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            rollback_succeeded = False
            logger.critical(
                "Template-create rollback failed tenant=%s template=%s; retained "
                "source must be preserved for reconciliation",
                tenant_id,
                template_id,
                exc_info=True,
            )
        outcome, committed_snapshot = (
            await _template_commit_outcome(
                tenant_id=uuid.UUID(tenant_id),
                template_id=template_id,
            )
            if rollback_succeeded
            else (None, None)
        )
        if outcome is True and committed_snapshot is not None:
            logger.warning(
                "Template-create commit was confirmed independently; preserving "
                "retained source tenant=%s template=%s",
                tenant_id,
                template_id,
            )
            return committed_snapshot
        if outcome is False:
            cleaned = await _remove_uncommitted_template_source(
                tenant_id=uuid.UUID(tenant_id),
                template_id=template_id,
                source_path=source_path,
            )
            if not cleaned:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Template creation did not commit and retained-source cleanup "
                        "failed. Do not retry until an operator reconciles the staged "
                        "source."
                    ),
                ) from exc
            raise HTTPException(
                status_code=500,
                detail=(
                    "Template creation did not commit; the staged source was removed. "
                    "Retry when the database is healthy."
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=(
                "Template creation commit outcome could not be verified. The retained "
                "source was preserved; do not retry until an operator reconciles the "
                "template row and source file."
            ),
        ) from exc
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
    """Render a source-backed PDF or DOCX without storing a matter document."""
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    template = await db.scalar(
        select(DocumentTemplate)
        .where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    template_format = str(template.format or "").lower()
    if template_format not in {"pdf", "docx"}:
        raise HTTPException(
            status_code=409,
            detail="File preview is available only for source-backed PDF or DOCX templates",
        )
    if template_format == "docx":
        source = await _verified_template_source(template)
        try:
            output = await asyncio.to_thread(
                fill_docx_template,
                source,
                variable_schema=template.variable_schema,
                variables=payload.variables,
                enforce_required=False,
            )
        except TemplateDocxError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = _safe_generated_filename(template.title, "docx")
        return Response(
            content=output,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Pragma": "no-cache",
            },
        )
    purpose = payload.preview_purpose
    if purpose == "activation" and payload.matter_id:
        raise HTTPException(
            status_code=422,
            detail="Activation previews are template-wide and cannot be tied to a matter.",
        )
    if purpose == "generation" and not template.is_active:
        raise HTTPException(
            status_code=409,
            detail="Activate this template before previewing a matter-ready PDF.",
        )
    matter = await _load_render_matter(
        db,
        tenant_id=tenant_id,
        matter_id=payload.matter_id,
    )
    if purpose == "generation" and matter is None:
        raise HTTPException(
            status_code=422,
            detail="Choose a matter before previewing the exact PDF values for save.",
        )
    if purpose == "activation":
        try:
            validate_representative_pdf_variables(
                template.variable_schema,
                payload.variables,
            )
        except TemplatePdfError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = await _verified_template_source(template)
    try:
        output = await asyncio.to_thread(
            fill_pdf_template,
            source,
            variable_schema=template.variable_schema,
            variables=payload.variables,
            flatten=payload.flatten_pdf,
            enforce_required=purpose == "activation",
        )
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    reviewed_fields, nonblank_count = pdf_review_evidence(
        template.variable_schema,
        payload.variables,
    )
    previewed_at = datetime.now(timezone.utc)
    evidence = DocumentTemplatePreview(
        tenant_id=tenant_id,
        template_id=template.id,
        previewed_by_user_id=current_user.id,
        matter_id=matter.id if matter else None,
        purpose=purpose,
        contract_sha256=_pdf_contract_sha256(template),
        values_hmac_sha256=_pdf_values_hmac_sha256(
            variables=payload.variables,
            flatten_pdf=payload.flatten_pdf,
            matter_id=matter.id if matter else None,
        ),
        output_sha256=hashlib.sha256(output).hexdigest(),
        renderer_version=_PDF_RENDERER_VERSION,
        flatten_pdf=payload.flatten_pdf,
        reviewed_field_count=len(reviewed_fields),
        nonblank_field_count=nonblank_count,
        reviewed_field_names=reviewed_fields,
        created_at=previewed_at,
        expires_at=previewed_at + _PDF_PREVIEW_TTLS[purpose],
    )
    db.add(evidence)
    await db.flush()
    await _trim_preview_evidence(
        db,
        tenant_id=tenant_id,
        template_id=template.id,
        user_id=current_user.id,
        purpose=purpose,
    )
    # Only a representative, flattened preview is activation evidence. Draft
    # and generation previews remain side-effect free with respect to template
    # lifecycle state and matter storage.
    if purpose == "activation" and payload.flatten_pdf:
        template.last_test_rendered_at = previewed_at
    await db.commit()
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
            "X-Clarity-Preview-ID": str(evidence.id),
            "X-Clarity-Preview-Purpose": purpose,
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
        select(DocumentTemplate)
        .where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
        )
        .with_for_update()
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
    if current_format == "docx" and requested_format != "docx":
        raise HTTPException(
            status_code=422,
            detail="A source-backed DOCX template cannot be converted to another format in place.",
        )
    if current_format == "docx" and ("body" in updates or "variable_schema" in updates):
        raise HTTPException(
            status_code=422,
            detail="Upload a new Word source to change a DOCX template body or field map.",
        )
    if "format" in updates:
        updates["format"] = requested_format

    pdf_schema_update_requested = "variable_schema" in updates
    pdf_body_update_requested = "body" in updates
    # A PDF field map is only trustworthy relative to the immutable retained
    # source. Re-discover form controls or text-overlay locations whenever its
    # map changes or the template is activated.
    validate_pdf_contract = current_format == "pdf" and (
        "variable_schema" in updates
        or "body" in updates
        or updates.get("is_active") is True
    )
    if validate_pdf_contract:
        source = await _verified_template_source(template)
        try:
            rediscovered = await asyncio.to_thread(
                analyze_template_upload,
                file_bytes=source,
                filename=template.source_filename or "template.pdf",
                content_type=template.source_content_type or "application/pdf",
                title=template.title,
            )
        except TemplatePdfError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        discovered_fields = rediscovered.variable_schema.get("fields") or []
        if not discovered_fields:
            raise HTTPException(
                status_code=422,
                detail="A PDF template must contain at least one reviewed form or text-overlay field before activation.",
            )
        discovered_schema = rediscovered.variable_schema
        effective_schema = updates.get("variable_schema", template.variable_schema)
        updates["variable_schema"] = _reviewed_variable_schema(
            json.dumps(effective_schema), discovered_schema
        )
        mapped_variables = {
            str(field.get("name"))
            for field in (updates["variable_schema"].get("fields") or [])
            if isinstance(field, dict)
            and (
                field.get("pdf_field_name")
                or field.get("pdf_overlay")
                or field.get("pdf_overlays")
            )
        }
        effective_body = str(updates.get("body", template.body) or "")
        unknown_body_variables = (
            set(extract_template_variables(effective_body)) - mapped_variables
        )
        if unknown_body_variables:
            raise HTTPException(
                status_code=422,
                detail=(
                    "PDF body contains variables without source mappings: "
                    + ", ".join(sorted(unknown_body_variables))
                ),
            )

    pdf_contract_changed = current_format == "pdf" and (
        (
            pdf_schema_update_requested
            and updates["variable_schema"] != template.variable_schema
        )
        or (pdf_body_update_requested and updates["body"] != template.body)
    )
    requested_activation = updates.get("is_active") is True
    if pdf_contract_changed and requested_activation:
        raise HTTPException(
            status_code=409,
            detail=(
                "Preview the updated PDF successfully before activating it. "
                "Save the field-map change, run Preview, then activate the template."
            ),
        )
    if current_format == "pdf" and requested_activation and not template.is_active:
        effective_schema = updates.get("variable_schema", template.variable_schema)
        effective_body = str(updates.get("body", template.body) or "")
        expected_contract = _pdf_contract_sha256(
            template,
            variable_schema=effective_schema,
            body=effective_body,
        )
        activation_evidence = await db.scalar(
            select(DocumentTemplatePreview)
            .where(
                DocumentTemplatePreview.tenant_id == uuid.UUID(tenant_id),
                DocumentTemplatePreview.template_id == template.id,
                DocumentTemplatePreview.previewed_by_user_id == current_user.id,
                DocumentTemplatePreview.purpose == "activation",
                DocumentTemplatePreview.contract_sha256 == expected_contract,
                DocumentTemplatePreview.flatten_pdf.is_(True),
                DocumentTemplatePreview.matter_id.is_(None),
                DocumentTemplatePreview.expires_at > datetime.now(timezone.utc),
            )
            .order_by(DocumentTemplatePreview.created_at.desc())
            .limit(1)
        )
        if not activation_evidence:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Run a representative flattened PDF preview as this user "
                    "before activating it. Exercise every non-signature field, "
                    "inspect every page, then activate the unchanged template."
                ),
            )

    if pdf_contract_changed:
        # A field-map/body change invalidates the exact artifact that was tested
        # and approved. Existing active templates remain usable until edited.
        updates["is_active"] = False
        updates["last_test_rendered_at"] = None
        updates["approved_at"] = None
        updates["approved_by_user_id"] = None
    elif current_format == "pdf" and requested_activation and not template.is_active:
        updates["approved_at"] = datetime.now(timezone.utc)
        updates["approved_by_user_id"] = current_user.id
    elif (
        current_format == "pdf"
        and updates.get("is_active") is False
        and template.is_active
    ):
        updates["approved_at"] = None
        updates["approved_by_user_id"] = None

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
    # Consumed evidence is part of the saved document's audit lifecycle. The
    # SET NULL template FK preserves consumed and reconciliation evidence after
    # template retirement. Draft/activation attempts have no records value.
    # Generation attempts are retained because a just-failed save may be
    # persisting a reconciliation block in a fresh transaction after rollback.
    await db.execute(
        delete(DocumentTemplatePreview).where(
            DocumentTemplatePreview.tenant_id == uuid.UUID(tenant_id),
            DocumentTemplatePreview.template_id == template.id,
            DocumentTemplatePreview.purpose.in_(["draft", "activation"]),
            DocumentTemplatePreview.consumed_at.is_(None),
            DocumentTemplatePreview.reconciliation_required_at.is_(None),
        )
    )
    await db.delete(template)
    await db.commit()
    if source_path:
        try:
            await asyncio.to_thread(source_path.unlink, missing_ok=True)
        except Exception:
            # The database deletion is already durable. Report the orphan for
            # operator cleanup without turning a successful, idempotent delete
            # into a misleading 500/then-404 sequence for the user.
            logger.exception(
                "Template source cleanup failed after committed delete "
                "tenant=%s template=%s",
                tenant_id,
                template_id,
            )


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
        select(DocumentTemplate)
        .where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == parsed_tenant_id,
        )
        .with_for_update()
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    matter = await _load_render_matter(
        db,
        tenant_id=parsed_tenant_id,
        matter_id=payload.matter_id,
    )
    if payload.matter_id and not template.is_active:
        raise HTTPException(
            status_code=409,
            detail="Only active templates can be saved to a matter. Preview and activate this template first.",
        )

    rendered = render_template(template.body, payload.variables)
    template_format = str(template.format or "").lower()
    output_format = (
        template_format
        if template_format in {"pdf", "docx"} and template.source_sha256
        else "markdown"
    )
    preview_evidence = None
    if output_format == "pdf" and matter is not None:
        if payload.preview_id is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Preview the exact current PDF values for this matter before "
                    "saving the generated document."
                ),
            )
        expected_contract = _pdf_contract_sha256(template)
        expected_values = _pdf_values_hmac_sha256(
            variables=payload.variables,
            flatten_pdf=payload.flatten_pdf,
            matter_id=matter.id,
        )
        preview_evidence = await db.scalar(
            select(DocumentTemplatePreview)
            .where(
                DocumentTemplatePreview.id == payload.preview_id,
                DocumentTemplatePreview.tenant_id == parsed_tenant_id,
            )
            .with_for_update()
        )
        if not preview_evidence:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The PDF preview expired, or its template, matter, output mode, "
                    "or field values changed. Preview the exact current values again "
                    "before save."
                ),
            )
        evidence_contract_matches = (
            preview_evidence.template_id == template.id
            and preview_evidence.previewed_by_user_id == current_user.id
            and preview_evidence.matter_id == matter.id
            and preview_evidence.purpose == "generation"
            and hmac.compare_digest(preview_evidence.contract_sha256, expected_contract)
            and hmac.compare_digest(
                preview_evidence.values_hmac_sha256, expected_values
            )
            and preview_evidence.flatten_pdf == payload.flatten_pdf
        )
        if not evidence_contract_matches:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The PDF preview expired, or its template, matter, output mode, "
                    "or field values changed. Preview the exact current values again "
                    "before save."
                ),
            )
        if (
            preview_evidence.reconciliation_required_at is not None
            and preview_evidence.reconciliation_resolved_at is None
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This PDF preview is blocked pending storage reconciliation. "
                    "Do not retry it; an operator must reconcile the staged object "
                    "and database outcome, then record a fresh preview."
                ),
            )
        if (
            preview_evidence.reconciliation_required_at is not None
            and preview_evidence.consumed_at is None
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This reconciled PDF preview has been retired. Record a fresh "
                    "preview before creating a document."
                ),
            )
        if preview_evidence.consumed_at is not None:
            existing_document = None
            if preview_evidence.consumed_by_document_id:
                existing_document = await db.scalar(
                    select(MatterDocument).where(
                        MatterDocument.id == preview_evidence.consumed_by_document_id,
                        MatterDocument.tenant_id == parsed_tenant_id,
                        MatterDocument.matter_id == matter.id,
                    )
                )
            if not existing_document:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This PDF preview was already consumed, but its saved "
                        "document is no longer available. Record a fresh preview "
                        "before creating another document."
                    ),
                )
            return DocumentTemplateRenderResponse(
                rendered=(
                    f'PDF already saved: "{existing_document.filename}"\n'
                    "Returning the document created by the original request."
                ),
                matter_document_id=str(existing_document.id),
                output_format="pdf",
                output_filename=existing_document.filename,
                download_url=(
                    f"/api/matters/{matter.id}/documents/"
                    f"{existing_document.id}/download"
                ),
                storage_backend=existing_document.storage_backend,
                storage_provider=existing_document.storage_provider,
                storage_warning=existing_document.storage_error,
            )
        if preview_evidence.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=409,
                detail=(
                    "The PDF preview expired, or its template, matter, output mode, "
                    "or field values changed. Preview the exact current values again "
                    "before save."
                ),
            )
    output_filename = _safe_generated_filename(
        template.title,
        {"pdf": "pdf", "docx": "docx"}.get(output_format, "md"),
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
    elif output_format == "docx":
        source = await _verified_template_source(template)
        try:
            output_bytes = await asyncio.to_thread(
                fill_docx_template,
                source,
                variable_schema=template.variable_schema,
                variables=payload.variables,
                enforce_required=bool(payload.matter_id),
            )
        except TemplateDocxError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rendered = (
            f'Word document ready: "{output_filename}"\n'
            f"Filled {sum(1 for value in payload.variables.values() if value)} reviewed field(s) while preserving the original DOCX layout."
        )
    else:
        output_bytes = rendered.encode("utf-8")
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()
    if preview_evidence and not hmac.compare_digest(
        output_sha256,
        preview_evidence.output_sha256,
    ):
        # The reviewed artifact is the release boundary. Even with the same
        # contract and values, a renderer/environment change must not silently
        # store bytes the user did not inspect.
        raise HTTPException(
            status_code=409,
            detail=(
                "The freshly rendered PDF does not match the reviewed preview. "
                "Preview the exact current values again before saving."
            ),
        )
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
    if matter is not None:
        parsed_matter_id = matter.id
        doc_id = uuid.uuid4()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_filename = _safe_generated_filename(
            f"{template.title}-{timestamp}-{doc_id.hex[:8]}",
            {"pdf": "pdf", "docx": "docx"}.get(output_format, "md"),
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
        content_type = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(output_format, "text/markdown")
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
        event = MatterEvent(
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
                "output_sha256": output_sha256,
                "filled_variables": sorted(
                    name for name, value in payload.variables.items() if value
                ),
                "flatten_pdf": payload.flatten_pdf if output_format == "pdf" else None,
                "renderer_version": (
                    _PDF_RENDERER_VERSION
                    if output_format == "pdf"
                    else "docx-source-v1"
                    if output_format == "docx"
                    else "markdown-v1"
                ),
                "preview_evidence_id": str(preview_evidence.id)
                if preview_evidence
                else None,
                "preview_contract_sha256": preview_evidence.contract_sha256
                if preview_evidence
                else None,
                "preview_values_hmac_sha256": preview_evidence.values_hmac_sha256
                if preview_evidence
                else None,
                "preview_output_sha256": preview_evidence.output_sha256
                if preview_evidence
                else None,
            },
            created_by=current_user.id,
        )
        reconciliation_preview_id = (
            preview_evidence.id if preview_evidence is not None else None
        )
        db.add_all([doc, event])
        try:
            # Flush the new document first so the evidence's audit FK can point
            # at a durable ID in the same transaction.
            await db.flush()
            if preview_evidence:
                preview_evidence.consumed_at = datetime.now(timezone.utc)
                preview_evidence.consumed_by_document_id = doc.id
                await db.flush()
        except Exception as exc:
            logger.exception(
                "Generated document pre-commit flush failed for tenant=%s matter=%s "
                "backend=%s provider_item_id=%s; compensating staged storage",
                tenant_id,
                parsed_matter_id,
                storage_result.backend,
                storage_result.provider_item_id,
            )
            await _rollback_quietly(
                db,
                tenant_id=parsed_tenant_id,
                matter_id=parsed_matter_id,
            )
            cleaned = await _compensate_staged_document(
                db,
                tenant_id=tenant_id,
                matter_id=parsed_matter_id,
                storage_result=storage_result,
            )
            if not cleaned:
                await _mark_preview_reconciliation_required(
                    tenant_id=parsed_tenant_id,
                    preview_id=reconciliation_preview_id,
                    reason="cleanup_failed",
                    storage_result=storage_result,
                    output_filename=output_filename,
                    output_sha256=output_sha256,
                    document_id=doc_id,
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "The document could not be finalized and automatic storage "
                        "cleanup failed. Do not retry until an operator reconciles "
                        "the staged file."
                    ),
                ) from exc
            raise HTTPException(
                status_code=500,
                detail=(
                    "The document could not be finalized; staged storage was removed. "
                    "Retry when the database is healthy."
                ),
            ) from exc

        commit_confirmed_independently = False
        try:
            await db.commit()
        except Exception as exc:
            # COMMIT failures are outcome-ambiguous: deleting immediately could
            # remove bytes referenced by a transaction PostgreSQL actually
            # committed before the acknowledgement was lost.
            logger.error(
                "Generated document commit acknowledgement failed tenant=%s "
                "matter=%s document=%s; checking outcome independently",
                tenant_id,
                parsed_matter_id,
                doc_id,
                exc_info=True,
            )
            rollback_succeeded = await _rollback_quietly(
                db,
                tenant_id=parsed_tenant_id,
                matter_id=parsed_matter_id,
            )
            outcome = (
                await _matter_document_commit_outcome(
                    tenant_id=parsed_tenant_id,
                    document_id=doc_id,
                )
                if rollback_succeeded
                else None
            )
            if outcome is True:
                commit_confirmed_independently = True
                logger.warning(
                    "Generated document commit was confirmed independently; "
                    "preserving storage tenant=%s document=%s",
                    tenant_id,
                    doc_id,
                )
            elif outcome is False:
                cleaned = await _compensate_staged_document(
                    db,
                    tenant_id=tenant_id,
                    matter_id=parsed_matter_id,
                    storage_result=storage_result,
                )
                if not cleaned:
                    await _mark_preview_reconciliation_required(
                        tenant_id=parsed_tenant_id,
                        preview_id=reconciliation_preview_id,
                        reason="cleanup_failed",
                        storage_result=storage_result,
                        output_filename=output_filename,
                        output_sha256=output_sha256,
                        document_id=doc_id,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "The document transaction did not commit and automatic "
                            "storage cleanup failed. Do not retry until an operator "
                            "reconciles the staged file."
                        ),
                    ) from exc
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "The document transaction did not commit; staged storage was "
                        "removed. Retry when the database is healthy."
                    ),
                ) from exc
            else:
                await _mark_preview_reconciliation_required(
                    tenant_id=parsed_tenant_id,
                    preview_id=reconciliation_preview_id,
                    reason="commit_outcome_unknown",
                    storage_result=storage_result,
                    output_filename=output_filename,
                    output_sha256=output_sha256,
                    document_id=doc_id,
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "The document commit outcome could not be verified. Staged "
                        "storage was preserved; do not retry until an operator "
                        "reconciles the document."
                    ),
                ) from exc
        if not commit_confirmed_independently:
            await db.refresh(doc)
        matter_document_id = str(doc_id)
        download_url = f"/api/matters/{parsed_matter_id}/documents/{doc_id}/download"

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
