"""Bounded document fact proposals. Source text is data, never instructions.

Only exact label: value lines are proposed. Acceptance is a separate user action,
using a signed source/version/current-value contract and re-reading source bytes.
"""

import asyncio
import hashlib
from types import SimpleNamespace
from datetime import datetime, timezone
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer, BadData
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.config import get_settings
from app.database import set_tenant_context
from app.services.docx_templates import validate_docx_package
from app.models.configurable_workflow import (
    CustomFieldDefinition,
    MatterCustomFieldValue,
)
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.schemas.configurable_workflow import normalized_field_value
from app.services.configurable_workflows import value_hmac
from app.services.matter_file_store import MatterFileStore, MatterFileReadError
from app.utils.text_processing import extract_text


class FactAccept(BaseModel):
    proposal_token: str = Field(max_length=12000)
    value: str | bool = Field(union_mode="left_to_right")
    replace_existing: bool = False


def parse_label_values(text, label, field_type, options):
    """Keep ambiguity visible; no fuzzy or model-based truth assertions."""
    matches = []
    for line_number, line in enumerate(text.splitlines(), 1):
        prefix, separator, raw = line.partition(":")
        if not separator or prefix.strip().casefold() != label.strip().casefold():
            continue
        raw = raw.strip()
        try:
            value = normalize_review_value(field_type, options, raw)
        except ValueError:
            matches.append(
                {"line": line_number, "value": None, "status": "unsupported_value"}
            )
        else:
            matches.append({"line": line_number, "value": value, "status": "suggested"})
    return matches[:50]


def normalize_review_value(field_type, options, value):
    if field_type == "boolean" and isinstance(value, str):
        booleans = {"yes": True, "true": True, "no": False, "false": False}
        if value.strip().lower() not in booleans:
            raise ValueError("Choose yes or no")
        value = booleans[value.strip().lower()]
    normalized = normalized_field_value(field_type, options, value)
    if normalized is None or normalized == "":
        raise ValueError("A reviewed value is required")
    return normalized


def signer():
    return URLSafeTimedSerializer(
        get_settings().SECRET_KEY, salt="studio-fact-review-v1"
    )


async def context(
    db, user, matter_id, document_id, field_id, *, lock=False, verified_content=None
):
    if lock and verified_content is None:
        raise ValueError("External reads must finish before acceptance locks")
    matter_query = select(Matter).where(
        Matter.tenant_id == user.tenant_id, Matter.id == matter_id
    )
    if lock:
        matter_query = matter_query.with_for_update()
    matter = await db.scalar(matter_query.execution_options(populate_existing=True))
    document_query = select(MatterDocument).where(
        MatterDocument.tenant_id == user.tenant_id,
        MatterDocument.matter_id == matter_id,
        MatterDocument.id == document_id,
    )
    if lock:
        document_query = document_query.with_for_update()
    document = await db.scalar(document_query.execution_options(populate_existing=True))
    field_query = select(CustomFieldDefinition).where(
        CustomFieldDefinition.tenant_id == user.tenant_id,
        CustomFieldDefinition.id == field_id,
        CustomFieldDefinition.entity_type == "matter",
        CustomFieldDefinition.active.is_(True),
        CustomFieldDefinition.sensitive.is_(False),
        CustomFieldDefinition.field_type.in_(
            ["text", "long_text", "number", "date", "boolean", "single_select"]
        ),
    )
    if lock:
        field_query = field_query.with_for_update()
    field = await db.scalar(field_query.execution_options(populate_existing=True))
    if matter is None or document is None or field is None:
        raise HTTPException(
            status_code=404, detail="Matter, source, or supported field not found"
        )
    if document.storage_state in {"conflict", "deleted", "pending"}:
        raise HTTPException(
            status_code=409,
            detail="Reconcile the source document before reviewing its facts",
        )
    if not document.filename.lower().endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(
            status_code=422, detail="Choose a PDF, Word, or plain-text source"
        )
    if verified_content is None:
        # OAuth refresh can commit. Never take final acceptance locks until
        # all external reads finish and the transaction's tenant is restored.
        stable_user = SimpleNamespace(id=user.id, tenant_id=user.tenant_id)
        before_source = (
            document.updated_at,
            document.provider_version_id,
            document.provider_etag,
        )
        try:
            content = await MatterFileStore().read_matter_file_bytes(
                db=db,
                tenant_id=str(stable_user.tenant_id),
                document=document,
                expected_sha256=document.document_sha256
                if document.storage_state == "verified"
                else None,
                max_bytes=10 * 1024 * 1024,
            )
        except MatterFileReadError as exc:
            raise HTTPException(
                status_code=409,
                detail="The exact source could not be verified; reopen or reconcile it",
            ) from exc
        await set_tenant_context(db, str(stable_user.tenant_id))
        resolved = await context(
            db,
            stable_user,
            matter_id,
            document_id,
            field_id,
            lock=False,
            verified_content=content,
        )
        fresh_document = resolved[1]
        if before_source != (
            fresh_document.updated_at,
            fresh_document.provider_version_id,
            fresh_document.provider_etag,
        ):
            raise HTTPException(
                status_code=409, detail="Source changed during review. Read it again."
            )
        return resolved
    content = verified_content
    value_query = select(MatterCustomFieldValue).where(
        MatterCustomFieldValue.tenant_id == user.tenant_id,
        MatterCustomFieldValue.matter_id == matter_id,
        MatterCustomFieldValue.field_definition_id == field_id,
    )
    if lock:
        value_query = value_query.with_for_update()
    value = await db.scalar(value_query.execution_options(populate_existing=True))
    contract = {
        "tenant": str(user.tenant_id),
        "actor": str(user.id),
        "matter": str(matter_id),
        "document": str(document_id),
        "field": str(field_id),
        "schema_version": field.schema_version,
        "definition_updated": field.updated_at.isoformat(),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "provider_version": document.provider_version_id,
        "provider_etag": document.provider_etag,
        "source_updated": document.updated_at.isoformat(),
        "previous_hmac": value.value_hmac if value else None,
        "previous_updated": value.updated_at.isoformat() if value else None,
    }
    return field, document, value, content, contract


