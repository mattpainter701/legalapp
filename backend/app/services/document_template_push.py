"""Draft-only firm-template authoring for Workspace MCP clients.

An outside agent (Claude, Codex, or any consented MCP client) can author a
Markdown firm template and push it here.  Everything this module writes is a
**draft**: ``is_active`` stays false and ``status`` stays ``draft``, which is
exactly what :func:`app.services.document_template_workspace._automation_ready`
refuses to render.  A pushed template therefore cannot produce client work
until a human opens it in Template Studio and activates it.

Live templates are never edited in place.  Revising a template the firm is
already using produces a *new* draft that records what it supersedes, so the
version in production keeps working while the proposal waits for review.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.models.document_template import DocumentTemplate
from app.schemas.document_template import CATEGORIES
from app.schemas.workspace_mcp import (
    MAX_DOCUMENT_BYTES,
    ProposeDocumentTemplateArgs,
)
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.document_template_versions import body_sha256, record_version
from app.services.template_intake import (
    TemplateAnalysis,
    TemplateOcrError,
    TemplatePdfError,
    analyze_template_upload,
)
from app.services.docx_templates import TemplateDocxError
from app.services.template_logic import TemplateLogicError
from app.services.template_regions import TemplateRegionError
from app.services.template_semantics import (
    TemplateSemanticsError,
    validate_semantic_metadata,
)

settings = get_settings()

#: Logic markers share placeholder syntax with variables but are never filled.
_VARIABLE_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")
MAX_TEMPLATE_VARIABLES = 200
#: Formats whose body and field map are derived from a retained binary source
#: rather than from an inline body.
SOURCE_BACKED_FORMATS = frozenset({"docx", "pdf"})

_EXTENSION_BY_FORMAT = {"docx": ".docx", "pdf": ".pdf"}
_CONTENT_TYPE_BY_FORMAT = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": "application/pdf",
}


@dataclass(frozen=True, slots=True)
class AnalyzedTemplateSource:
    """A pushed template file that analysis accepted, with its field map."""

    analysis: TemplateAnalysis
    content: bytes
    filename: str
    content_type: str
    sha256: str
    fillable_field_count: int


def _decoded_template_source(args: ProposeDocumentTemplateArgs) -> bytes:
    try:
        content = base64.b64decode(args.content_base64 or "", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CapabilityError(
            "invalid_template_encoding",
            "content_base64 is not valid standard base64",
        ) from exc
    if not content:
        raise CapabilityError("empty_template", "The uploaded template is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise CapabilityError(
            "template_too_large",
            f"The uploaded template exceeds the {MAX_DOCUMENT_BYTES}-byte limit",
        )
    if args.content_sha256:
        digest = hashlib.sha256(content).hexdigest()
        if digest != args.content_sha256:
            raise CapabilityError(
                "template_integrity_failed",
                "The uploaded template does not match content_sha256",
            )
    return content


def _fillable_fields(schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return AcroForm-backed fields — the ones a fillable PDF can actually fill."""

    fields = (schema or {}).get("fields")
    if not isinstance(fields, list):
        return []
    return [
        field
        for field in fields
        if isinstance(field, dict) and str(field.get("pdf_field_name") or "").strip()
    ]


