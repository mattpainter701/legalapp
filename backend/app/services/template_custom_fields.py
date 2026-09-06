"""Tenant-scoped, non-sensitive typed field sources for Template Studio."""

from sqlalchemy import select
from app.models.plugin import MatterEvent
from app.models.configurable_workflow import (
    CustomFieldDefinition,
    MatterCustomFieldValue,
    ContactCustomFieldValue,
)
from app.schemas.document_template import DocumentTemplateVariableSuggestion
from app.services.template_bindings import custom_binding


async def definitions(db, tenant_id):
    return list(
        (
            await db.scalars(
                select(CustomFieldDefinition)
                .where(
                    CustomFieldDefinition.tenant_id == tenant_id,
                    CustomFieldDefinition.active.is_(True),
                    CustomFieldDefinition.sensitive.is_(False),
                    CustomFieldDefinition.field_type.in_(
                        [
                            "text",
                            "long_text",
                            "number",
                            "date",
                            "boolean",
                            "single_select",
                        ]
                    ),
                )
                .order_by(CustomFieldDefinition.label)
            )
        ).all()
    )


def display_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def suggestions(db, tenant_id, matter, bindings):
    requested = {
        name: custom_binding(path)
        for name, path in bindings.items()
        if custom_binding(path)
    }
    if not requested:
        return {}
    available = {
        (field.entity_type, str(field.id)): field
        for field in await definitions(db, tenant_id)
    }
    output = {}
    for name, identity in requested.items():
        field = available.get(identity)
        value = None
        entity_id = (
            getattr(matter, "id", None)
            if identity[0] == "matter"
            else getattr(matter, "client_contact_id", None)
        )
        if field and entity_id:
            model = (
                MatterCustomFieldValue
                if identity[0] == "matter"
                else ContactCustomFieldValue
            )
            column = model.matter_id if identity[0] == "matter" else model.contact_id
            value = await db.scalar(
                select(model).where(
                    model.tenant_id == tenant_id,
                    column == entity_id,
                    model.field_definition_id == field.id,
                )
            )
        provenance = {
            "status": "from_custom_record" if value else "binding_unresolved",
            "binding": bindings[name],
            "binding_label": field.label if field else "Unavailable data source",
        }
        if value:
            provenance.update(
                field_definition_id=str(field.id),
                schema_version=field.schema_version,
                record_id=str(value.id),
                updated_at=value.updated_at.isoformat(),
                updated_by_user_id=str(value.updated_by_user_id),
            )
        if value and identity[0] == "matter":
            reviewed = await db.scalar(
                select(MatterEvent)
                .where(
                    MatterEvent.tenant_id == tenant_id,
                    MatterEvent.matter_id == entity_id,
                    MatterEvent.event_type == "template_fact_reviewed",
                    MatterEvent.metadata_json["field"].as_string() == str(field.id),
                    MatterEvent.metadata_json["accepted_value_hmac"].as_string()
                    == value.value_hmac,
                    MatterEvent.metadata_json["reviewed_at"].as_string()
                    == value.updated_at.isoformat(),
                )
                .order_by(MatterEvent.created_at.desc())
                .limit(1)
            )
            if reviewed:
                evidence = reviewed.metadata_json
                provenance.update(
                    status="reviewed_from_document",
                    source_document_id=evidence["document"],
                    source_sha256=evidence["source_sha256"],
                    reviewed_by_user_id=str(reviewed.created_by),
                    reviewed_at=evidence["reviewed_at"],
                )
        output[name] = DocumentTemplateVariableSuggestion(
            variable=name,
            suggested_value=display_value(value.value_json) if value else None,
            source_type="custom_record" if value else None,
            source_field=bindings[name],
            provenance=provenance,
            review_required=True,
        )
    return output
