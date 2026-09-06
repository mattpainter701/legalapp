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
import math
import os
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import async_session_maker, get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.document_template import DocumentTemplate
from app.models.document_template_version import DocumentTemplateVersion
from app.models.document_template_preview import DocumentTemplatePreview
from app.models.matter_document import MatterDocument
from app.models.matter_party import MatterParty
from app.models.plugin import Matter, MatterEvent
from app.models.tenant import TenantSettings
from app.schemas.document_template import (
    DocumentTemplateBindingCatalogue,
    DocumentTemplateOutlineResponse,
    DocumentTemplateVersionDetail,
    DocumentTemplateVersionListResponse,
    DocumentTemplateVersionSummary,
    DocumentTemplateBindingOption,
    DocumentTemplateCollectionOption,
    CATEGORIES,
    DocumentTemplateCreate,
    DocumentTemplateListResponse,
    DocumentTemplateQueueResponse,
    DocumentTemplateRenderRequest,
    DocumentTemplateRenderResponse,
    DocumentTemplatePublishRequest,
    DocumentTemplateResponse,
    DocumentTemplateSmartFillRequest,
    DocumentTemplateSmartFillResponse,
    DocumentTemplateUploadAnalysisResponse,
    DocumentTemplateUpdate,
    DocumentTemplateVariableSuggestion,
)
from app.schemas.matter_party import normalize_matter_party_role
from app.services import template_custom_fields, template_fact_review
from app.services.template_intake import (
    TemplateAnalysis,
    analyze_template_upload,
    prepare_template_source,
)
from app.services.template_ai_service import (
    TemplateAiAssistError,
    assist_template_mapping,
)
from app.services.template_ai_assist import (
    AiFieldProposal,
    reconcile_ai_template_fields,
)
from app.services.pdf_templates import (
    TemplatePdfError,
    fill_pdf_template,
    pdf_review_evidence,
    render_pdf_page_preview,
    validate_representative_pdf_variables,
)
from app.services.docx_templates import TemplateDocxError, fill_docx_template
from app.services.docx_to_pdf import DocxToPdfError, docx_to_pdf_bytes
from app.services.docx_outline import docx_outline, validate_visual_field_map
from app.services.template_regions import (
    TemplateRegionError,
    parse_regions,
    stored_regions,
)
from app.services.document_template_versions import (
    get_version,
    list_versions,
    record_version,
    snapshot_differs,
)
from app.services.template_logic import (
    OPERATORS as LOGIC_OPERATORS,
    TemplateLogicError,
    validate_condition,
    expand_markdown_logic,
    suppressed_fields,
)
from app.services.template_semantics import (
    TemplateSemanticsError,
    is_semantic_only_change,
    validate_semantic_metadata,
)
from app.services.template_bindings import (
    MANUAL_BINDING,
    alias_for_binding,
    binding_label,
    catalogue as binding_catalogue,
    collections as binding_collections,
    declared_bindings,
    is_item_binding,
    is_valid_binding,
)
from app.services.template_ocr import TemplateOcrError, image_to_pdf
from app.services.matter_file_store import MatterFileStore
from app.services.access_control import require_capability, require_capabilities
from app.utils.text_processing import extract_text
from app.utils.sql_filters import escape_like

router = APIRouter(prefix="/api/templates", tags=["document-templates"])
logger = logging.getLogger(__name__)
settings = get_settings()
matter_file_store = MatterFileStore()
VARIABLE_PATTERN = re.compile(r"\{\{(.+?)\}\}")
_IMAGE_TEMPLATE_EXTENSIONS = (
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
)
_IMAGE_TEMPLATE_CONTENT_TYPES = {
    "image/bmp",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}
_ALLOWED_TEMPLATE_UPLOAD_EXTENSIONS = (
    ".docx",
    ".pdf",
    ".txt",
    *_IMAGE_TEMPLATE_EXTENSIONS,
)
_ALLOWED_TEMPLATE_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    *_IMAGE_TEMPLATE_CONTENT_TYPES,
}
_PDF_RENDERER_VERSION = "pdf-source-v4-ocr-preview-bound"
_DOCX_PDF_RENDERER_VERSION = "docx-libreoffice-pdf-v1-preview-bound"
_MAX_PERSISTED_PREVIEWS_PER_USER_PURPOSE = 50
_PDF_PREVIEW_TTLS = {
    "draft": timedelta(hours=1),
    "activation": timedelta(hours=24),
    "generation": timedelta(minutes=30),
}
_COMMIT_OUTCOME_DELAYS = (0, 0.1, 0.3, 0.6, 1.0)
_ANALYSIS_TOKEN_SALT = "document-template-intake-v1"
_ANALYSIS_TOKEN_MAX_AGE_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class TemplateUploadSample:
    content: bytes
    filename: str
    content_type: str
    original_filename: str
    warnings: tuple[str, ...] = ()


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


def _analysis_token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.SECRET_KEY,
        salt=_ANALYSIS_TOKEN_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def _issue_analysis_token(
    *,
    analysis: dict[str, Any],
    file_bytes: bytes,
    filename: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str:
    """Bind a reviewed intake result to the exact user, tenant, and source.

    The token is signed rather than stored in process memory so it remains
    valid across API workers.  It travels in multipart form data (never a URL)
    and expires quickly.  Creation still validates every user-edited field
    against the signed server-discovered map.
    """

    return _analysis_token_serializer().dumps(
        {
            "version": 1,
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "filename": _safe_upload_filename(filename),
            "source_sha256": hashlib.sha256(file_bytes).hexdigest(),
            "analysis": analysis,
        }
    )


def _analysis_from_token(
    token: str,
    *,
    file_bytes: bytes,
    filename: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    detail = (
        "This document analysis is no longer current. Analyze the source again "
        "before saving the template."
    )
    if len(token) > 250_000:
        raise HTTPException(status_code=409, detail=detail)
    try:
        payload = _analysis_token_serializer().loads(
            token,
            max_age=_ANALYSIS_TOKEN_MAX_AGE_SECONDS,
        )
    except (SignatureExpired, BadData) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise HTTPException(status_code=409, detail=detail)
    expected_source_sha256 = str(payload.get("source_sha256") or "")
    actual_source_sha256 = hashlib.sha256(file_bytes).hexdigest()
    claims_match = (
        hmac.compare_digest(str(payload.get("tenant_id") or ""), str(tenant_id))
        and hmac.compare_digest(str(payload.get("user_id") or ""), str(user_id))
        and hmac.compare_digest(expected_source_sha256, actual_source_sha256)
        and hmac.compare_digest(
            str(payload.get("filename") or ""),
            _safe_upload_filename(filename),
        )
    )
    analysis = payload.get("analysis")
    required = {
        "title",
        "format",
        "body",
        "body_preview",
        "extracted_text",
        "suggested_variable_schema",
        "detected_branding_profile",
        "warnings",
    }
    if (
        not claims_match
        or not isinstance(analysis, dict)
        or not required <= analysis.keys()
    ):
        raise HTTPException(status_code=409, detail=detail)
    return analysis


def _analysis_from_snapshot(
    snapshot: dict[str, Any],
    *,
    source_bytes: bytes,
    source_filename: str,
    source_content_type: str,
    source_format: str,
    requested_title: str | None,
) -> TemplateAnalysis:
    """Rehydrate a signed analysis while attaching freshly verified source bytes."""

    if str(snapshot.get("format") or "").lower() != source_format:
        raise HTTPException(
            status_code=409,
            detail=(
                "This document analysis no longer matches the uploaded source. "
                "Analyze the source again before saving the template."
            ),
        )
    return TemplateAnalysis(
        title=requested_title or str(snapshot["title"]),
        format=source_format,
        body=str(snapshot["body"]),
        body_preview=str(snapshot["body_preview"]),
        extracted_text=str(snapshot["extracted_text"]),
        source_text=str(snapshot.get("source_text") or snapshot["extracted_text"]),
        variable_schema=dict(snapshot["suggested_variable_schema"]),
        branding_profile=dict(snapshot["detected_branding_profile"]),
        warnings=[str(item) for item in snapshot["warnings"]],
        _normalized_source_bytes=source_bytes,
        _normalized_source_filename=source_filename,
        _normalized_source_content_type=source_content_type,
    )


async def _analysis_for_template_create(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    requested_title: str | None,
    analysis_token: str | None,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> TemplateAnalysis:
    if not analysis_token:
        return await asyncio.to_thread(
            analyze_template_upload,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            title=requested_title,
        )

    snapshot = _analysis_from_token(
        analysis_token,
        file_bytes=file_bytes,
        filename=filename,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    prepared = await asyncio.to_thread(
        prepare_template_source,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
    )
    analysis = _analysis_from_snapshot(
        snapshot,
        source_bytes=prepared.source_bytes,
        source_filename=prepared.filename,
        source_content_type=prepared.content_type,
        source_format=prepared.format,
        requested_title=requested_title,
    )
    if prepared.format == "docx":
        analysis.source_text = await asyncio.to_thread(
            extract_text,
            prepared.source_bytes,
            prepared.content_type,
            prepared.filename,
        )
    return analysis


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
    schema_contract_fields = []
    for field in (schema or {}).get("fields") or []:
        if not isinstance(field, dict):
            schema_contract_fields.append(field)
            continue
        normalized_field = dict(field)
        # Intake schemas historically omitted ``included`` when a discovered
        # field used the default (included) state. Revalidation makes that
        # default explicit, but the rendered contract is unchanged.
        normalized_field.setdefault("included", True)
        schema_contract_fields.append(normalized_field)
    schema_contract = {"fields": schema_contract_fields}
    if (schema or {}).get("applicability"):
        schema_contract["applicability"] = schema["applicability"]
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


def _preview_mismatch_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            "The PDF preview expired, or its template, matter, output mode, "
            "or field values changed. Preview the exact current values again "
            "before save."
        ),
    )


def _template_response(template: DocumentTemplate) -> DocumentTemplateResponse:
    response = DocumentTemplateResponse.model_validate(template)
    template_format = str(template.format or "").lower()
    source_ready = template_format not in {"pdf", "docx"} or bool(
        template.source_storage_path
        and template.source_filename
        and template.source_sha256
        and template.source_file_size
        and template.source_file_size > 0
    )
    return response.model_copy(update={"source_ready": source_ready})


async def _load_generation_preview_evidence(
    db: AsyncSession,
    *,
    preview_id: uuid.UUID,
    tenant_id: uuid.UUID,
    template: DocumentTemplate,
    matter: Matter,
    user_id: uuid.UUID,
    variables: dict[str, str],
    flatten_pdf: bool,
    lock: bool,
) -> tuple[DocumentTemplatePreview, MatterDocument | None]:
    stmt = select(DocumentTemplatePreview).where(
        DocumentTemplatePreview.id == preview_id,
        DocumentTemplatePreview.tenant_id == tenant_id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    evidence = await db.scalar(stmt)
    if not evidence:
        raise _preview_mismatch_error()

    expected_contract = _pdf_contract_sha256(template)
    expected_values = _pdf_values_hmac_sha256(
        variables=variables,
        flatten_pdf=flatten_pdf,
        matter_id=matter.id,
    )
    if not (
        evidence.template_id == template.id
        and evidence.previewed_by_user_id == user_id
        and evidence.matter_id == matter.id
        and evidence.purpose == "generation"
        and hmac.compare_digest(evidence.contract_sha256, expected_contract)
        and hmac.compare_digest(evidence.values_hmac_sha256, expected_values)
        and evidence.flatten_pdf == flatten_pdf
    ):
        raise _preview_mismatch_error()
    if (
        evidence.reconciliation_required_at is not None
        and evidence.reconciliation_resolved_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This PDF preview is blocked pending storage reconciliation. "
                "Do not retry it; an operator must reconcile the staged object "
                "and database outcome, then record a fresh preview."
            ),
        )
    if evidence.reconciliation_required_at is not None and evidence.consumed_at is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This reconciled PDF preview has been retired. Record a fresh "
                "preview before creating a document."
            ),
        )
    existing_document = None
    if evidence.consumed_at is not None:
        if evidence.consumed_by_document_id:
            existing_document = await db.scalar(
                select(MatterDocument).where(
                    MatterDocument.id == evidence.consumed_by_document_id,
                    MatterDocument.tenant_id == tenant_id,
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
        # Successful retries remain idempotent even after the short preview
        # evidence window expires. The original save already established the
        # reviewed output and the existing document is the authoritative result.
        return evidence, existing_document
    if evidence.expires_at <= datetime.now(timezone.utc):
        raise _preview_mismatch_error()
    return evidence, existing_document


def _existing_document_response(
    document: MatterDocument, *, matter_id: uuid.UUID
) -> DocumentTemplateRenderResponse:
    return DocumentTemplateRenderResponse(
        rendered=(
            f'PDF already saved: "{document.filename}"\n'
            "Returning the document created by the original request."
        ),
        matter_document_id=str(document.id),
        output_format="pdf",
        output_filename=document.filename,
        download_url=f"/api/matters/{matter_id}/documents/{document.id}/download",
        storage_backend=document.storage_backend,
        storage_provider=document.storage_provider,
        storage_warning=document.storage_error,
    )


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
                    return True, _template_response(template)
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
            existing_marker = await reconciliation_db.scalar(
                select(DocumentTemplatePreview).where(
                    DocumentTemplatePreview.tenant_id == tenant_id,
                    DocumentTemplatePreview.reconciliation_document_id == document_id,
                    DocumentTemplatePreview.reconciliation_required_at.is_not(None),
                    DocumentTemplatePreview.reconciliation_resolved_at.is_(None),
                )
            )
            if existing_marker:
                return True
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
                if reason != "cleanup_failed":
                    logger.warning(
                        "Preview reconciliation marker skipped because committed "
                        "consumption is independently visible tenant=%s preview=%s "
                        "document=%s",
                        tenant_id,
                        preview_id,
                        evidence.consumed_by_document_id,
                    )
                    return False
                # A concurrent retry can stage the same reviewed output before
                # the original transaction consumes this preview. If deleting
                # that losing staged object fails, preserve the successful
                # consumption and create a separate terminal operations record
                # for the orphan candidate.
                marker = DocumentTemplatePreview(
                    tenant_id=tenant_id,
                    template_id=evidence.template_id,
                    previewed_by_user_id=evidence.previewed_by_user_id,
                    matter_id=evidence.matter_id,
                    purpose=evidence.purpose,
                    contract_sha256=evidence.contract_sha256,
                    values_hmac_sha256=evidence.values_hmac_sha256,
                    output_sha256=evidence.output_sha256,
                    renderer_version=evidence.renderer_version,
                    flatten_pdf=evidence.flatten_pdf,
                    reviewed_field_count=evidence.reviewed_field_count,
                    nonblank_field_count=evidence.nonblank_field_count,
                    reviewed_field_names=evidence.reviewed_field_names,
                    expires_at=evidence.expires_at,
                )
                reconciliation_db.add(marker)
            else:
                if evidence.reconciliation_required_at is not None:
                    return True
                marker = evidence
            marker.reconciliation_required_at = datetime.now(timezone.utc)
            marker.reconciliation_reason = reason
            marker.reconciliation_storage_backend = backend
            marker.reconciliation_provider_item_id = (
                str(storage_result.provider_item_id)[:500]
                if storage_result.provider_item_id
                else None
            )
            marker.reconciliation_provider_drive_id = (
                str(storage_result.drive_id)[:500] if storage_result.drive_id else None
            )
            marker.reconciliation_local_path = local_path
            marker.reconciliation_output_filename = output_filename[:500]
            marker.reconciliation_output_sha256 = output_sha256
            marker.reconciliation_document_id = document_id
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


async def _discard_staged_generation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    preview_id: uuid.UUID,
    storage_result,
    output_filename: str,
    output_sha256: str,
    document_id: uuid.UUID,
) -> None:
    """Release finalization locks and remove a staged-but-uncommitted PDF."""
    rolled_back = await _rollback_quietly(
        db,
        tenant_id=tenant_id,
        matter_id=matter_id,
    )
    cleaned = rolled_back and await _compensate_staged_document(
        db,
        tenant_id=str(tenant_id),
        matter_id=matter_id,
        storage_result=storage_result,
    )
    if cleaned:
        return
    await _mark_preview_reconciliation_required(
        tenant_id=tenant_id,
        preview_id=preview_id,
        reason="cleanup_failed",
        storage_result=storage_result,
        output_filename=output_filename,
        output_sha256=output_sha256,
        document_id=document_id,
    )
    raise HTTPException(
        status_code=500,
        detail=(
            "The generated PDF could not be finalized and automatic storage "
            "cleanup failed. Do not retry until an operator reconciles the staged file."
        ),
    )


def _reconcile_submitted_ai_fields(
    raw: str | None,
    *,
    analysis,
    file_bytes: bytes,
) -> None:
    """Re-locate submitted AI proposals without trusting client coordinates."""

    if raw is None or not raw.strip():
        return
    try:
        submitted = json.loads(raw)
    except json.JSONDecodeError:
        return
    fields = submitted.get("fields") if isinstance(submitted, dict) else None
    if not isinstance(fields, list):
        return
    proposals: list[AiFieldProposal] = []
    try:
        for field in fields:
            if not isinstance(field, dict) or not field.get("ai_suggested"):
                continue
            proposals.append(
                AiFieldProposal(
                    existing_name=(
                        str(field.get("ai_existing_name") or "").strip() or None
                    )
                    if field.get("ai_update_kind") == "updated"
                    else None,
                    name=str(field.get("name") or ""),
                    label=str(field.get("label") or field.get("name") or ""),
                    source_text=str(
                        field.get("source_text") or field.get("example") or ""
                    ),
                    field_type=str(field.get("field_type") or "text"),
                    confidence=min(
                        0.75,
                        max(0.0, float(field.get("confidence") or 0.5)),
                    ),
                    reason=str(field.get("ai_reason") or ""),
                )
            )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="An AI-proposed field is invalid. Run the proposal again and review it.",
        ) from exc
    if len(proposals) > 40:
        raise HTTPException(
            status_code=422,
            detail="A premium AI proposal may contain at most 40 fields.",
        )
    if proposals:
        mapped, unmapped = reconcile_ai_template_fields(
            analysis=analysis,
            file_bytes=file_bytes,
            proposals=proposals,
        )
        added_count = sum(field.get("ai_update_kind") == "added" for field in mapped)
        updated_count = sum(
            field.get("ai_update_kind") == "updated" for field in mapped
        )
        analysis.variable_schema.setdefault("detection", {}).update(
            {
                "ai_assisted": True,
                "ai_added_count": added_count,
                "ai_updated_count": updated_count,
                "ai_unmapped_count": len(unmapped),
                "review_required": True,
            }
        )


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
    signed_pages = {
        int(page.get("page")): page
        for page in (discovered.get("pages") or [])
        if isinstance(page, dict) and page.get("page") is not None
    }
    discovered_docx_keys = {
        str(field.get("docx_source_key"))
        for field in (discovered.get("fields") or [])
        if isinstance(field, dict) and field.get("docx_source_key")
    }
    discovered_by_docx_key = {
        str(field.get("docx_source_key")): field
        for field in (discovered.get("fields") or [])
        if isinstance(field, dict) and field.get("docx_source_key")
    }

    def _safe_rect(value, *, page_number: int, label: str) -> list[float]:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise HTTPException(
                status_code=422, detail=f"{label} must be a four-number rectangle"
            )
        try:
            rect = [float(item) for item in value]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"{label} must be a four-number rectangle"
            ) from exc
        if not all(math.isfinite(item) for item in rect):
            raise HTTPException(
                status_code=422, detail=f"{label} must contain finite numbers"
            )
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            raise HTTPException(
                status_code=422, detail=f"{label} must have positive size"
            )
        page = signed_pages.get(page_number)
        if (
            page is None
            or rect[0] < 0
            or rect[1] < 0
            or (
                rect[2] > float(page.get("width", 0))
                or rect[3] > float(page.get("height", 0))
            )
        ):
            raise HTTPException(
                status_code=422, detail=f"{label} falls outside its signed page bounds"
            )
        return rect

    def _review_bool(field: dict, key: str, default: bool = False) -> bool:
        value = field.get(key, default)
        if not isinstance(value, bool):
            raise HTTPException(
                status_code=422, detail=f"PDF field {key} must be boolean"
            )
        return value

    def _review_overlay_specs(field: dict, authoritative: dict) -> list[dict]:
        submitted = field.get("pdf_overlays") or [field.get("pdf_overlay")]
        original = authoritative.get("pdf_overlays") or [
            authoritative.get("pdf_overlay")
        ]
        if not isinstance(submitted, list) or len(submitted) != len(original):
            raise HTTPException(
                status_code=422, detail="Reviewed PDF overlay count cannot change"
            )
        reviewed: list[dict] = []
        for candidate, source in zip(submitted, original):
            if not isinstance(candidate, dict) or not isinstance(source, dict):
                raise HTTPException(
                    status_code=422, detail="Reviewed PDF overlay mapping is invalid"
                )
            page_number = int(source.get("page"))
            rect = _safe_rect(
                candidate.get("rect", source.get("rect")),
                page_number=page_number,
                label="PDF overlay rectangle",
            )
            immutable = dict(source)
            immutable["rect"] = rect
            immutable["source_rect"] = _safe_rect(
                source.get("source_rect", source.get("rect")),
                page_number=page_number,
                label="PDF source rectangle",
            )
            for key in (
                "page",
                "source_text",
                "source_kind",
                "pdf_source_key",
                "erase_source",
            ):
                if key in source:
                    immutable[key] = source[key]
            reviewed.append(immutable)
        return reviewed

    seen_names: set[str] = set()
    seen_pdf_names: set[str] = set()
    seen_overlay_keys: set[str] = set()
    seen_docx_keys: set[str] = set()
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
        field["name"] = name
        binding = field.get("binding")
        if binding is not None:
            binding = str(binding).strip()
            if not binding:
                field.pop("binding", None)
            elif not is_valid_binding(binding):
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown data binding for {name!r}: {binding}",
                )
            else:
                field["binding"] = binding
        docx_key = field.get("docx_source_key")
        if docx_key is not None:
            docx_key = str(docx_key)
            if docx_key not in discovered_docx_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown Word field mapping: {name!r}",
                )
            if docx_key in seen_docx_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"Duplicate Word field mapping: {name!r}",
                )
            seen_docx_keys.add(docx_key)
            authoritative = discovered_by_docx_key[docx_key]
            field["docx_source_key"] = docx_key
            field["docx_anchor"] = authoritative.get("docx_anchor")
            field["source_text"] = authoritative.get("source_text")
            if authoritative.get("example") is not None:
                field["example"] = authoritative.get("example")
        elif field.get("docx_anchor") is not None and discovered_docx_keys:
            raise HTTPException(
                status_code=422,
                detail=f"Word variable {name!r} is missing its reviewed source mapping",
            )
        pdf_name = field.get("pdf_field_name")
        overlay_key = field.get("pdf_source_key")
        has_pdf_mapping = pdf_name is not None
        has_overlay_mapping = bool(
            field.get("pdf_overlay") or field.get("pdf_overlays")
        )
        is_manual = isinstance(overlay_key, str) and overlay_key.startswith("manual:")
        if is_manual:
            try:
                uuid.UUID(overlay_key.split(":", 1)[1])
            except (ValueError, IndexError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="Manual PDF source key must contain a valid UUID",
                ) from exc
            if not has_overlay_mapping:
                raise HTTPException(
                    status_code=422,
                    detail=f"Manual PDF variable {name!r} needs an overlay mapping",
                )
            field_type = str(field.get("field_type") or "text")
            if field_type not in {"text", "date", "checkbox", "signature"}:
                raise HTTPException(
                    status_code=422,
                    detail="Manual PDF fields support text, date, checkbox, or signature",
                )
            specs = field.get("pdf_overlays") or [field.get("pdf_overlay")]
            if (
                not isinstance(specs, list)
                or len(specs) != 1
                or not isinstance(specs[0], dict)
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Manual PDF fields support exactly one overlay",
                )
            spec = dict(specs[0])
            try:
                page_number = int(spec.get("page"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422, detail="Manual PDF overlay page must be an integer"
                ) from exc
            spec["rect"] = _safe_rect(
                spec.get("rect"),
                page_number=page_number,
                label="Manual PDF overlay rectangle",
            )
            spec.update(
                {
                    "pdf_source_key": overlay_key,
                    "source_kind": "manual",
                    "erase_source": False,
                }
            )
            field["pdf_overlay"] = spec
            field.pop("pdf_overlays", None)
            field["pdf_source_key"] = overlay_key
            field["included"] = _review_bool(field, "included", True)
            field["required"] = _review_bool(field, "required")
            field["multiline"] = _review_bool(field, "multiline")
            continue
        if has_pdf_mapping and has_overlay_mapping:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"PDF variable {name!r} cannot combine AcroForm and "
                    "overlay mappings"
                ),
            )
        if (discovered_pdf_names or discovered_overlay_keys) and not (
            has_pdf_mapping or has_overlay_mapping
        ):
            raise HTTPException(
                status_code=422,
                detail=f"PDF variable {name!r} is missing its source mapping",
            )
        if has_pdf_mapping:
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
            submitted_required = _review_bool(field, "required")
            for key in (
                "pdf_field_name",
                "pdf_source_key",
                "field_type",
                "multiline",
                "options",
                "page",
                "rect",
            ):
                field[key] = authoritative.get(key)
            # The source controls geometry/type/options and a source-required
            # field can never be weakened, but review may promote an optional
            # AcroForm field to required for downstream automation.
            field["required"] = bool(
                authoritative.get("required") or submitted_required
            )
            field["included"] = _review_bool(field, "included", True)
        if has_overlay_mapping:
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
            field_type = str(
                field.get("field_type") or authoritative.get("field_type") or "text"
            )
            if field_type not in {"text", "date", "checkbox", "signature"}:
                raise HTTPException(
                    status_code=422,
                    detail="PDF overlays support text, date, checkbox, or signature",
                )
            field["field_type"] = field_type
            field["required"] = bool(
                authoritative.get("required") or _review_bool(field, "required")
            )
            field["multiline"] = _review_bool(
                field, "multiline", bool(authoritative.get("multiline", False))
            )
            field["included"] = _review_bool(field, "included", True)
            for key in ("page", "rect", "source_text"):
                field[key] = authoritative.get(key)
            reviewed = _review_overlay_specs(field, authoritative)
            field["pdf_overlays"] = reviewed
            field["pdf_overlay"] = reviewed[0]
            field["pdf_source_key"] = authoritative.get("pdf_source_key")
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
    if discovered_docx_keys and seen_docx_keys != discovered_docx_keys:
        raise HTTPException(
            status_code=422,
            detail="Reviewed Word schema must preserve every detected source mapping",
        )
    if schema.get("regions") is not None:
        try:
            schema["regions"] = [
                region.as_dict()
                for region in parse_regions(schema["regions"], known_fields=seen_names)
            ]
        except TemplateRegionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Conditions are checked once the full name set is known, so logic that
    # references a field the template does not define is rejected at save time
    # rather than silently dropping a clause at generation time.
    for field in schema["fields"]:
        if isinstance(field, dict) and field.get("logic") is not None:
            try:
                validate_condition(
                    field["logic"],
                    known_fields=seen_names,
                    label=str(field.get("name") or ""),
                )
            except TemplateLogicError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        schema["version"] = max(1, int(schema.get("version") or 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="variable_schema.version must be an integer"
        ) from exc
    if isinstance(discovered.get("detection"), dict):
        schema["detection"] = discovered["detection"]
    if discovered.get("pages"):
        # Page geometry is signed/server-discovered metadata; clients may not
        # rewrite it while reviewing placements.
        schema["pages"] = discovered["pages"]
    schema.pop("ai_proposal", None)
    schema.pop("unmapped_ai_suggestions", None)
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


async def _read_template_sample(file: UploadFile) -> TemplateUploadSample:
    original_filename = _safe_upload_filename(file.filename)
    if not _is_allowed_template_sample(original_filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: DOCX, PDF, TXT, PNG, JPEG, TIFF, BMP, and WebP.",
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
    media_type = _normalized_media_type(file.content_type)
    filename_lower = original_filename.lower()
    is_image = (
        filename_lower.endswith(_IMAGE_TEMPLATE_EXTENSIONS)
        or media_type in _IMAGE_TEMPLATE_CONTENT_TYPES
    )
    if is_image:
        try:
            normalized = await asyncio.to_thread(image_to_pdf, file_bytes)
        except TemplateOcrError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        normalized_filename = _safe_upload_filename(
            f"{Path(original_filename).stem or 'scanned-template'}.pdf"
        )
        return TemplateUploadSample(
            content=normalized.content,
            filename=normalized_filename,
            content_type="application/pdf",
            original_filename=original_filename,
            warnings=(
                f"The {normalized.image_format} image was converted to a safe {normalized.pages}-page PDF for OCR and reusable field placement. Review every detected field.",
            ),
        )

    if filename_lower.endswith(".pdf") or media_type == "application/pdf":
        normalized_content_type = "application/pdf"
    elif filename_lower.endswith(".docx"):
        normalized_content_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    else:
        normalized_content_type = "text/plain"
    return TemplateUploadSample(
        content=file_bytes,
        filename=original_filename,
        content_type=normalized_content_type,
        original_filename=original_filename,
    )


def render_template(
    template_body: str,
    variables: dict[str, str],
    *,
    collections: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    """Resolve template logic, then replace {{variable}} placeholders.

    Logic runs first and never writes a value into the body, so a customer's
    value can never be reinterpreted as a block marker. Substitution then runs
    once over the expanded body.
    """

    expanded, scoped = expand_markdown_logic(
        template_body, variables, collections=collections
    )
    resolved = {**variables, **scoped}

    def replacer(match):
        key = match.group(1).strip()
        return resolved.get(key, match.group(0))

    return VARIABLE_PATTERN.sub(replacer, expanded)


def extract_template_variables(template_body: str) -> list[str]:
    """Return the substitutable variables in a body, in first-seen order.

    Logic markers (``{{#if x}}``, ``{{/each}}``) share the placeholder syntax
    but are not variables: they are never filled, never smart-filled, and must
    not be reported to callers that validate a field map against the body.
    """

    variables: list[str] = []
    seen: set[str] = set()
    for match in VARIABLE_PATTERN.finditer(template_body):
        variable = match.group(1).strip()
        if variable.startswith(("#", "/")):
            continue
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
    provenance: dict[str, Any] | None = None,
) -> None:
    suggested_value = _stringify_suggestion(value)
    if suggested_value is None:
        return
    key = _normalize_variable_name(alias)
    candidate_provenance = {
        "source_type": source_type,
        "source_field": source_field,
        "record_id": str(record_id) if record_id else None,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    if provenance:
        candidate_provenance.update(provenance)
    candidates.setdefault(
        key,
        DocumentTemplateVariableSuggestion(
            variable=alias,
            suggested_value=suggested_value,
            source_type=source_type,
            source_field=source_field,
            provenance=candidate_provenance,
            confidence=confidence,
            review_required=review_required,
        ),
    )


def _address_value(address: dict | None, key: str) -> str | None:
    if not isinstance(address, dict):
        return None
    return _stringify_suggestion(address.get(key))


def _caption_parties(parties: Sequence[MatterParty], role: str) -> list[MatterParty]:
    matching: list[MatterParty] = []
    for party in parties:
        try:
            party_role = normalize_matter_party_role(getattr(party, "role", "other"))
        except ValueError:
            continue
        if party_role != role:
            continue
        if not _stringify_suggestion(
            getattr(getattr(party, "contact", None), "display_name", None)
        ):
            continue
        matching.append(party)
    return sorted(
        matching,
        key=lambda party: (
            not bool(getattr(party, "is_primary", False)),
            str(getattr(party, "created_at", "")),
            str(getattr(party, "id", "")),
        ),
    )


def _collect_caption_party_candidates(
    candidates: dict[str, DocumentTemplateVariableSuggestion],
    parties: Sequence[MatterParty],
) -> None:
    for role in ("plaintiff", "defendant"):
        role_parties = _caption_parties(parties, role)
        if not role_parties:
            continue

        primary_party = role_parties[0]
        primary_contact = primary_party.contact
        primary_name = primary_contact.display_name
        selection = (
            "primary"
            if bool(getattr(primary_party, "is_primary", False))
            else "first_listed"
        )
        singular_provenance = {
            "party_role": role,
            "selection": selection,
            "contact_id": str(primary_contact.id),
        }
        for alias in (role, f"{role}_name"):
            _add_candidate(
                candidates,
                alias,
                primary_name,
                source_type="matter_party",
                source_field="contact.display_name",
                record_id=primary_party.id,
                provenance=singular_provenance,
            )

        unique_names = list(
            dict.fromkeys(party.contact.display_name for party in role_parties)
        )
        all_party_ids = [str(party.id) for party in role_parties]
        for alias in (f"{role}s", f"{role}_names"):
            _add_candidate(
                candidates,
                alias,
                "; ".join(unique_names),
                source_type="matter_parties",
                source_field="contacts.display_name",
                provenance={
                    "party_role": role,
                    "selection": "all",
                    "record_ids": all_party_ids,
                },
            )

        for suffix, value, source_field in (
            ("email", primary_contact.email, "contact.email"),
            ("phone", primary_contact.phone, "contact.phone"),
        ):
            _add_candidate(
                candidates,
                f"{role}_{suffix}",
                value,
                source_type="matter_party",
                source_field=source_field,
                record_id=primary_party.id,
                provenance=singular_provenance,
            )
        for suffix, address_key in (
            ("street", "street"),
            ("city", "city"),
            ("state", "state"),
            ("zip", "zip"),
            ("country", "country"),
        ):
            _add_candidate(
                candidates,
                f"{role}_{suffix}",
                _address_value(primary_contact.address, address_key),
                source_type="matter_party",
                source_field=f"contact.address.{address_key}",
                record_id=primary_party.id,
                provenance=singular_provenance,
            )


def _represented_caption_role(value: Any) -> str | None:
    tokens = set(_normalize_variable_name(str(value or "")).split("_"))
    roles = tokens.intersection({"plaintiff", "defendant"})
    return roles.pop() if len(roles) == 1 else None


def _add_inferred_caption_name(
    candidates: dict[str, DocumentTemplateVariableSuggestion],
    *,
    role: str,
    value: Any,
    source_type: str,
    source_field: str,
    record_id: uuid.UUID | str | None,
) -> None:
    for alias in (role, f"{role}_name", f"{role}s", f"{role}_names"):
        _add_candidate(
            candidates,
            alias,
            value,
            source_type=source_type,
            source_field=source_field,
            record_id=record_id,
            confidence=0.75,
            review_required=True,
            provenance={
                "party_role": role,
                "selection": "legacy_matter_role_inference",
            },
        )


def _collect_smart_fill_candidates(
    *,
    matter: Matter | None,
    parties: Sequence[MatterParty] = (),
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
        "matter_role": matter.role,
        "represented_side": matter.role,
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

    _collect_caption_party_candidates(candidates, parties)

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

    represented_role = _represented_caption_role(matter.role)
    if represented_role:
        opposing_role = "defendant" if represented_role == "plaintiff" else "plaintiff"
        if client:
            _add_inferred_caption_name(
                candidates,
                role=represented_role,
                value=client.display_name,
                source_type="contact",
                source_field="display_name",
                record_id=client.id,
            )
        _add_inferred_caption_name(
            candidates,
            role=opposing_role,
            value=matter.counterparty,
            source_type="matter",
            source_field="counterparty",
            record_id=matter.id,
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


async def _load_matter_parties(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    matter: Matter | None,
) -> list[MatterParty]:
    if matter is None:
        return []
    result = await db.execute(
        select(MatterParty)
        .where(
            MatterParty.matter_id == matter.id,
            MatterParty.tenant_id == tenant_id,
        )
        .order_by(
            MatterParty.is_primary.desc(),
            MatterParty.created_at,
            MatterParty.id,
        )
    )
    return list(result.scalars().all())


def _schema_for_values(variable_schema: Any, variables: dict[str, str]) -> Any:
    """Relax required-ness for fields their own logic switches off.

    A field carrying a condition that is false for this set of values does not
    apply to the document being generated, so demanding a value for it would
    block generation on a clause the template deliberately omitted.  Geometry,
    anchors, and every other authoritative key are copied through untouched.
    """

    suppressed = suppressed_fields(variable_schema, variables)
    if not suppressed or not isinstance(variable_schema, dict):
        return variable_schema
    fields = variable_schema.get("fields")
    if not isinstance(fields, list):
        return variable_schema
    adjusted = []
    for field in fields:
        if (
            isinstance(field, dict)
            and str(field.get("name") or "").strip() in suppressed
        ):
            field = {**field, "required": False}
        adjusted.append(field)
    return {**variable_schema, "fields": adjusted}


def _party_item(party: MatterParty) -> dict[str, str]:
    contact = getattr(party, "contact", None)
    return {
        "party_name": _stringify_suggestion(getattr(contact, "display_name", None))
        or "",
        "party_role": _stringify_suggestion(getattr(party, "role", None)) or "",
        "party_email": _stringify_suggestion(getattr(contact, "email", None)) or "",
        "party_phone": _stringify_suggestion(getattr(contact, "phone", None)) or "",
    }


def _repeat_collections(
    parties: Sequence[MatterParty],
) -> dict[str, list[dict[str, str]]]:
    """Build the record sets a repeating section may iterate.

    Only parties carrying a resolvable display name are emitted, matching the
    caption-candidate rule: a nameless row would render an empty bullet or
    signature block in a filed document.
    """

    named = [
        party
        for party in parties
        if _stringify_suggestion(
            getattr(getattr(party, "contact", None), "display_name", None)
        )
    ]
    return {
        "parties": [_party_item(party) for party in named],
        "plaintiffs": [
            _party_item(party) for party in _caption_parties(named, "plaintiff")
        ],
        "defendants": [
            _party_item(party) for party in _caption_parties(named, "defendant")
        ],
    }


def _bound_suggestion(
    variable: str,
    binding: str,
    candidates: dict[str, DocumentTemplateVariableSuggestion],
) -> DocumentTemplateVariableSuggestion:
    """Resolve one field through its declared binding.

    An unresolved binding is reported with the path that failed, so the user
    can see the field is bound to a record the current matter does not carry
    rather than a blank box with no explanation.
    """

    if binding == MANUAL_BINDING:
        return DocumentTemplateVariableSuggestion(
            variable=variable,
            provenance={"status": "manual_entry", "binding": binding},
            review_required=True,
        )
    if is_item_binding(binding):
        # Its value comes from whichever item of a repeating section is being
        # rendered, so there is nothing for a person to fill in once.
        return DocumentTemplateVariableSuggestion(
            variable=variable,
            provenance={
                "status": "repeat_item",
                "binding": binding,
                "binding_label": binding_label(binding),
            },
            review_required=False,
        )
    alias = alias_for_binding(binding)
    candidate = candidates.get(alias) if alias else None
    if candidate is not None:
        provenance = {
            **candidate.provenance,
            "binding": binding,
            "binding_label": binding_label(binding),
        }
        return candidate.model_copy(
            update={"variable": variable, "provenance": provenance}
        )
    return DocumentTemplateVariableSuggestion(
        variable=variable,
        provenance={
            # A path the catalogue no longer describes has no label; saying so
            # is more useful than omitting the key.
            "status": "binding_unresolved",
            "binding": binding,
            "binding_label": binding_label(binding) or "Unknown data source",
        },
        review_required=True,
    )


async def _check_applicability(db, template, matter, user):
    rule = (template.variable_schema or {}).get("applicability")
    if not rule:
        return
    from app.services.template_semantics import validate_applicability

    try:
        validate_applicability(template.variable_schema)
    except TemplateSemanticsError as exc:
        raise HTTPException(
            status_code=409,
            detail="This template's scenario needs correction before use",
        ) from exc
    sources = await template_custom_fields.suggestions(
        db, template.tenant_id, matter, declared_bindings(template.variable_schema)
    )
    source = sources.get(rule["field"])
    if (
        not source
        or source.suggested_value is None
        or source.suggested_value.strip().casefold() != rule["value"].strip().casefold()
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Scenario '{rule['label']}' does not match the current matter details. Review missing or conflicting facts before generating.",
        )


async def _published_template(db, template):
    from app.services.document_template_versions import published_template_view

    try:
        return await published_template_view(db, template)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    parties = await _load_matter_parties(
        db=db,
        tenant_id=tenant_id,
        matter=matter,
    )
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
        matter=matter,
        parties=parties,
        current_user=current_user,
    )
    bindings = declared_bindings(getattr(template, "variable_schema", None))

    custom = await template_custom_fields.suggestions(db, tenant_id, matter, bindings)
    suggestions: list[DocumentTemplateVariableSuggestion] = []
    for variable in variables:
        if variable in custom:
            suggestions.append(custom[variable])
            continue
        binding = bindings.get(variable)
        if binding:
            # A declared binding is authoritative. Falling back to name
            # matching here would reintroduce exactly the surprise bindings
            # exist to remove: a field the customer bound to one record
            # silently filling from another because of its name.
            suggestions.append(_bound_suggestion(variable, binding, candidates))
            continue
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


@router.get("/bindings", response_model=DocumentTemplateBindingCatalogue)
async def list_template_bindings(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    """Return the data sources a template field may bind to.

    The catalogue is static, server-owned vocabulary rather than tenant data,
    so it needs no tenant context — but it stays behind the same capability as
    the editor that consumes it.
    """

    await set_tenant_context(db, str(current_user.tenant_id))
    custom_fields = await template_custom_fields.definitions(db, current_user.tenant_id)
    return DocumentTemplateBindingCatalogue(
        bindings=[
            DocumentTemplateBindingOption(
                path=entry.path, label=entry.label, group=entry.group
            )
            for entry in binding_catalogue()
        ]
        + [
            DocumentTemplateBindingOption(
                field_type=field.field_type,
                options=field.options_json or [],
                path=f"custom.{field.entity_type}.{field.id}",
                label=field.label,
                group="Matter details"
                if field.entity_type == "matter"
                else "Client details",
            )
            for field in custom_fields
        ],
        collections=[
            DocumentTemplateCollectionOption(
                name=entry.name,
                label=entry.label,
                item_fields=list(entry.item_fields),
            )
            for entry in binding_collections()
        ],
        operators=sorted(LOGIC_OPERATORS),
    )


@router.get("", response_model=DocumentTemplateListResponse)
async def list_templates(
    include_inactive: bool = Query(False),
    query: str | None = Query(None, max_length=120),
    category: str | None = Query(None),
    template_status: str | None = Query(None, pattern="^(active|inactive)$"),
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    parsed_tenant_id = uuid.UUID(tenant_id)
    tenant_filter = DocumentTemplate.tenant_id == parsed_tenant_id
    filters = [tenant_filter]
    if category is not None:
        if category not in CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid category. Must be one of: {', '.join(CATEGORIES)}",
            )
        filters.append(DocumentTemplate.category == category)
    normalized_query = " ".join(str(query or "").split()).strip()
    if normalized_query:
        search = f"%{escape_like(normalized_query)}%"
        filters.append(
            DocumentTemplate.title.ilike(search, escape="\\")
            | DocumentTemplate.description.ilike(search, escape="\\")
        )
    if template_status == "active":
        filters.append(DocumentTemplate.is_active.is_(True))
    elif template_status == "inactive":
        filters.append(DocumentTemplate.is_active.is_(False))
    elif not include_inactive:
        filters.append(DocumentTemplate.is_active.is_(True))

    stmt = (
        select(DocumentTemplate)
        .where(*filters)
        .order_by(DocumentTemplate.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    count_stmt = select(func.count(DocumentTemplate.id)).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    source_missing_filter = and_(
        func.lower(func.coalesce(DocumentTemplate.format, "")).in_(["pdf", "docx"]),
        or_(
            func.nullif(DocumentTemplate.source_storage_path, "").is_(None),
            func.nullif(DocumentTemplate.source_filename, "").is_(None),
            func.nullif(DocumentTemplate.source_sha256, "").is_(None),
            DocumentTemplate.source_file_size.is_(None),
            DocumentTemplate.source_file_size <= 0,
        ),
    )
    summary_stmt = select(
        func.count(DocumentTemplate.id),
        func.count(DocumentTemplate.id).filter(DocumentTemplate.is_active.is_(True)),
        func.count(DocumentTemplate.id).filter(DocumentTemplate.is_active.is_(False)),
        func.count(DocumentTemplate.id).filter(
            DocumentTemplate.is_active.is_(True),
            ~source_missing_filter,
        ),
        func.count(DocumentTemplate.id).filter(source_missing_filter),
    ).where(tenant_filter)
    summary_total, active_total, inactive_total, ready_total, source_missing_total = (
        await db.execute(summary_stmt)
    ).one()

    result = await db.execute(stmt)
    templates = result.scalars().all()

    return DocumentTemplateListResponse(
        items=[_template_response(t) for t in templates],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(templates) < total,
        summary={
            "total": summary_total,
            "active": active_total,
            "inactive": inactive_total,
            "ready": ready_total,
            "source_missing": source_missing_total,
        },
    )


@router.get("/queues", response_model=DocumentTemplateQueueResponse)
async def template_studio_queues(
    limit: int = Query(4, ge=1, le=12),
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Return globally correct, non-overlapping Studio action queues."""

    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    tenant_filter = DocumentTemplate.tenant_id == tenant_id
    source_missing = and_(
        func.lower(func.coalesce(DocumentTemplate.format, "")).in_(["pdf", "docx"]),
        or_(
            func.nullif(DocumentTemplate.source_storage_path, "").is_(None),
            func.nullif(DocumentTemplate.source_filename, "").is_(None),
            func.nullif(DocumentTemplate.source_sha256, "").is_(None),
            DocumentTemplate.source_file_size.is_(None),
            DocumentTemplate.source_file_size <= 0,
        ),
    )
    predicates = {
        "needs_attention": or_(
            source_missing,
            DocumentTemplate.status == "test_failed",
        ),
        "awaiting_publish": and_(
            ~source_missing,
            DocumentTemplate.status == "ready_to_publish",
            DocumentTemplate.is_active.is_(False),
        ),
        "published": and_(
            ~source_missing,
            DocumentTemplate.status == "published",
            DocumentTemplate.is_active.is_(True),
        ),
        "continue_setup": and_(
            ~source_missing,
            DocumentTemplate.is_active.is_(False),
            DocumentTemplate.status.notin_(
                ["ready_to_publish", "test_failed", "paused"]
            ),
        ),
    }
    queues = {}
    for name, predicate in predicates.items():
        total = await db.scalar(
            select(func.count(DocumentTemplate.id)).where(tenant_filter, predicate)
        )
        rows = await db.scalars(
            select(DocumentTemplate)
            .where(tenant_filter, predicate)
            .order_by(DocumentTemplate.updated_at.desc(), DocumentTemplate.id)
            .limit(limit)
        )
        queues[name] = {
            "total": int(total or 0),
            "items": [_template_response(template) for template in rows.all()],
        }
    return DocumentTemplateQueueResponse(**queues)


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
    template.is_active = False
    template.status = "draft"
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_response(template)


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
    sample = await _read_template_sample(file)
    try:
        analysis = await asyncio.to_thread(
            analyze_template_upload,
            file_bytes=sample.content,
            filename=sample.filename,
            content_type=sample.content_type,
            title=_validated_title(title),
        )
    except (TemplatePdfError, TemplateDocxError, TemplateOcrError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response_payload = analysis.as_dict()
    response_payload["warnings"] = list(
        dict.fromkeys([*(response_payload.get("warnings") or []), *sample.warnings])
    )
    response_payload["analysis_token"] = _issue_analysis_token(
        analysis=response_payload,
        file_bytes=sample.content,
        filename=sample.filename,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )
    return DocumentTemplateUploadAnalysisResponse(**response_payload)


@router.post(
    "/intake/ai-propose",
    response_model=DocumentTemplateUploadAnalysisResponse,
)
async def propose_template_fields_with_ai(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    consent_to_external_ai: bool = Form(False),
    current_user=Depends(require_capability("use_premium_ai")),
    db: AsyncSession = Depends(get_db),
):
    """Return a review-only premium-AI field proposal for one upload."""

    if not getattr(current_user, "premium_ai_enabled", False):
        raise HTTPException(
            status_code=403,
            detail="Premium AI is not enabled for this user.",
        )
    await set_tenant_context(db, str(current_user.tenant_id))
    sample = await _read_template_sample(file)
    try:
        analysis = await asyncio.to_thread(
            analyze_template_upload,
            file_bytes=sample.content,
            filename=sample.filename,
            content_type=sample.content_type,
            title=_validated_title(title),
        )
        analysis.warnings.extend(
            warning for warning in sample.warnings if warning not in analysis.warnings
        )
        analysis = await assist_template_mapping(
            db=db,
            user=current_user,
            analysis=analysis,
            file_bytes=sample.content,
            consent_to_external_ai=consent_to_external_ai,
        )
    except (
        TemplatePdfError,
        TemplateDocxError,
        TemplateOcrError,
        TemplateAiAssistError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response_payload = analysis.as_dict()
    response_payload["analysis_token"] = _issue_analysis_token(
        analysis=response_payload,
        file_bytes=sample.content,
        filename=sample.filename,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )
    return DocumentTemplateUploadAnalysisResponse(**response_payload)


@router.post("/intake/pdf-page-preview")
async def preview_template_pdf_page(
    file: UploadFile = File(...),
    page_number: int = Query(1, ge=1, le=250),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render an uploaded PDF page for the visual manual field mapper."""
    await set_tenant_context(db, str(current_user.tenant_id))
    sample = await _read_template_sample(file)
    if not sample.content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400, detail="PDF page preview requires a PDF upload"
        )
    try:
        image, metadata = await asyncio.to_thread(
            render_pdf_page_preview, sample.content, page_number
        )
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "X-PDF-Page-Count": str(metadata["page_count"]),
        "X-PDF-Page-Width": str(metadata["width"]),
        "X-PDF-Page-Height": str(metadata["height"]),
    }
    return Response(content=image, media_type="image/png", headers=headers)


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
    analysis_token: str | None = Form(None),
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

    sample = await _read_template_sample(file)
    file_bytes = sample.content
    filename = sample.filename
    requested_title = _validated_title(title)
    try:
        analysis = await _analysis_for_template_create(
            file_bytes=file_bytes,
            filename=filename,
            content_type=sample.content_type,
            requested_title=requested_title,
            analysis_token=analysis_token,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
        )
    except (TemplatePdfError, TemplateDocxError, TemplateOcrError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    _reconcile_submitted_ai_fields(
        variable_schema,
        analysis=analysis,
        file_bytes=file_bytes,
    )
    reviewed_schema = _reviewed_variable_schema(
        variable_schema, analysis.variable_schema
    )
    # A scan may legitimately yield zero automatic detections: the review
    # canvas can still add valid manual overlays. Validate the final reviewed
    # contract, rather than rejecting the source before the user's edits are
    # considered.
    if analysis.format == "pdf" and not any(
        isinstance(field, dict)
        and field.get("included", True) is True
        and (
            field.get("pdf_field_name")
            or field.get("pdf_overlay")
            or field.get("pdf_overlays")
        )
        for field in (reviewed_schema.get("fields") or [])
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "No reusable PDF fields were detected. Add at least one field "
                "in the review canvas or upload a clearer source."
            ),
        )
    if analysis.format == "docx":
        seen_source_text: dict[str, str] = {}
        seen_docx_anchors: dict[tuple[int, int, int], str] = {}
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
            if source_text not in analysis.source_text:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Word variable {name!r} no longer matches text in the uploaded document. "
                        "Select the source again and review the detected details."
                    ),
                )
            anchor = field.get("docx_anchor")
            if anchor is not None:
                if not isinstance(anchor, dict):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Word variable {name!r} has an invalid reviewed location.",
                    )
                try:
                    anchor_key = (
                        int(anchor["paragraph_ordinal"]),
                        int(anchor["start"]),
                        int(anchor["end"]),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Word variable {name!r} has an invalid reviewed location.",
                    ) from exc
                if (
                    anchor_key[0] < 0
                    or anchor_key[1] < 0
                    or anchor_key[2] <= anchor_key[1]
                    or anchor_key[2] - anchor_key[1] != len(source_text)
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Word variable {name!r} has a reviewed location that does not match its source text."
                        ),
                    )
                previous_name = seen_docx_anchors.get(anchor_key)
                if previous_name and previous_name != name:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"The same Word location cannot map to both {previous_name!r} and {name!r}."
                        ),
                    )
                seen_docx_anchors[anchor_key] = name
                continue
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
        try:
            await asyncio.to_thread(
                fill_docx_template,
                file_bytes,
                variable_schema=reviewed_schema,
                variables={},
            )
        except TemplateDocxError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "A reviewed Word field location no longer matches the uploaded document. "
                    "Re-run detection and review the highlighted source locations."
                ),
            ) from exc
    if analysis.format in {"pdf", "docx"}:
        mapped_variables = {
            str(field.get("name"))
            for field in (reviewed_schema.get("fields") or [])
            if isinstance(field, dict)
            and field.get("included", True) is True
            and field.get("name")
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
    canonical_source_bytes = analysis._normalized_source_bytes or file_bytes
    canonical_source_filename = analysis._normalized_source_filename or filename
    canonical_source_content_type = (
        analysis._normalized_source_content_type
        or sample.content_type
        or "application/octet-stream"
    )
    template_id = uuid.uuid4()
    source_path = await _persist_template_source(
        tenant_id=tenant_id,
        template_id=template_id,
        filename=canonical_source_filename,
        content=canonical_source_bytes,
    )
    template = DocumentTemplate(
        id=template_id,
        tenant_id=uuid.UUID(tenant_id),
        title=analysis.title,
        body=body,
        category=category,
        description=f"Draft created from uploaded sample: {sample.original_filename}",
        status="draft",
        format=analysis.format,
        module=_clean_optional_form_value(module),
        stage=_clean_optional_form_value(stage),
        jurisdiction=_clean_optional_form_value(jurisdiction),
        kind=_clean_optional_form_value(kind),
        variable_schema=reviewed_schema,
        branding_profile=analysis.branding_profile,
        source_storage_path=source_path,
        source_filename=canonical_source_filename,
        source_content_type=canonical_source_content_type,
        source_sha256=hashlib.sha256(canonical_source_bytes).hexdigest(),
        source_file_size=len(canonical_source_bytes),
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
    return _template_response(template)


@router.get("/{template_id}", response_model=DocumentTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    published: bool = Query(False),
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
    if published:
        template = await _published_template(db, template)
    return _template_response(template)


@router.get("/{template_id}/outline", response_model=DocumentTemplateOutlineResponse)
async def get_template_outline(
    template_id: uuid.UUID,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Return a Word template's paragraphs, numbered as filling numbers them.

    This is the authoring surface for DOCX. A Word field is a character span in
    a paragraph, not a rectangle on a page, so the editor needs addressable
    text rather than a rendered image — and the ordinals come from the same
    iterator that fills the template, so an anchor placed here addresses the
    same paragraph at generation time.
    """

    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    template = await db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == uuid.UUID(tenant_id),
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if str(template.format or "").lower() != "docx":
        raise HTTPException(
            status_code=422,
            detail="A paragraph outline is available for Word templates only.",
        )

    source = await _verified_template_source(template)
    try:
        outline = await asyncio.to_thread(docx_outline, source)
    except TemplateDocxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DocumentTemplateOutlineResponse(template_id=str(template.id), **outline)


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
    if payload.preview_purpose == "generation":
        template = await _published_template(db, template)
    template_format = str(template.format or "").lower()
    if template_format not in {"pdf", "docx"}:
        raise HTTPException(
            status_code=409,
            detail="File preview is available only for source-backed PDF or DOCX templates",
        )
    if payload.convert_to_pdf and template_format != "docx":
        raise HTTPException(
            status_code=422,
            detail="Word-to-PDF conversion is available only for DOCX templates.",
        )
    if template_format == "docx":
        purpose = payload.preview_purpose
        if purpose == "activation" and payload.matter_id:
            raise HTTPException(
                status_code=422,
                detail="Activation previews are template-wide and cannot be tied to a matter.",
            )
        if purpose == "generation" and not template.is_active:
            raise HTTPException(
                status_code=409,
                detail="Publish this template before previewing a matter-ready PDF.",
            )
        matter = await _load_render_matter(
            db, tenant_id=tenant_id, matter_id=payload.matter_id
        )
        if payload.convert_to_pdf and purpose == "generation" and matter is None:
            raise HTTPException(
                status_code=422,
                detail="Choose a matter before previewing the exact PDF values for save.",
            )
        source = await _verified_template_source(template)
        try:
            output = await asyncio.to_thread(
                fill_docx_template,
                source,
                variable_schema=template.variable_schema,
                variables=payload.variables,
                enforce_required=payload.preview_purpose == "activation",
            )
        except TemplateDocxError as exc:
            if payload.preview_purpose == "activation":
                template.status = "test_failed"
                template.tested_version_no = None
                await db.commit()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.convert_to_pdf:
            if not settings.DOCX_PDF_CONVERSION_ENABLED:
                raise HTTPException(
                    status_code=503,
                    detail="Word-to-PDF conversion is unavailable.",
                )
            try:
                output = await docx_to_pdf_bytes(
                    output,
                    executable=settings.DOCX_PDF_CONVERTER_PATH,
                    timeout_seconds=settings.DOCX_PDF_CONVERSION_TIMEOUT_SECONDS,
                    max_output_bytes=settings.DOCX_PDF_CONVERSION_MAX_OUTPUT_BYTES,
                    max_pages=settings.DOCX_PDF_CONVERSION_MAX_PAGES,
                )
            except DocxToPdfError as exc:
                if purpose == "activation":
                    template.status = "test_failed"
                    template.tested_version_no = None
                    await db.commit()
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            reviewed_fields, nonblank_count = pdf_review_evidence(
                template.variable_schema, payload.variables
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
                    flatten_pdf=True,
                    matter_id=matter.id if matter else None,
                ),
                output_sha256=hashlib.sha256(output).hexdigest(),
                renderer_version=_DOCX_PDF_RENDERER_VERSION,
                flatten_pdf=True,
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
            if purpose == "activation":
                await _ensure_current_template_version(
                    db,
                    template=template,
                    tenant_id=tenant_id,
                    user_id=current_user.id,
                    change_summary="Tested draft",
                )
                template.tested_version_no = template.current_version_no
                template.last_test_rendered_at = previewed_at
                template.status = "ready_to_publish"
            await db.commit()
            filename = _safe_generated_filename(template.title, "pdf")
            return Response(
                content=output,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, no-store",
                    "Pragma": "no-cache",
                    "X-Clarity-Preview-ID": str(evidence.id),
                    "X-Clarity-Preview-Purpose": purpose,
                },
            )
        if payload.preview_purpose == "activation":
            await _ensure_current_template_version(
                db,
                template=template,
                tenant_id=tenant_id,
                user_id=current_user.id,
                change_summary="Tested draft",
            )
            template.tested_version_no = template.current_version_no
            template.last_test_rendered_at = datetime.now(timezone.utc)
            template.status = "ready_to_publish"
            await db.commit()
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
    if purpose == "generation":
        await _check_applicability(db, template, matter, current_user)
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
            template.status = "test_failed"
            template.tested_version_no = None
            await db.commit()
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
        if purpose == "activation":
            template.status = "test_failed"
            template.tested_version_no = None
            await db.commit()
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
        await _ensure_current_template_version(
            db,
            template=template,
            tenant_id=tenant_id,
            user_id=current_user.id,
            change_summary="Tested draft",
        )
        template.tested_version_no = template.current_version_no
        template.last_test_rendered_at = previewed_at
        template.status = "ready_to_publish"
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
    # Version metadata labels the history row, it is not a template column.
    updates.pop("change_summary", None)
    if "variable_schema" in updates:
        try:
            validate_semantic_metadata(updates["variable_schema"])
        except (
            TemplateSemanticsError,
            TemplateLogicError,
            TemplateRegionError,
        ) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    if current_format == "docx" and "body" in updates:
        raise HTTPException(
            status_code=422,
            detail="Upload a new Word source to change a DOCX template body.",
        )
    if (
        current_format == "docx"
        and "variable_schema" in updates
        and not is_semantic_only_change(
            template.variable_schema, updates["variable_schema"]
        )
    ):
        try:
            source = await _verified_template_source(template)
            await asyncio.to_thread(
                validate_visual_field_map,
                source,
                template.variable_schema or {},
                updates["variable_schema"],
            )
        except TemplateDocxError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "format" in updates:
        updates["format"] = requested_format

    pdf_schema_update_requested = "variable_schema" in updates
    pdf_body_update_requested = "body" in updates
    # Activating a PDF re-derives variable_schema from the retained source and
    # writes it into `updates`, so the keys the caller actually asked to change
    # have to be recorded before that rewrite hides the difference.
    requested_update_keys = frozenset(updates)
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
        discovered_schema = rediscovered.variable_schema
        effective_schema = updates.get("variable_schema", template.variable_schema)
        updates["variable_schema"] = _reviewed_variable_schema(
            json.dumps(effective_schema), discovered_schema
        )
        mapped_variables = {
            str(field.get("name"))
            for field in (updates["variable_schema"].get("fields") or [])
            if isinstance(field, dict)
            and field.get("included", True) is True
            and (
                field.get("pdf_field_name")
                or field.get("pdf_overlay")
                or field.get("pdf_overlays")
            )
        }
        if not mapped_variables:
            raise HTTPException(
                status_code=422,
                detail=(
                    "A PDF template must contain at least one included form or "
                    "text-overlay field before activation."
                ),
            )
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
    if current_format == "pdf" and requested_activation:
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
    if (
        requested_activation
        and template.tested_version_no != template.current_version_no
    ):
        raise HTTPException(
            status_code=409,
            detail="Test this exact template version successfully before publishing it.",
        )

    if pdf_contract_changed:
        # A field-map/body change invalidates the exact artifact that was tested
        # and approved. Existing active templates remain usable until edited.
        updates["is_active"] = False
        updates["last_test_rendered_at"] = None
        updates["approved_at"] = None
        updates["approved_by_user_id"] = None
    elif current_format == "pdf" and requested_activation:
        updates["approved_at"] = datetime.now(timezone.utc)
        updates["approved_by_user_id"] = current_user.id
    elif (
        current_format == "pdf"
        and updates.get("is_active") is False
        and template.is_active
    ):
        updates["approved_at"] = None
        updates["approved_by_user_id"] = None
    if requested_activation:
        updates["status"] = "published"

    versioned_change = snapshot_differs(template, updates)
    content_change = any(
        key in requested_update_keys and updates[key] != getattr(template, key, None)
        for key in (
            "title",
            "body",
            "variable_schema",
            "format",
            "category",
            "source_sha256",
        )
    )
    if content_change:
        # Keep the last release live while this authoring row becomes a draft.
        updates["is_active"] = bool(
            template.is_active and template.published_version_no
        )
        updates["status"] = "draft"
        updates["tested_version_no"] = None
        updates["last_test_rendered_at"] = None
        updates["approved_at"] = None
        updates["approved_by_user_id"] = None
    elif updates.get("is_active") is False and template.is_active:
        updates["status"] = "paused"
        updates["approved_at"] = None
        updates["approved_by_user_id"] = None

    for field, value in updates.items():
        setattr(template, field, value)

    # The version number identifies the resulting state, never the wording it
    # replaced.  Both writes share this transaction, so neither can exist
    # without the other.
    if versioned_change:
        await record_version(
            db,
            template=template,
            tenant_id=uuid.UUID(tenant_id),
            user_id=current_user.id,
            change_summary=payload.change_summary,
        )
    if requested_activation:
        template.tested_version_no = template.current_version_no
        template.published_version_no = template.current_version_no

    await db.commit()
    await db.refresh(template)
    return _template_response(template)


def _version_summary(
    version: DocumentTemplateVersion,
) -> DocumentTemplateVersionSummary:
    schema = (
        version.variable_schema if isinstance(version.variable_schema, dict) else {}
    )
    fields = schema.get("fields")
    return DocumentTemplateVersionSummary(
        version_no=version.version_no,
        title=version.title,
        format=version.format,
        category=version.category,
        body_sha256=version.body_sha256,
        source_sha256=version.source_sha256,
        source_filename=version.source_filename,
        is_active=version.is_active,
        field_count=len(fields) if isinstance(fields, list) else 0,
        change_summary=version.change_summary,
        created_by_user_id=(
            str(version.created_by_user_id) if version.created_by_user_id else None
        ),
        created_at=version.created_at.isoformat(),
    )


async def _ensure_current_template_version(
    db: AsyncSession,
    *,
    template: DocumentTemplate,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    change_summary: str,
) -> DocumentTemplateVersion:
    """Return an immutable row that exactly matches the live authoring row."""

    if template.current_version_no:
        current = await get_version(
            db,
            tenant_id=tenant_id,
            template_id=template.id,
            version_no=int(template.current_version_no),
        )
        if current and not snapshot_differs(
            template,
            {
                "title": current.title,
                "body": current.body,
                "variable_schema": current.variable_schema,
                "format": current.format,
                "category": current.category,
                "source_sha256": current.source_sha256,
                "is_active": current.is_active,
            },
        ):
            return current
    return await record_version(
        db,
        template=template,
        tenant_id=tenant_id,
        user_id=user_id,
        change_summary=change_summary,
    )


@router.get(
    "/{template_id}/versions",
    response_model=DocumentTemplateVersionListResponse,
)
async def list_template_versions(
    template_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Return this template's published history, newest first."""

    tenant_id = str(current_user.tenant_id)
    parsed_tenant_id = uuid.UUID(tenant_id)
    await set_tenant_context(db, tenant_id)

    template = await db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == parsed_tenant_id,
        )
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    versions, total = await list_versions(
        db,
        tenant_id=parsed_tenant_id,
        template_id=template_id,
        limit=limit,
        offset=offset,
    )
    return DocumentTemplateVersionListResponse(
        template_id=str(template.id),
        current_version_no=int(template.current_version_no or 0),
        tested_version_no=template.tested_version_no,
        published_version_no=template.published_version_no,
        total=total,
        versions=[_version_summary(version) for version in versions],
    )


@router.get(
    "/{template_id}/versions/{version_no}",
    response_model=DocumentTemplateVersionDetail,
)
async def get_template_version(
    template_id: uuid.UUID,
    version_no: int,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Return one recorded version, including its body and field map."""

    tenant_id = str(current_user.tenant_id)
    parsed_tenant_id = uuid.UUID(tenant_id)
    await set_tenant_context(db, tenant_id)

    version = await get_version(
        db,
        tenant_id=parsed_tenant_id,
        template_id=template_id,
        version_no=version_no,
    )
    if not version:
        raise HTTPException(status_code=404, detail="Template version not found")
    return DocumentTemplateVersionDetail(
        **_version_summary(version).model_dump(),
        body=version.body,
        variable_schema=version.variable_schema,
    )


@router.post(
    "/{template_id}/versions/{version_no}/restore",
    response_model=DocumentTemplateResponse,
)
async def restore_template_version(
    template_id: uuid.UUID,
    version_no: int,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Put an earlier wording back, as a new version rather than a rewrite.

    Restoring records the current state first, so the history keeps every
    published state including the one being replaced. The restored template
    is always left inactive: an earlier field map has not been previewed
    against the current source, and activation stays a deliberate human step.
    """

    tenant_id = str(current_user.tenant_id)
    parsed_tenant_id = uuid.UUID(tenant_id)
    await set_tenant_context(db, tenant_id)

    template = await db.scalar(
        select(DocumentTemplate)
        .where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == parsed_tenant_id,
        )
        .with_for_update()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    version = await get_version(
        db,
        tenant_id=parsed_tenant_id,
        template_id=template_id,
        version_no=version_no,
    )
    if not version:
        raise HTTPException(status_code=404, detail="Template version not found")

    # A source-backed template's field map is only meaningful against the exact
    # retained bytes. Restoring a version recorded against different bytes
    # would reinstate anchors that no longer point anywhere.
    if str(template.format or "").lower() in {"pdf", "docx"} and (
        version.source_sha256 != template.source_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "That version was recorded against a different source file. "
                "Re-upload the original source before restoring it."
            ),
        )

    template.title = version.title
    template.body = version.body
    template.variable_schema = version.variable_schema
    template.category = version.category or template.category
    template.is_active = bool(template.is_active and template.published_version_no)
    template.last_test_rendered_at = None
    template.approved_at = None
    template.approved_by_user_id = None
    template.tested_version_no = None
    template.status = "draft"

    await record_version(
        db,
        template=template,
        tenant_id=parsed_tenant_id,
        user_id=current_user.id,
        change_summary=f"Restore of version {version_no}",
    )

    await db.commit()
    await db.refresh(template)
    return _template_response(template)


@router.post(
    "/{template_id}/publish",
    response_model=DocumentTemplateResponse,
)
async def publish_template(
    template_id: uuid.UUID,
    payload: DocumentTemplatePublishRequest,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Publish only the exact immutable version the user successfully tested."""

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
    if (
        template.is_active
        and template.published_version_no == template.current_version_no
        and template.tested_version_no == template.current_version_no
    ):
        return _template_response(template)
    if not template.current_version_no or (
        template.tested_version_no != template.current_version_no
    ):
        raise HTTPException(
            status_code=409,
            detail="Test this exact template version successfully before publishing it.",
        )

    template.is_active = True
    template.status = "published"
    template.approved_at = datetime.now(timezone.utc)
    template.approved_by_user_id = current_user.id
    await record_version(
        db,
        template=template,
        tenant_id=tenant_id,
        user_id=current_user.id,
        change_summary=payload.change_summary or "Published after successful test",
    )
    template.tested_version_no = template.current_version_no
    template.published_version_no = template.current_version_no
    await db.commit()
    await db.refresh(template)
    return _template_response(template)


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

    if payload.published:
        template = await _published_template(db, template)

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
    if matter is not None or payload.preview_purpose == "generation":
        template = await _published_template(db, template)
        await _check_applicability(db, template, matter, current_user)

    # Repeating sections iterate the matter's own party records, so a
    # rendered document reflects however many parties this matter actually
    # has rather than however many the template author happened to type.
    render_parties = await _load_matter_parties(
        db=db, tenant_id=parsed_tenant_id, matter=matter
    )
    try:
        rendered = render_template(
            template.body,
            payload.variables,
            collections=_repeat_collections(render_parties),
        )
    except TemplateLogicError as exc:
        if matter is None and payload.preview_purpose == "activation":
            template.status = "test_failed"
            template.tested_version_no = None
            await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    template_format = str(template.format or "").lower()
    if payload.convert_to_pdf and template_format != "docx":
        raise HTTPException(
            status_code=422,
            detail="Word-to-PDF conversion is available only for DOCX templates.",
        )
    convert_docx_to_pdf = (
        template_format == "docx"
        and bool(template.source_sha256)
        and payload.convert_to_pdf
    )
    output_format = (
        "pdf"
        if convert_docx_to_pdf
        else template_format
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
        preview_evidence, existing_document = await _load_generation_preview_evidence(
            db,
            preview_id=payload.preview_id,
            tenant_id=parsed_tenant_id,
            template=template,
            matter=matter,
            user_id=current_user.id,
            variables=payload.variables,
            flatten_pdf=payload.flatten_pdf,
            lock=False,
        )
        if existing_document:
            return _existing_document_response(existing_document, matter_id=matter.id)
    output_filename = _safe_generated_filename(
        template.title,
        {"pdf": "pdf", "docx": "docx"}.get(output_format, "md"),
    )
    if template_format == "pdf" and output_format == "pdf":
        source = await _verified_template_source(template)
        try:
            output_bytes = await asyncio.to_thread(
                fill_pdf_template,
                source,
                variable_schema=_schema_for_values(
                    template.variable_schema, payload.variables
                ),
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
    elif template_format == "docx" and template.source_sha256:
        source = await _verified_template_source(template)
        try:
            output_bytes = await asyncio.to_thread(
                fill_docx_template,
                source,
                variable_schema=_schema_for_values(
                    template.variable_schema, payload.variables
                ),
                variables=payload.variables,
                enforce_required=bool(payload.matter_id),
                collections=_repeat_collections(render_parties),
                regions=stored_regions(template.variable_schema),
            )
        except TemplateDocxError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if convert_docx_to_pdf:
            if not settings.DOCX_PDF_CONVERSION_ENABLED:
                raise HTTPException(
                    status_code=503,
                    detail="Word-to-PDF conversion is unavailable.",
                )
            try:
                output_bytes = await docx_to_pdf_bytes(
                    output_bytes,
                    executable=settings.DOCX_PDF_CONVERTER_PATH,
                    timeout_seconds=settings.DOCX_PDF_CONVERSION_TIMEOUT_SECONDS,
                    max_output_bytes=settings.DOCX_PDF_CONVERSION_MAX_OUTPUT_BYTES,
                    max_pages=settings.DOCX_PDF_CONVERSION_MAX_PAGES,
                )
            except DocxToPdfError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            rendered = (
                f'PDF ready for signature: "{output_filename}"\n'
                f"Filled {sum(1 for value in payload.variables.values() if value)} reviewed field(s) from the Word template."
            )
        else:
            rendered = (
                f'Word document ready: "{output_filename}"\n'
                f"Filled {sum(1 for value in payload.variables.values() if value)} reviewed field(s) while preserving the original DOCX layout."
            )
    else:
        output_bytes = rendered.encode("utf-8")
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()
    if (
        matter is None
        and payload.preview_purpose == "activation"
        and output_format == "markdown"
    ):
        await _ensure_current_template_version(
            db,
            template=template,
            tenant_id=parsed_tenant_id,
            user_id=current_user.id,
            change_summary="Tested draft",
        )
        template.tested_version_no = template.current_version_no
        template.last_test_rendered_at = datetime.now(timezone.utc)
        template.status = "ready_to_publish"
        await db.commit()
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
        # DOCX/Markdown generation uses the authorized snapshot read at request
        # start. It has no preview-evidence state to consume, so holding a row
        # lock across a cloud upload adds contention without making the output
        # safer. PDF saves revalidate their exact reviewed contract below.
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

        if output_format == "pdf":
            try:
                final_template = await db.scalar(
                    select(DocumentTemplate)
                    .where(
                        DocumentTemplate.id == template_id,
                        DocumentTemplate.tenant_id == parsed_tenant_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    not final_template
                    or not final_template.is_active
                    or final_template.published_version_no
                    != template.published_version_no
                    or final_template.source_sha256 != template.source_sha256
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The template changed while the PDF was being prepared. "
                            "Preview the current template and values again."
                        ),
                    )
                template = await _published_template(db, final_template)
                (
                    preview_evidence,
                    existing_document,
                ) = await _load_generation_preview_evidence(
                    db,
                    preview_id=payload.preview_id,
                    tenant_id=parsed_tenant_id,
                    template=template,
                    matter=matter,
                    user_id=current_user.id,
                    variables=payload.variables,
                    flatten_pdf=payload.flatten_pdf,
                    lock=True,
                )
                if not hmac.compare_digest(
                    output_sha256,
                    preview_evidence.output_sha256,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The freshly rendered PDF does not match the reviewed "
                            "preview. Preview the exact current values again before saving."
                        ),
                    )
            except HTTPException:
                await _discard_staged_generation(
                    db,
                    tenant_id=parsed_tenant_id,
                    matter_id=parsed_matter_id,
                    preview_id=payload.preview_id,
                    storage_result=storage_result,
                    output_filename=output_filename,
                    output_sha256=output_sha256,
                    document_id=doc_id,
                )
                raise
            except Exception as exc:
                logger.exception(
                    "Generated PDF finalization failed after storage for "
                    "tenant=%s matter=%s; compensating staged storage",
                    tenant_id,
                    parsed_matter_id,
                )
                await _discard_staged_generation(
                    db,
                    tenant_id=parsed_tenant_id,
                    matter_id=parsed_matter_id,
                    preview_id=payload.preview_id,
                    storage_result=storage_result,
                    output_filename=output_filename,
                    output_sha256=output_sha256,
                    document_id=doc_id,
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "The generated PDF could not be finalized; staged storage "
                        "was removed. Retry when the database is healthy."
                    ),
                ) from exc
            if existing_document:
                await _discard_staged_generation(
                    db,
                    tenant_id=parsed_tenant_id,
                    matter_id=parsed_matter_id,
                    preview_id=payload.preview_id,
                    storage_result=storage_result,
                    output_filename=output_filename,
                    output_sha256=output_sha256,
                    document_id=doc_id,
                )
                return _existing_document_response(
                    existing_document,
                    matter_id=parsed_matter_id,
                )

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
                "template_version_no": template.published_version_no,
                "output_document_id": str(doc.id),
                "output_filename": output_filename,
                "output_format": output_format,
                "output_sha256": output_sha256,
                "filled_variables": sorted(
                    name for name, value in payload.variables.items() if value
                ),
                "flatten_pdf": payload.flatten_pdf if output_format == "pdf" else None,
                "renderer_version": (
                    _DOCX_PDF_RENDERER_VERSION
                    if convert_docx_to_pdf
                    else _PDF_RENDERER_VERSION
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


@router.post("/fact-review/{matter_id}/{document_id}/{field_id}")
async def propose_matter_fact(
    matter_id: uuid.UUID,
    document_id: uuid.UUID,
    field_id: uuid.UUID,
    current_user=Depends(require_capabilities("manage_documents", "manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    return await template_fact_review.propose(
        db, current_user, matter_id, document_id, field_id
    )


@router.post("/fact-review/{matter_id}/{document_id}/{field_id}/accept")
async def accept_matter_fact(
    matter_id: uuid.UUID,
    document_id: uuid.UUID,
    field_id: uuid.UUID,
    payload: template_fact_review.FactAccept,
    current_user=Depends(require_capabilities("manage_documents", "manage_matters")),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    return await template_fact_review.accept(
        db, current_user, matter_id, document_id, field_id, payload
    )