async def _analyzed_template_source(
    args: ProposeDocumentTemplateArgs,
) -> AnalyzedTemplateSource:
    """Discover a pushed template's field map from the file itself.

    Analysis, not the caller, produces the field map. A DOCX field is anchored
    to the exact source text it replaces and a PDF variable comes from a real
    AcroForm widget, so a pushed template cannot claim a mapping the file does
    not actually support.
    """

    content = _decoded_template_source(args)
    template_format = args.format
    filename = args.filename or f"{args.title}{_EXTENSION_BY_FORMAT[template_format]}"
    expected_extension = _EXTENSION_BY_FORMAT[template_format]
    if not filename.casefold().endswith(expected_extension):
        raise CapabilityError(
            "template_filename_mismatch",
            f"A {template_format} template filename must end in {expected_extension}",
        )

    try:
        analysis = await asyncio.to_thread(
            analyze_template_upload,
            file_bytes=content,
            filename=filename,
            content_type=_CONTENT_TYPE_BY_FORMAT[template_format],
            title=args.title,
        )
    except (TemplatePdfError, TemplateDocxError, TemplateOcrError) as exc:
        raise CapabilityError("template_analysis_failed", str(exc)) from exc

    if analysis.format != template_format:
        # The bytes decide, not the declared format: a PDF renamed .docx must
        # not be stored under a contract its content cannot honour.
        raise CapabilityError(
            "template_format_mismatch",
            f"The uploaded file is a {analysis.format} rather than a "
            f"{template_format} template",
        )

    fillable = _fillable_fields(analysis.variable_schema)
    if template_format == "pdf" and not fillable:
        # Overlay placement on a flat or scanned PDF is a human judgement about
        # where text lands on the page; it has no safe automatic answer.
        raise CapabilityError(
            "pdf_not_fillable",
            "This PDF has no fillable AcroForm fields. Push a fillable PDF whose "
            "form fields LawHand can discover, or build it in LawHand's template "
            "intake review canvas where a person can place each field",
        )

    canonical = analysis._normalized_source_bytes or content
    canonical_filename = analysis._normalized_source_filename or filename
    return AnalyzedTemplateSource(
        analysis=analysis,
        content=canonical,
        filename=canonical_filename,
        content_type=(
            analysis._normalized_source_content_type
            or _CONTENT_TYPE_BY_FORMAT[template_format]
        ),
        sha256=hashlib.sha256(canonical).hexdigest(),
        fillable_field_count=len(fillable),
    )


def _template_source_dir(tenant_id: uuid.UUID, template_id: uuid.UUID) -> str:
    """Mirror the layout document_template_workspace verifies reads against."""

    return os.path.join(
        settings.UPLOAD_DIR, str(tenant_id), "templates", str(template_id)
    )


async def _persist_template_source(
    *, tenant_id: uuid.UUID, template_id: uuid.UUID, filename: str, content: bytes
) -> str:
    directory = _template_source_dir(tenant_id, template_id)
    await asyncio.to_thread(
        Path(directory).mkdir, parents=True, exist_ok=True, mode=0o750
    )
    path = os.path.join(directory, os.path.basename(filename))

    def write_source() -> None:
        created = False
        try:
            # Exclusive creation: a retry must never half-overwrite the bytes an
            # existing template's sha256 already attests to.
            with Path(path).open("xb") as destination:
                created = True
                destination.write(content)
        except FileExistsError:
            existing = Path(path).read_bytes()
            if (
                hashlib.sha256(existing).hexdigest()
                != hashlib.sha256(content).hexdigest()
            ):
                raise
        except Exception:
            if created:
                Path(path).unlink(missing_ok=True)
            raise

    try:
        await asyncio.to_thread(write_source)
    except OSError as exc:
        raise CapabilityError(
            "template_source_write_failed",
            "The template source could not be stored",
        ) from exc
    return path


def template_variable_names(body: str) -> list[str]:
    """Return substitutable ``{{variable}}`` names in first-seen order."""

    names: list[str] = []
    for match in _VARIABLE_PATTERN.finditer(body):
        name = match.group(1).strip()
        if not name or name.startswith(("#", "/")):
            continue
        if name not in names:
            names.append(name)
    return names


def _derived_template_id(
    *, tenant_id: uuid.UUID, client_request_id: uuid.UUID
) -> uuid.UUID:
    """Give a retried push the same identity instead of a second draft."""

    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"lawhand:document-template:{tenant_id}:{client_request_id}",
    )


def _provenance(
    context: CapabilityContext,
    args: ProposeDocumentTemplateArgs,
    *,
    client_request_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Record who pushed this draft without retaining the template body."""

    return {
        "origin": "workspace_mcp",
        "channel": context.channel,
        "client_id": context.client_id,
        "grant_id": str(context.grant_id) if context.grant_id else None,
        "pushed_by_user_id": str(context.actor_user_id),
        "client_request_id": str(client_request_id) if client_request_id else None,
        "supersedes_template_id": (
            str(args.supersedes_template_id) if args.supersedes_template_id else None
        ),
    }


def _validated_schema(
    args: ProposeDocumentTemplateArgs, variables: list[str]
) -> dict[str, Any] | None:
    schema = args.variable_schema
    if schema is None:
        return None
    if not isinstance(schema, dict):
        raise CapabilityError(
            "invalid_variable_schema", "variable_schema must be an object"
        )
    try:
        validate_semantic_metadata(schema)
    except (TemplateSemanticsError, TemplateLogicError, TemplateRegionError) as exc:
        raise CapabilityError("invalid_variable_schema", str(exc)) from exc

    fields = schema.get("fields")
    if fields is not None:
        if not isinstance(fields, list):
            raise CapabilityError(
                "invalid_variable_schema", "variable_schema.fields must be a list"
            )
        declared = [
            str(field.get("name") or "").strip()
            for field in fields
            if isinstance(field, dict)
        ]
        # A field map that names variables the body does not contain would
        # silently never be filled. Catch it while a human can still fix it.
        unknown = sorted({name for name in declared if name and name not in variables})
        if unknown:
            raise CapabilityError(
                "invalid_variable_schema",
                "variable_schema names variable(s) absent from the body: "
                + ", ".join(unknown[:5]),
            )
    return schema


async def _load_tenant_template(
    context: CapabilityContext, template_id: uuid.UUID, *, lock: bool
) -> DocumentTemplate | None:
    statement = select(DocumentTemplate).where(
        DocumentTemplate.id == template_id,
        DocumentTemplate.tenant_id == context.tenant_id,
    )
    if lock:
        statement = statement.with_for_update()
    return await context.db.scalar(statement)


def _require_same_format(template: DocumentTemplate, requested: str) -> None:
    """A template's format is fixed once its source and field map exist."""

    current = str(template.format or "markdown").strip().casefold()
    if current != requested:
        raise CapabilityError(
            "template_format_immutable",
            f"This template is a {current} template and cannot be converted to "
            f"{requested} in place. Push a new template instead",
        )


def _is_draft(template: DocumentTemplate) -> bool:
    status = str(template.status or "").strip().casefold()
    return not template.is_active and status in {"", "draft"}


def _response(
    template: DocumentTemplate,
    *,
    variables: list[str],
    version_no: int | None,
    created: bool,
    idempotent_replay: bool,
    frontend_url: str,
    source: AnalyzedTemplateSource | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": str(template.id),
        "title": template.title,
        "category": template.category,
        "format": template.format,
        "status": template.status,
        "is_active": bool(template.is_active),
        "created": created,
        "idempotent_replay": idempotent_replay,
        "version_no": version_no,
        "body_sha256": body_sha256(template.body),
        "variables": variables[:MAX_TEMPLATE_VARIABLES],
        "supersedes_template_id": (template.source_provenance or {}).get(
            "supersedes_template_id"
        ),
        "template_url": f"{frontend_url.rstrip('/')}/templates/{template.id}",
        "approval_effect": (
            "The template is saved as an inactive draft. A LawHand user with "
            "template permissions must review and activate it before it can "
            "render any client document."
        ),
    }
    if source is not None:
        payload.update(
            {
                "source_sha256": source.sha256,
                "source_filename": source.filename,
                "source_size": len(source.content),
                "fillable_field_count": source.fillable_field_count,
            }
        )
    return payload


async def push_workspace_template(
    context: CapabilityContext, args: ProposeDocumentTemplateArgs
) -> dict[str, Any]:
    """Create or revise a draft firm template pushed by an MCP client."""

    if args.category not in CATEGORIES:
        raise CapabilityError(
            "invalid_template_category",
            "category must be one of: " + ", ".join(CATEGORIES),
        )

    source: AnalyzedTemplateSource | None = None
    if args.format in SOURCE_BACKED_FORMATS:
        source = await _analyzed_template_source(args)
        # The discovered map is authoritative; a caller-supplied body may only
        # refine the reviewer-facing text around it.
        body = args.body or source.analysis.body
        schema = source.analysis.variable_schema
        variables = _schema_variable_names(schema)
        _reject_unmapped_variables(body, variables)
    else:
        body = args.body or ""
        variables = template_variable_names(body)
        schema = _validated_schema(args, variables)

    if len(variables) > MAX_TEMPLATE_VARIABLES:
        raise CapabilityError(
            "too_many_template_variables",
            f"A template may declare at most {MAX_TEMPLATE_VARIABLES} variables",
        )

    superseded: DocumentTemplate | None = None
    if args.supersedes_template_id is not None:
        superseded = await _load_tenant_template(
            context, args.supersedes_template_id, lock=False
        )
        if superseded is None:
            raise CapabilityError("template_not_found", "Template not found")

    client_request_id = args.client_request_id
    if client_request_id is None and context.idempotency_key:
        client_request_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lawhand:{context.tenant_id}:{context.channel}:"
            f"{context.idempotency_key.strip()}",
        )

    if args.template_id is not None:
        return await _revise_draft(
            context,
            args,
            template_id=args.template_id,
            body=body,
            schema=schema,
            variables=variables,
            source=source,
            frontend_url=settings.FRONTEND_URL,
            client_request_id=client_request_id,
        )
    return await _create_draft(
        context,
        args,
        body=body,
        schema=schema,
        variables=variables,
        source=source,
        inherit_from=superseded,
        frontend_url=settings.FRONTEND_URL,
        client_request_id=client_request_id,
    )


