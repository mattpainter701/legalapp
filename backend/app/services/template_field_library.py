"""Read-only, tenant-scoped usage of explicit shared field bindings."""

from sqlalchemy import case, cast, column, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONB

from app.models.document_template import DocumentTemplate


def mapped_fields(tenant_id):
    """Expand only valid arrays; never select document bodies or matter values."""
    fields = cast(DocumentTemplate.variable_schema, JSONB)["fields"]
    safe_fields = case(
        (func.jsonb_typeof(fields) == "array", fields),
        else_=literal([], type_=JSONB),
    )
    entries = (
        func.jsonb_array_elements(safe_fields)
        .table_valued(column("value", JSONB))
        .lateral("field")
    )
    value = entries.c.value
    return (
        select(
            DocumentTemplate.id.label("template_id"),
            DocumentTemplate.title,
            DocumentTemplate.status,
            DocumentTemplate.current_version_no,
            value["binding"].astext.label("binding"),
            value["name"].astext.label("name"),
            value["label"].astext.label("label"),
        )
        .select_from(DocumentTemplate)
        .join(entries, true())
        .where(
            DocumentTemplate.tenant_id == tenant_id,
            value["included"].astext.is_distinct_from("false"),
            func.coalesce(value["value_from"].astext, "") == "",
            func.coalesce(value["name"].astext, "") != "",
            func.coalesce(value["binding"].astext, "") != "",
        )
        .subquery()
    )


async def usage_counts(db, tenant_id):
    fields = mapped_fields(tenant_id)
    result = await db.execute(
        select(
            fields.c.binding, func.count(func.distinct(fields.c.template_id))
        ).group_by(fields.c.binding)
    )
    return dict(result.all())


async def binding_usage(db, tenant_id, binding, limit, offset):
    fields = mapped_fields(tenant_id)
    matching = fields.c.binding == binding
    total = await db.scalar(
        select(func.count(func.distinct(fields.c.template_id))).where(matching)
    )
    result = await db.execute(
        select(
            fields.c.template_id,
            fields.c.title,
            fields.c.status,
            fields.c.current_version_no,
            func.jsonb_agg(
                func.jsonb_build_object("name", fields.c.name, "label", fields.c.label)
            ).label("fields"),
        )
        .where(matching)
        .group_by(
            fields.c.template_id,
            fields.c.title,
            fields.c.status,
            fields.c.current_version_no,
        )
        .order_by(func.lower(fields.c.title), fields.c.template_id)
        .limit(limit)
        .offset(offset)
    )
    items = [dict(row) for row in result.mappings().all()]
    for item in items:
        item["fields"].sort(key=lambda field: (field["name"], field["label"] or ""))
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }
