"""Integrity-checked template access for review-first Workspace MCP drafts."""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.models.document_template import DocumentTemplate
from app.models.plugin import Matter
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.cloud_docx_snapshot import (
    CloudDocxSnapshotError,
    inspect_cloud_docx_snapshot,
)
from app.services.docx_templates import TemplateDocxError, fill_docx_template
from app.services.document_template_versions import published_template_view
from app.services.template_bindings import declared_bindings
from app.services.template_custom_fields import suggestions as custom_suggestions

settings = get_settings()
_VARIABLE_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")


@dataclass(frozen=True, slots=True)
class RenderedWorkspaceTemplate:
    template: DocumentTemplate
    title: str
    document_kind: str
    review_text: str
    source_docx_bytes: bytes | None
    template_sha256: str
    template_format: str
    variable_snapshot: dict[str, str]
    preview_truncated: bool


def _normalized(value: str | None) -> str:
    return " ".join(str(value or "").casefold().split())


def _compatible(template_value: str | None, matter_value: str | None) -> bool:
    expected = _normalized(template_value)
    actual = _normalized(matter_value)
    return not expected or bool(actual and expected == actual)


def _automation_ready(template: DocumentTemplate) -> bool:
    status = _normalized(template.status)
    if not status or status in {"draft", "deprecated", "inactive", "archived"}:
        return False
    template_format = _normalized(template.format)
    if (
        template_format == "pdf"
        and template.approved_at is None
        and not getattr(template, "published_version_no", None)
    ):
        return False
    if template.source_storage_path and not template.source_sha256:
        return False
    return bool(template.source_storage_path or str(template.body or "").strip())


async def require_workspace_template(
    context: CapabilityContext,
    *,
    matter_id,
    template_id,
) -> tuple[Matter, DocumentTemplate]:
    matter = await context.db.scalar(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == context.tenant_id,
        )
    )
    if matter is None:
        raise CapabilityError("matter_not_found", "Matter not found")
    template = await context.db.scalar(
        select(DocumentTemplate).where(
            DocumentTemplate.id == template_id,
            DocumentTemplate.tenant_id == context.tenant_id,
            DocumentTemplate.is_active.is_(True),
        )
    )
    if template is not None and getattr(template, "published_version_no", None):
        try:
            template = await published_template_view(context.db, template)
        except ValueError as exc:
            raise CapabilityError("template_not_found", str(exc)) from exc
    if template is None or not _automation_ready(template):
        raise CapabilityError("template_not_found", "Active template not found")
    if not _compatible(template.jurisdiction, matter.jurisdiction):
        raise CapabilityError(
            "template_incompatible",
            "Template jurisdiction does not match the matter",
        )
    if not _compatible(template.stage, matter.stage):
        raise CapabilityError(
            "template_incompatible", "Template stage does not match the matter"
        )
    if not _compatible(template.module, matter.primary_plugin):
        raise CapabilityError(
            "template_incompatible", "Template workflow does not match the matter"
        )
    rule = (template.variable_schema or {}).get("applicability")
    if rule:
        sources = await custom_suggestions(
            context.db,
            context.tenant_id,
            matter,
            declared_bindings(template.variable_schema),
        )
        source = sources.get(rule.get("field"))
        if (
            not source
            or source.suggested_value is None
            or source.suggested_value.strip().casefold()
            != str(rule.get("value", "")).strip().casefold()
        ):
            raise CapabilityError(
                "template_incompatible",
                "Template scenario does not match saved matter details",
            )
    return matter, template


def _safe_source_path(template: DocumentTemplate) -> Path | None:
    if not template.source_storage_path:
        return None
    resolved = Path(template.source_storage_path).resolve()
    expected_root = (
        Path(settings.UPLOAD_DIR)
        / str(template.tenant_id)
        / "templates"
        / str(template.id)
    ).resolve()
    try:
        resolved.relative_to(expected_root)
    except ValueError:
        return None
    return resolved


async def verified_template_source(template: DocumentTemplate) -> bytes:
    path = _safe_source_path(template)
    if path is None or not await asyncio.to_thread(path.is_file):
        raise CapabilityError(
            "template_source_unavailable", "The original template file is unavailable"
        )
    content = await asyncio.to_thread(path.read_bytes)
    digest = hashlib.sha256(content).hexdigest()
    if not template.source_sha256 or digest != template.source_sha256:
        raise CapabilityError(
            "template_integrity_failed",
            "The original template failed its integrity check",
        )
    return content


def _schema_fields(template: DocumentTemplate) -> list[dict[str, Any]]:
    schema = template.variable_schema or {}
    if not isinstance(schema, dict):
        return []
    fields = schema.get("fields")
    if isinstance(fields, list):
        return [field for field in fields if isinstance(field, dict)]
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = set(schema.get("required") or [])
        return [
            {"name": str(name), "required": name in required} for name in properties
        ]
    return []