def _schema_variable_names(schema: dict[str, Any] | None) -> list[str]:
    names: list[str] = []
    for field in (schema or {}).get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _reject_unmapped_variables(body: str, mapped: list[str]) -> None:
    """A source-backed body may not reference a variable the file cannot fill."""

    unmapped = sorted(set(template_variable_names(body)) - set(mapped))
    if unmapped:
        raise CapabilityError(
            "unmapped_template_variable",
            "The template body references variable(s) with no source mapping: "
            + ", ".join(unmapped[:5]),
        )


#: Placement fields describe where a template belongs, not what it says. An
#: omitted one keeps the value already on the row (or the superseded template's)
#: instead of silently clearing it.
_PLACEMENT_FIELDS = ("module", "stage", "jurisdiction", "kind")


def _template_fields(
    args: ProposeDocumentTemplateArgs,
    *,
    body: str,
    schema: dict[str, Any] | None,
    inherit_from: DocumentTemplate | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "title": args.title,
        "body": body,
        "category": args.category,
        "description": args.description,
        "variable_schema": schema,
    }
    for name in _PLACEMENT_FIELDS:
        fields[name] = getattr(args, name) or (
            getattr(inherit_from, name, None) if inherit_from is not None else None
        )
    return fields


def _source_fields(
    source: AnalyzedTemplateSource | None, *, storage_path: str
) -> dict[str, Any]:
    """Bind the retained file to the row that renders from it."""

    if source is None:
        return {}
    return {
        "branding_profile": source.analysis.branding_profile,
        "source_storage_path": storage_path,
        "source_filename": source.filename,
        "source_content_type": source.content_type,
        "source_sha256": source.sha256,
        "source_file_size": len(source.content),
    }


