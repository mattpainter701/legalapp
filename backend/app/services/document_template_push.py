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

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.models.document_template import DocumentTemplate
from app.schemas.document_template import CATEGORIES
from app.schemas.workspace_mcp import ProposeDocumentTemplateArgs
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.document_template_versions import body_sha256, record_version
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
#: Formats whose body is derived from a retained binary source. Those still
#: require the in-app intake canvas, where a human anchors every field to the
#: exact source text, so they are refused here rather than half-supported.
SOURCE_BACKED_FORMATS = frozenset({"docx", "pdf"})


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


def _require_markdown(template: DocumentTemplate) -> None:
    template_format = str(template.format or "markdown").strip().casefold()
    if template_format in SOURCE_BACKED_FORMATS:
        raise CapabilityError(
            "template_format_not_pushable",
            "Word and PDF templates keep their original source file and must be "
            "revised through LawHand's template intake review",
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
) -> dict[str, Any]:
    return {
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


async def push_workspace_template(
    context: CapabilityContext, args: ProposeDocumentTemplateArgs
) -> dict[str, Any]:
    """Create or revise a draft Markdown firm template from an MCP client."""

    if args.category not in CATEGORIES:
        raise CapabilityError(
            "invalid_template_category",
            "category must be one of: " + ", ".join(CATEGORIES),
        )

    variables = template_variable_names(args.body)
    if len(variables) > MAX_TEMPLATE_VARIABLES:
        raise CapabilityError(
            "too_many_template_variables",
            f"A template may declare at most {MAX_TEMPLATE_VARIABLES} variables",
        )
    schema = _validated_schema(args, variables)

    superseded: DocumentTemplate | None = None
    if args.supersedes_template_id is not None:
        superseded = await _load_tenant_template(
            context, args.supersedes_template_id, lock=False
        )
        if superseded is None:
            raise CapabilityError("template_not_found", "Template not found")
        _require_markdown(superseded)

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
            schema=schema,
            variables=variables,
            frontend_url=settings.FRONTEND_URL,
            client_request_id=client_request_id,
        )
    return await _create_draft(
        context,
        args,
        schema=schema,
        variables=variables,
        inherit_from=superseded,
        frontend_url=settings.FRONTEND_URL,
        client_request_id=client_request_id,
    )


#: Placement fields describe where a template belongs, not what it says. An
#: omitted one keeps the value already on the row (or the superseded template's)
#: instead of silently clearing it.
_PLACEMENT_FIELDS = ("module", "stage", "jurisdiction", "kind")


def _template_fields(
    args: ProposeDocumentTemplateArgs,
    *,
    schema: dict[str, Any] | None,
    inherit_from: DocumentTemplate | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "title": args.title,
        "body": args.body,
        "category": args.category,
        "description": args.description,
        "variable_schema": schema,
    }
    for name in _PLACEMENT_FIELDS:
        fields[name] = getattr(args, name) or (
            getattr(inherit_from, name, None) if inherit_from is not None else None
        )
    return fields


async def _revise_draft(
    context: CapabilityContext,
    args: ProposeDocumentTemplateArgs,
    *,
    template_id: uuid.UUID,
    schema: dict[str, Any] | None,
    variables: list[str],
    frontend_url: str,
    client_request_id: uuid.UUID | None,
) -> dict[str, Any]:
    template = await _load_tenant_template(context, template_id, lock=True)
    if template is None:
        raise CapabilityError("template_not_found", "Template not found")
    _require_markdown(template)
    fields = _template_fields(args, schema=schema, inherit_from=template)
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

    for key, value in fields.items():
        setattr(template, key, value)
    template.format = "markdown"
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
    )


async def _create_draft(
    context: CapabilityContext,
    args: ProposeDocumentTemplateArgs,
    *,
    schema: dict[str, Any] | None,
    variables: list[str],
    inherit_from: DocumentTemplate | None,
    frontend_url: str,
    client_request_id: uuid.UUID | None,
) -> dict[str, Any]:
    fields = _template_fields(args, schema=schema, inherit_from=inherit_from)
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
        if body_sha256(existing.body) != body_sha256(args.body) or (
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

    template = DocumentTemplate(
        id=template_id,
        tenant_id=context.tenant_id,
        format="markdown",
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
    )


__all__ = [
    "MAX_TEMPLATE_VARIABLES",
    "push_workspace_template",
    "template_variable_names",
]