def template_variable_names(template: DocumentTemplate) -> list[str]:
    names: list[str] = []
    for field in _schema_fields(template):
        name = str(
            field.get("name") or field.get("variable") or field.get("key") or ""
        ).strip()
        if name and name not in names:
            names.append(name)
    for match in _VARIABLE_PATTERN.finditer(str(template.body or "")):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names[:200]


def _validate_template_variables(
    template: DocumentTemplate, variables: dict[str, str]
) -> None:
    fields = _schema_fields(template)
    allowed = set(template_variable_names(template))
    unknown = set(variables) - allowed
    if unknown:
        raise CapabilityError(
            "unknown_template_variable",
            "Unknown template variable(s): " + ", ".join(sorted(unknown)[:5]),
        )
    missing = sorted(
        str(field.get("name") or field.get("variable") or field.get("key") or "")
        for field in fields
        if field.get("required")
        and not str(
            variables.get(
                str(
                    field.get("name") or field.get("variable") or field.get("key") or ""
                )
            )
            or ""
        ).strip()
    )
    if missing:
        raise CapabilityError(
            "required_template_variable_missing",
            "Required template variable(s) are empty: "
            + ", ".join(item for item in missing if item)[:500],
        )


def _canonical_docx_bytes(content: bytes) -> bytes:
    """Normalize ZIP metadata so an idempotent template render is byte-stable."""

    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content), "r") as source:
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as target:
            for original in sorted(source.infolist(), key=lambda item: item.filename):
                if original.is_dir():
                    continue
                normalized = zipfile.ZipInfo(
                    filename=original.filename,
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                normalized.compress_type = zipfile.ZIP_DEFLATED
                normalized.create_system = original.create_system
                normalized.external_attr = original.external_attr
                normalized.internal_attr = original.internal_attr
                normalized.comment = original.comment
                target.writestr(normalized, source.read(original.filename))
    return output.getvalue()


async def render_workspace_template(
    context: CapabilityContext,
    *,
    matter_id,
    template_id,
    variables: dict[str, str],
    title: str | None,
) -> RenderedWorkspaceTemplate:
    _matter, template = await require_workspace_template(
        context, matter_id=matter_id, template_id=template_id
    )
    _validate_template_variables(template, variables)
    template_format = _normalized(template.format) or "markdown"
    resolved_title = " ".join(str(title or template.title).split())
    if any(character in resolved_title for character in ("/", "\\", "\x00")):
        raise CapabilityError(
            "invalid_document_title", "Document title cannot contain a path"
        )

    if template_format == "docx":
        source = await verified_template_source(template)
        try:
            output = await asyncio.to_thread(
                fill_docx_template,
                source,
                variable_schema=template.variable_schema,
                variables=variables,
                enforce_required=True,
            )
            output = await asyncio.to_thread(_canonical_docx_bytes, output)
            snapshot = await asyncio.to_thread(
                inspect_cloud_docx_snapshot,
                output,
                filename=f"{resolved_title}.docx",
            )
        except (TemplateDocxError, CloudDocxSnapshotError) as exc:
            raise CapabilityError(
                "template_render_failed",
                "The Word template could not be safely rendered",
            ) from exc
        return RenderedWorkspaceTemplate(
            template=template,
            title=resolved_title,
            document_kind=template.kind or template.category or "other",
            review_text=snapshot.review_text,
            source_docx_bytes=output,
            template_sha256=str(template.source_sha256),
            template_format="docx",
            variable_snapshot=dict(sorted(variables.items())),
            preview_truncated=snapshot.preview_truncated,
        )

    if template_format == "pdf":
        raise CapabilityError(
            "template_preview_required",
            "PDF templates require an exact LawHand visual preview before they can be saved",
        )
    if template_format != "markdown":
        raise CapabilityError(
            "unsupported_template_format",
            "Only approved DOCX and Markdown templates can be proposed from Workspace MCP",
        )

    body = str(template.body or "")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        return variables.get(name, match.group(0))

    rendered = _VARIABLE_PATTERN.sub(replace, body).strip()
    unresolved = sorted(
        {match.group(1).strip() for match in _VARIABLE_PATTERN.finditer(rendered)}
    )
    if unresolved:
        raise CapabilityError(
            "required_template_variable_missing",
            "Provide values for template variable(s): " + ", ".join(unresolved[:10]),
        )
    if not rendered or len(rendered) > 50_000:
        raise CapabilityError(
            "template_render_failed",
            "Rendered template text is empty or exceeds the review limit",
        )
    return RenderedWorkspaceTemplate(
        template=template,
        title=resolved_title,
        document_kind=template.kind or template.category or "other",
        review_text=rendered,
        source_docx_bytes=None,
        template_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        template_format="markdown",
        variable_snapshot=dict(sorted(variables.items())),
        preview_truncated=False,
    )