async def _revise_draft(
    context: CapabilityContext,
    args: ProposeDocumentTemplateArgs,
    *,
    template_id: uuid.UUID,
    body: str,
    schema: dict[str, Any] | None,
    variables: list[str],
    source: AnalyzedTemplateSource | None,
    frontend_url: str,
    client_request_id: uuid.UUID | None,
) -> dict[str, Any]:
    template = await _load_tenant_template(context, template_id, lock=True)
    if template is None:
        raise CapabilityError("template_not_found", "Template not found")
    _require_same_format(template, args.format)
    fields = _template_fields(args, body=body, schema=schema, inherit_from=template)
    if not _is_draft(template):
        # Editing what the firm is rendering today would be an approval, not a
        # proposal. Name the supported alternative rather than failing blankly.
        raise CapabilityError(
            "template_not_draft",
            "This template is live. Pass supersedes_template_id to propose a "
            "replacement draft instead of editing it in place",
        )

    unchanged = all(
        getattr(template, key, None) == value for key, value in fields.items()
    )
    if unchanged:
        return _response(
            template,
            variables=variables,
            version_no=template.current_version_no or None,
            created=False,
            idempotent_replay=True,
            frontend_url=frontend_url,
        )

    if source is not None:
        storage_path = await _persist_template_source(
            tenant_id=context.tenant_id,
            template_id=template.id,
            filename=source.filename,
            content=source.content,
        )
        fields.update(_source_fields(source, storage_path=storage_path))
    for key, value in fields.items():
        setattr(template, key, value)
    template.format = args.format
    template.is_active = False
    template.status = "draft"
    template.source_provenance = _provenance(
        context, args, client_request_id=client_request_id
    )
    await context.db.flush()
    version = await record_version(
        context.db,
        template=template,
        tenant_id=context.tenant_id,
        user_id=context.actor_user_id,
        change_summary=args.change_summary or "Revised by a Workspace MCP client",
    )
    return _response(
        template,
        variables=variables,
        version_no=version.version_no,
        created=False,
        idempotent_replay=False,
        frontend_url=frontend_url,
        source=source,
    )


async def _create_draft(
    context: CapabilityContext,
    args: ProposeDocumentTemplateArgs,
    *,
    body: str,
    schema: dict[str, Any] | None,
    variables: list[str],
    source: AnalyzedTemplateSource | None,
    inherit_from: DocumentTemplate | None,
    frontend_url: str,
    client_request_id: uuid.UUID | None,
) -> dict[str, Any]:
    fields = _template_fields(args, body=body, schema=schema, inherit_from=inherit_from)
    template_id = (
        _derived_template_id(
            tenant_id=context.tenant_id, client_request_id=client_request_id
        )
        if client_request_id is not None
        else uuid.uuid4()
    )
    existing = await _load_tenant_template(context, template_id, lock=True)
    if existing is not None:
        # A retry of the same push must return the same draft, but a different
        # template under the same key is a caller bug worth surfacing.
        if body_sha256(existing.body) != body_sha256(body) or (
            existing.title != args.title
        ):
            raise CapabilityError(
                "idempotency_conflict",
                "A different template already exists for this client_request_id",
            )
        return _response(
            existing,
            variables=variables,
            version_no=existing.current_version_no or None,
            created=False,
            idempotent_replay=True,
            frontend_url=frontend_url,
        )

    if source is not None:
        # Written before the row so a template can never point at a file that
        # does not exist; an orphaned source is reconciled, a broken row is not.
        fields.update(
            _source_fields(
                source,
                storage_path=await _persist_template_source(
                    tenant_id=context.tenant_id,
                    template_id=template_id,
                    filename=source.filename,
                    content=source.content,
                ),
            )
        )

    template = DocumentTemplate(
        id=template_id,
        tenant_id=context.tenant_id,
        format=args.format,
        visibility="tenant",
        is_active=False,
        status="draft",
        source_provenance=_provenance(
            context, args, client_request_id=client_request_id
        ),
        **fields,
    )
    context.db.add(template)
    await context.db.flush()
    version = await record_version(
        context.db,
        template=template,
        tenant_id=context.tenant_id,
        user_id=context.actor_user_id,
        change_summary=args.change_summary or "Authored by a Workspace MCP client",
    )
    return _response(
        template,
        variables=variables,
        version_no=version.version_no,
        created=True,
        idempotent_replay=False,
        frontend_url=frontend_url,
        source=source,
    )


__all__ = [
    "MAX_TEMPLATE_VARIABLES",
    "push_workspace_template",
    "template_variable_names",
]
