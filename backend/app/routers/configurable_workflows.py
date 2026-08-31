"""Tenant configuration and review-first matter workflow endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.configurable_workflow import (
    ContactCustomFieldValue,
    CustomFieldDefinition,
    MatterCustomFieldValue,
    MatterWorkflowChecklistDefinition,
    MatterWorkflowFieldRequirement,
    MatterWorkflowRun,
    MatterWorkflowStageDefinition,
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.models.contact import Contact
from app.models.plugin import Matter
from app.schemas.configurable_workflow import (
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionUpdate,
    CustomFieldValuesUpdate,
    WorkflowApplyRequest,
    WorkflowRollbackRequest,
    WorkflowTemplateCreate,
    WorkflowTemplateVersionCreate,
    normalized_field_value,
)
from app.services.access_control import require_capability
from app.services.configurable_workflows import (
    append_run_event,
    apply_run,
    build_preview,
    definition_payload,
    digest_payload,
    load_template_bundle,
    rollback_run,
    run_response,
    stored_definition_payload,
    value_hmac,
)


router = APIRouter(tags=["configurable-workflows"])


def _field_response(field: CustomFieldDefinition, value: object = None) -> dict:
    return {
        "id": field.id,
        "entity_type": field.entity_type,
        "field_key": field.field_key,
        "label": field.label,
        "description": field.description,
        "field_type": field.field_type,
        "options": field.options_json,
        "required": field.required,
        "sensitive": field.sensitive,
        "active": field.active,
        "schema_version": field.schema_version,
        # Sensitive values may be written and used for required-field checks,
        # but are never returned through the configuration API.
        "value": None if field.sensitive else value,
        "has_value": value is not None,
        "created_at": field.created_at,
        "updated_at": field.updated_at,
    }


async def _matter_or_404(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Matter:
    query = select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update()
    matter = await db.scalar(query)
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _contact_or_404(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Contact:
    query = select(Contact).where(
        Contact.id == contact_id, Contact.tenant_id == tenant_id
    )
    if lock:
        query = query.with_for_update()
    contact = await db.scalar(query)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.get("/api/workflow-config/fields")
async def list_field_definitions(
    entity_type: str | None = Query(default=None, pattern="^(matter|contact)$"),
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    filters = [CustomFieldDefinition.tenant_id == user.tenant_id]
    if entity_type:
        filters.append(CustomFieldDefinition.entity_type == entity_type)
    if not include_inactive:
        filters.append(CustomFieldDefinition.active.is_(True))
    rows = (
        (
            await db.execute(
                select(CustomFieldDefinition)
                .where(*filters)
                .order_by(
                    CustomFieldDefinition.entity_type, CustomFieldDefinition.label
                )
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_field_response(row) for row in rows]}


@router.post("/api/workflow-config/fields", status_code=201)
async def create_field_definition(
    body: CustomFieldDefinitionCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    field = CustomFieldDefinition(
        tenant_id=user.tenant_id,
        entity_type=body.entity_type,
        field_key=body.field_key,
        label=body.label,
        description=body.description,
        field_type=body.field_type,
        options_json=body.options,
        required=body.required,
        sensitive=body.sensitive,
        created_by_user_id=user.id,
    )
    db.add(field)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A field with that key already exists for this firm and scope",
        ) from None
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(field)
    return _field_response(field)


@router.patch("/api/workflow-config/fields/{field_id}")
async def update_field_definition(
    field_id: uuid.UUID,
    body: CustomFieldDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    field = await db.scalar(
        select(CustomFieldDefinition)
        .where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.tenant_id == user.tenant_id,
        )
        .with_for_update()
    )
    if field is None:
        raise HTTPException(status_code=404, detail="Custom field not found")
    if field.schema_version != body.expected_schema_version:
        raise HTTPException(
            status_code=409, detail="Custom field changed; reload before editing"
        )
    changes = body.model_dump(exclude_unset=True)
    changes.pop("expected_schema_version", None)
    if field.sensitive and changes.get("sensitive") is False:
        raise HTTPException(
            status_code=409,
            detail="Sensitive classification cannot be removed; create a new field",
        )
    if "options" in changes:
        if changes["options"] != field.options_json:
            value_model = (
                MatterCustomFieldValue
                if field.entity_type == "matter"
                else ContactCustomFieldValue
            )
            stored_value = await db.scalar(
                select(value_model.id)
                .where(
                    value_model.tenant_id == user.tenant_id,
                    value_model.field_definition_id == field.id,
                )
                .limit(1)
            )
            if stored_value is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Options with stored values cannot change; create a new field",
                )
        validated = CustomFieldDefinitionCreate(
            entity_type=field.entity_type,
            field_key=field.field_key,
            label=changes.get("label", field.label),
            description=changes.get("description", field.description),
            field_type=field.field_type,
            options=changes["options"],
            required=changes.get("required", field.required),
            sensitive=changes.get("sensitive", field.sensitive),
        )
        changes["options_json"] = validated.options
        changes.pop("options", None)
    for name, value in changes.items():
        setattr(field, name, value)
    field.schema_version += 1
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(field)
    return _field_response(field)


async def _list_entity_values(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
) -> list[dict]:
    value_model = (
        MatterCustomFieldValue if entity_type == "matter" else ContactCustomFieldValue
    )
    entity_column = (
        MatterCustomFieldValue.matter_id
        if entity_type == "matter"
        else ContactCustomFieldValue.contact_id
    )
    rows = (
        await db.execute(
            select(CustomFieldDefinition, value_model)
            .outerjoin(
                value_model,
                (value_model.field_definition_id == CustomFieldDefinition.id)
                & (value_model.tenant_id == CustomFieldDefinition.tenant_id)
                & (entity_column == entity_id),
            )
            .where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
                CustomFieldDefinition.active.is_(True),
            )
            .order_by(CustomFieldDefinition.label)
        )
    ).all()
    return [
        _field_response(field, value.value_json if value is not None else None)
        for field, value in rows
    ]


async def _replace_entity_values(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    body: CustomFieldValuesUpdate,
) -> None:
    requested = {item.field_definition_id: item.value for item in body.values}
    definitions = (
        (
            await db.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.tenant_id == tenant_id,
                    CustomFieldDefinition.entity_type == entity_type,
                    CustomFieldDefinition.id.in_(requested),
                    CustomFieldDefinition.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(definitions) != len(requested):
        raise HTTPException(status_code=404, detail="Custom field not found")
    value_model = (
        MatterCustomFieldValue if entity_type == "matter" else ContactCustomFieldValue
    )
    entity_column_name = "matter_id" if entity_type == "matter" else "contact_id"
    constraint_name = (
        "uq_matter_custom_field_values_field"
        if entity_type == "matter"
        else "uq_contact_custom_field_values_field"
    )
    for field in definitions:
        try:
            normalized = normalized_field_value(
                field.field_type, field.options_json, requested[field.id]
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"{field.field_key}: {exc}"
            ) from None
        if normalized in (None, "", []):
            if field.required:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field.field_key}: a value is required",
                )
            normalized = None
        if field.field_type == "contact" and normalized is not None:
            linked = await db.scalar(
                select(Contact.id).where(
                    Contact.id == uuid.UUID(str(normalized)),
                    Contact.tenant_id == tenant_id,
                )
            )
            if linked is None:
                raise HTTPException(status_code=404, detail="Contact not found")
        if normalized is None:
            await db.execute(
                delete(value_model).where(
                    value_model.tenant_id == tenant_id,
                    getattr(value_model, entity_column_name) == entity_id,
                    value_model.field_definition_id == field.id,
                )
            )
            continue
        values = {
            "tenant_id": tenant_id,
            entity_column_name: entity_id,
            "field_definition_id": field.id,
            "entity_type": entity_type,
            "linked_contact_id": (
                uuid.UUID(str(normalized)) if field.field_type == "contact" else None
            ),
            "value_json": normalized,
            "value_hmac": value_hmac(normalized),
            "updated_by_user_id": actor_user_id,
        }
        await db.execute(
            pg_insert(value_model)
            .values(**values)
            .on_conflict_do_update(
                constraint=constraint_name,
                set_={
                    "value_json": normalized,
                    "linked_contact_id": (
                        uuid.UUID(str(normalized))
                        if field.field_type == "contact"
                        else None
                    ),
                    "value_hmac": values["value_hmac"],
                    "updated_by_user_id": actor_user_id,
                    "updated_at": func.now(),
                },
            )
        )


@router.get("/api/matters/{matter_id}/custom-fields")
async def list_matter_custom_fields(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await _matter_or_404(db, user.tenant_id, matter_id)
    return {
        "items": await _list_entity_values(
            db,
            tenant_id=user.tenant_id,
            entity_type="matter",
            entity_id=matter_id,
        )
    }


@router.put("/api/matters/{matter_id}/custom-fields")
async def update_matter_custom_fields(
    matter_id: uuid.UUID,
    body: CustomFieldValuesUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await _matter_or_404(db, user.tenant_id, matter_id, lock=True)
    await _replace_entity_values(
        db,
        tenant_id=user.tenant_id,
        entity_type="matter",
        entity_id=matter_id,
        actor_user_id=user.id,
        body=body,
    )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    return {
        "items": await _list_entity_values(
            db,
            tenant_id=user.tenant_id,
            entity_type="matter",
            entity_id=matter_id,
        )
    }


@router.get("/api/contacts/{contact_id}/custom-fields")
async def list_contact_custom_fields(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await _contact_or_404(db, user.tenant_id, contact_id)
    return {
        "items": await _list_entity_values(
            db,
            tenant_id=user.tenant_id,
            entity_type="contact",
            entity_id=contact_id,
        )
    }


@router.put("/api/contacts/{contact_id}/custom-fields")
async def update_contact_custom_fields(
    contact_id: uuid.UUID,
    body: CustomFieldValuesUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await _contact_or_404(db, user.tenant_id, contact_id, lock=True)
    await _replace_entity_values(
        db,
        tenant_id=user.tenant_id,
        entity_type="contact",
        entity_id=contact_id,
        actor_user_id=user.id,
        body=body,
    )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    return {
        "items": await _list_entity_values(
            db,
            tenant_id=user.tenant_id,
            entity_type="contact",
            entity_id=contact_id,
        )
    }


async def _assert_required_fields(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    field_ids: list[uuid.UUID],
) -> list[CustomFieldDefinition]:
    if not field_ids:
        return []
    rows = (
        (
            await db.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.tenant_id == tenant_id,
                    CustomFieldDefinition.entity_type == "matter",
                    CustomFieldDefinition.active.is_(True),
                    CustomFieldDefinition.id.in_(field_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != len(field_ids):
        raise HTTPException(status_code=404, detail="Required matter field not found")
    return list(rows)


async def _create_template_version(
    db: AsyncSession,
    *,
    template: MatterWorkflowTemplate,
    version_number: int,
    body: WorkflowTemplateCreate | WorkflowTemplateVersionCreate,
    actor_user_id: uuid.UUID,
) -> MatterWorkflowTemplateVersion:
    required_fields = await _assert_required_fields(
        db, template.tenant_id, body.required_field_definition_ids
    )
    definition = definition_payload(body)
    version = MatterWorkflowTemplateVersion(
        tenant_id=template.tenant_id,
        template_id=template.id,
        version=version_number,
        status="draft",
        initial_stage_key=body.initial_stage_key,
        definition_sha256=digest_payload(definition),
        created_by_user_id=actor_user_id,
    )
    db.add(version)
    await db.flush()
    for position, stage in enumerate(body.stages):
        db.add(
            MatterWorkflowStageDefinition(
                tenant_id=template.tenant_id,
                template_version_id=version.id,
                stage_key=stage.stage_key,
                label=stage.label,
                position=position,
            )
        )
    await db.flush()
    for position, item in enumerate(body.checklist):
        db.add(
            MatterWorkflowChecklistDefinition(
                tenant_id=template.tenant_id,
                template_version_id=version.id,
                stage_key=item.stage_key,
                item_key=item.item_key,
                title=item.title,
                description=item.description,
                position=position,
                task_type=item.task_type,
                priority=item.priority,
                due_offset_days=item.due_offset_days,
                assignee_role=item.assignee_role,
            )
        )
    for field in required_fields:
        db.add(
            MatterWorkflowFieldRequirement(
                tenant_id=template.tenant_id,
                template_version_id=version.id,
                field_definition_id=field.id,
            )
        )
    await db.flush()
    return version


@router.post("/api/workflow-config/templates", status_code=201)
async def create_workflow_template(
    body: WorkflowTemplateCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    template = MatterWorkflowTemplate(
        tenant_id=user.tenant_id,
        name=" ".join(body.name.split()),
        description=body.description,
        created_by_user_id=user.id,
    )
    db.add(template)
    try:
        await db.flush()
        version = await _create_template_version(
            db,
            template=template,
            version_number=1,
            body=body,
            actor_user_id=user.id,
        )
        await db.commit()
    except HTTPException:
        raise
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="A workflow template with that name already exists"
        ) from None
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "active": template.active,
        "version_id": version.id,
        "version": version.version,
        "status": version.status,
        "definition_sha256": version.definition_sha256,
    }


@router.post("/api/workflow-config/templates/{template_id}/versions", status_code=201)
async def create_workflow_template_version(
    template_id: uuid.UUID,
    body: WorkflowTemplateVersionCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    template = await db.scalar(
        select(MatterWorkflowTemplate)
        .where(
            MatterWorkflowTemplate.id == template_id,
            MatterWorkflowTemplate.tenant_id == user.tenant_id,
        )
        .with_for_update()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    if not template.active:
        raise HTTPException(status_code=409, detail="Workflow template is inactive")
    latest = await db.scalar(
        select(func.max(MatterWorkflowTemplateVersion.version)).where(
            MatterWorkflowTemplateVersion.tenant_id == user.tenant_id,
            MatterWorkflowTemplateVersion.template_id == template.id,
        )
    )
    if latest != body.expected_latest_version:
        raise HTTPException(
            status_code=409, detail="Workflow template changed; reload before editing"
        )
    version = await _create_template_version(
        db,
        template=template,
        version_number=int(latest or 0) + 1,
        body=body,
        actor_user_id=user.id,
    )
    await db.commit()
    return {
        "template_id": template.id,
        "version_id": version.id,
        "version": version.version,
        "status": version.status,
        "definition_sha256": version.definition_sha256,
    }


async def _template_version_response(
    db: AsyncSession, tenant_id: uuid.UUID, version_id: uuid.UUID
) -> dict:
    template, version, stages, checklist, fields = await load_template_bundle(
        db, tenant_id, version_id
    )
    return {
        "template_id": template.id,
        "template_name": template.name,
        "template_description": template.description,
        "template_active": template.active,
        "version_id": version.id,
        "version": version.version,
        "status": version.status,
        "initial_stage_key": version.initial_stage_key,
        "definition_sha256": version.definition_sha256,
        "approved_by_user_id": version.approved_by_user_id,
        "approved_at": version.approved_at,
        "stages": [
            {"stage_key": stage.stage_key, "label": stage.label} for stage in stages
        ],
        "checklist": [
            {
                "item_key": item.item_key,
                "stage_key": item.stage_key,
                "title": item.title,
                "description": item.description,
                "task_type": item.task_type,
                "priority": item.priority,
                "due_offset_days": item.due_offset_days,
                "assignee_role": item.assignee_role,
            }
            for item in checklist
        ],
        "required_fields": [_field_response(field) for field in fields],
        "created_at": version.created_at,
    }


@router.get("/api/workflow-config/templates")
async def list_workflow_templates(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    template_filter = [MatterWorkflowTemplate.tenant_id == user.tenant_id]
    if not include_inactive:
        template_filter.append(MatterWorkflowTemplate.active.is_(True))
    version_ids = (
        await db.execute(
            select(MatterWorkflowTemplateVersion.id)
            .join(
                MatterWorkflowTemplate,
                MatterWorkflowTemplate.id == MatterWorkflowTemplateVersion.template_id,
            )
            .where(*template_filter)
            .order_by(
                MatterWorkflowTemplate.name,
                MatterWorkflowTemplateVersion.version.desc(),
            )
        )
    ).scalars()
    return {
        "items": [
            await _template_version_response(db, user.tenant_id, version_id)
            for version_id in version_ids
        ]
    }


@router.post(
    "/api/workflow-config/templates/{template_id}/versions/{version_id}/approve"
)
async def approve_workflow_template_version(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("approve_legal_work")),
):
    await set_tenant_context(db, str(user.tenant_id))
    template, version, stages, checklist, fields = await load_template_bundle(
        db, user.tenant_id, version_id, lock=True
    )
    if template.id != template_id:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    if not template.active:
        raise HTTPException(status_code=409, detail="Workflow template is inactive")
    if version.status == "approved":
        return await _template_version_response(db, user.tenant_id, version.id)
    if any(not field.active for field in fields):
        raise HTTPException(status_code=409, detail="A required field is inactive")
    expected = digest_payload(
        stored_definition_payload(version, stages, checklist, fields)
    )
    if expected != version.definition_sha256:
        raise HTTPException(status_code=409, detail="Workflow definition hash mismatch")
    version.status = "approved"
    version.approved_by_user_id = user.id
    version.approved_at = datetime.now(timezone.utc)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    return await _template_version_response(db, user.tenant_id, version.id)


@router.post("/api/workflow-config/templates/{template_id}/archive")
async def archive_workflow_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    template = await db.scalar(
        select(MatterWorkflowTemplate)
        .where(
            MatterWorkflowTemplate.id == template_id,
            MatterWorkflowTemplate.tenant_id == user.tenant_id,
        )
        .with_for_update()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    template.active = False
    await db.commit()
    return {"id": template.id, "active": template.active}


@router.get("/api/matters/{matter_id}/workflow-templates")
async def list_approved_matter_workflow_templates(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await _matter_or_404(db, user.tenant_id, matter_id)
    latest_approved = (
        select(
            MatterWorkflowTemplateVersion.template_id,
            func.max(MatterWorkflowTemplateVersion.version).label("max_version"),
        )
        .where(
            MatterWorkflowTemplateVersion.tenant_id == user.tenant_id,
            MatterWorkflowTemplateVersion.status == "approved",
        )
        .group_by(MatterWorkflowTemplateVersion.template_id)
        .subquery()
    )
    version_ids = (
        await db.execute(
            select(MatterWorkflowTemplateVersion.id)
            .join(
                latest_approved,
                (
                    latest_approved.c.template_id
                    == MatterWorkflowTemplateVersion.template_id
                )
                & (
                    latest_approved.c.max_version
                    == MatterWorkflowTemplateVersion.version
                ),
            )
            .join(
                MatterWorkflowTemplate,
                MatterWorkflowTemplate.id == MatterWorkflowTemplateVersion.template_id,
            )
            .where(
                MatterWorkflowTemplateVersion.tenant_id == user.tenant_id,
                MatterWorkflowTemplate.active.is_(True),
            )
            .order_by(MatterWorkflowTemplate.name)
        )
    ).scalars()
    return {
        "items": [
            await _template_version_response(db, user.tenant_id, version_id)
            for version_id in version_ids
        ]
    }


@router.post("/api/matters/{matter_id}/workflow-runs/preview", status_code=201)
async def preview_matter_workflow(
    matter_id: uuid.UUID,
    template_version_id: uuid.UUID,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _matter_or_404(db, user.tenant_id, matter_id)
    request_payload = {
        "matter_id": str(matter_id),
        "template_version_id": str(template_version_id),
    }
    request_sha256 = digest_payload(request_payload)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:value))"),
        {"value": f"workflow:{user.tenant_id}:{matter_id}:{idempotency_key}"},
    )
    existing = await db.scalar(
        select(MatterWorkflowRun).where(
            MatterWorkflowRun.tenant_id == user.tenant_id,
            MatterWorkflowRun.matter_id == matter_id,
            MatterWorkflowRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_sha256 != request_sha256:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used with different input",
            )
        return {
            "run_id": existing.id,
            "planned_at": existing.created_at,
            **existing.preview_json,
            "preview_sha256": existing.preview_sha256,
        }
    preview, preview_sha256, template_sha256, matter_sha256 = await build_preview(
        db,
        matter=matter,
        version_id=template_version_id,
        as_of=date.today(),
    )
    run = MatterWorkflowRun(
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        template_version_id=template_version_id,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        template_sha256=template_sha256,
        matter_sha256=matter_sha256,
        preview_sha256=preview_sha256,
        preview_json=preview,
        prior_stage=matter.stage,
        planned_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()
    await append_run_event(
        db,
        run,
        event_type="previewed",
        actor_user_id=user.id,
        detail={
            "preview_sha256": preview_sha256,
            "can_apply": preview["can_apply"],
            "missing_required_field_count": len(preview["missing_required_fields"]),
            "missing_assignee_count": len(preview["missing_assignees"]),
        },
    )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(run)
    return {
        "run_id": run.id,
        "planned_at": run.created_at,
        **preview,
        "preview_sha256": run.preview_sha256,
    }


async def _run_or_404(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    run_id: uuid.UUID,
    lock: bool = False,
) -> MatterWorkflowRun:
    query = select(MatterWorkflowRun).where(
        MatterWorkflowRun.id == run_id,
        MatterWorkflowRun.tenant_id == tenant_id,
        MatterWorkflowRun.matter_id == matter_id,
    )
    if lock:
        query = query.with_for_update()
    run = await db.scalar(query)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


@router.post("/api/matters/{matter_id}/workflow-runs/{run_id}/apply")
async def apply_matter_workflow(
    matter_id: uuid.UUID,
    run_id: uuid.UUID,
    body: WorkflowApplyRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("approve_legal_work")),
):
    await set_tenant_context(db, str(user.tenant_id))
    run = await _run_or_404(
        db,
        tenant_id=user.tenant_id,
        matter_id=matter_id,
        run_id=run_id,
        lock=True,
    )
    matter = await _matter_or_404(db, user.tenant_id, matter_id, lock=True)
    await apply_run(
        db,
        run=run,
        matter=matter,
        actor_user_id=user.id,
        preview_sha256=body.preview_sha256,
    )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(run)
    return await run_response(db, run)


@router.get("/api/matters/{matter_id}/workflow-runs")
async def list_matter_workflow_runs(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await _matter_or_404(db, user.tenant_id, matter_id)
    rows = (
        (
            await db.execute(
                select(MatterWorkflowRun)
                .where(
                    MatterWorkflowRun.tenant_id == user.tenant_id,
                    MatterWorkflowRun.matter_id == matter_id,
                )
                .order_by(MatterWorkflowRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [await run_response(db, run) for run in rows]}


@router.post("/api/matters/{matter_id}/workflow-runs/{run_id}/rollback")
async def rollback_matter_workflow(
    matter_id: uuid.UUID,
    run_id: uuid.UUID,
    body: WorkflowRollbackRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("approve_legal_work")),
):
    await set_tenant_context(db, str(user.tenant_id))
    run = await _run_or_404(
        db,
        tenant_id=user.tenant_id,
        matter_id=matter_id,
        run_id=run_id,
        lock=True,
    )
    matter = await _matter_or_404(db, user.tenant_id, matter_id, lock=True)
    request_sha256 = digest_payload({"run_id": str(run_id), "reason": body.reason})
    run, blockers = await rollback_run(
        db,
        run=run,
        matter=matter,
        actor_user_id=user.id,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        reason=body.reason,
    )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(run)
    response = await run_response(db, run)
    if blockers:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Rollback requires manual compensation review",
                "blockers": blockers,
                "run": response,
            },
        )
    return response