async def propose(db, user, matter_id, document_id, field_id):
    field, document, value, content, contract = await context(
        db, user, matter_id, document_id, field_id
    )
    try:
        if document.filename.lower().endswith(".docx"):
            await asyncio.to_thread(validate_docx_package, content)
        text = await asyncio.to_thread(
            extract_text,
            content,
            document.content_type or "",
            document.filename,
            max_pdf_pages=100,
            max_pdf_chars=200000,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Source text could not be read. Review the original and enter the value manually.",
        ) from exc
    if len(text) > 200000:
        raise HTTPException(
            status_code=422, detail="Source text exceeds the review limit"
        )
    candidates = parse_label_values(
        text, field.label, field.field_type, field.options_json
    )
    distinct = {
        value_hmac(item["value"]) for item in candidates if item["value"] is not None
    }
    return {
        "proposal_token": signer().dumps(contract),
        "field_label": field.label,
        "field_type": field.field_type,
        "options": field.options_json,
        "source_filename": document.filename,
        "source_sha256": contract["source_sha256"],
        "source_document_id": str(document.id),
        "current_value": value.value_json if value else None,
        "candidates": candidates,
        "status": "conflicting_sources"
        if len(distinct) > 1
        else "suggested"
        if distinct
        else "missing",
        "review_required": True,
    }


async def accept(db, user, matter_id, document_id, field_id, payload):
    try:
        expected = signer().loads(payload.proposal_token, max_age=1800)
    except BadData as exc:
        raise HTTPException(
            status_code=409, detail="This review expired. Read the source again."
        ) from exc
    user = SimpleNamespace(id=user.id, tenant_id=user.tenant_id)
    _, _, _, content, read_contract = await context(
        db, user, matter_id, document_id, field_id
    )
    await set_tenant_context(db, str(user.tenant_id))
    field, document, previous, content, current = await context(
        db, user, matter_id, document_id, field_id, lock=True, verified_content=content
    )
    if read_contract != current:
        raise HTTPException(
            status_code=409,
            detail="A value or source changed during review. Read it again.",
        )
    if expected != current:
        raise HTTPException(
            status_code=409,
            detail="The source, field, or existing value changed. Review again before accepting.",
        )
    try:
        value = normalize_review_value(
            field.field_type, field.options_json, payload.value
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if previous and previous.value_json != value and not payload.replace_existing:
        raise HTTPException(
            status_code=409,
            detail="An existing value differs. Confirm replacement after reviewing the conflict.",
        )
    stamp = datetime.now(timezone.utc)
    evidence = {
        **current,
        "accepted_value_hmac": value_hmac(value),
        "reviewed_at": stamp.isoformat(),
        "method": "human_reviewed_label_value",
    }
    values = dict(
        tenant_id=user.tenant_id,
        matter_id=matter_id,
        field_definition_id=field_id,
        entity_type="matter",
        value_json=value,
        value_hmac=value_hmac(value),
        updated_by_user_id=user.id,
        updated_at=stamp,
    )
    # Other custom-field writes also lock the parent matter. The unique key
    # protects the previously missing-value case against duplicate insertion.
    await db.execute(
        insert(MatterCustomFieldValue)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_matter_custom_field_values_field",
            set_={
                key: values[key]
                for key in (
                    "value_json",
                    "value_hmac",
                    "updated_by_user_id",
                    "updated_at",
                )
            },
        )
    )
    db.add(
        MatterEvent(
            tenant_id=user.tenant_id,
            matter_id=matter_id,
            event_type="template_fact_reviewed",
            title="Document fact reviewed",
            content="A staff member reviewed a source document and accepted a matter detail.",
            created_by=user.id,
            metadata_json=evidence,
        )
    )
    await db.commit()
    return {
        "status": "accepted",
        "field_definition_id": str(field_id),
        "reviewed_at": stamp.isoformat(),
    }
