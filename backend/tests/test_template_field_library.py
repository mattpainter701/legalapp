"""Shared definitions and usage, without exposing source text or record values."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.models.document_template import DocumentTemplate
from app.routers import document_templates as router
from app.schemas.document_template import (
    DocumentTemplateBindingCatalogue,
    DocumentTemplateBindingOption,
    DocumentTemplateFieldUsage,
)
from app.services import template_field_library as library


def catalog():
    return DocumentTemplateBindingCatalogue(
        bindings=[
            DocumentTemplateBindingOption(
                path="client.name", label="Client name", group="Client"
            ),
            DocumentTemplateBindingOption(
                path="matter.court", label="Court", group="Matter"
            ),
        ],
        collections=[],
        operators=[],
    )


@pytest.mark.asyncio
async def test_catalog_counts_only_available_definitions(monkeypatch):
    user, db = SimpleNamespace(tenant_id=uuid.uuid4()), AsyncMock()
    bindings = AsyncMock(return_value=catalog())
    counts = AsyncMock(return_value={"client.name": 32, "custom.matter.unavailable": 4})
    monkeypatch.setattr(router, "list_template_bindings", bindings)
    monkeypatch.setattr(library, "usage_counts", counts)
    result = await router.template_field_library_catalogue(db=db, current_user=user)
    assert [(field.path, field.template_count) for field in result.fields] == [
        ("client.name", 32),
        ("matter.court", 0),
    ]
    assert result.fields[0].suggested_name == "client_name"
    bindings.assert_awaited_once_with(db=db, current_user=user)
    counts.assert_awaited_once_with(db, user.tenant_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding", ["manual", "client.unknown", "custom.matter." + str(uuid.uuid4())]
)
async def test_usage_rejects_unavailable_and_other_firm_definitions(
    monkeypatch, binding
):
    monkeypatch.setattr(
        router, "list_template_bindings", AsyncMock(return_value=catalog())
    )
    query = AsyncMock()
    monkeypatch.setattr(library, "binding_usage", query)
    with pytest.raises(HTTPException) as error:
        await router.template_field_library_usage(
            binding, 20, 0, AsyncMock(), SimpleNamespace(tenant_id=uuid.uuid4())
        )
    assert error.value.status_code == 404
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_usage_routes_pagination_and_does_not_return_sample_values(monkeypatch):
    tenant_id, template_id = uuid.uuid4(), uuid.uuid4()
    row = {
        "template_id": template_id,
        "title": "Fee agreement",
        "status": "draft",
        "current_version_no": 3,
        "fields": [{"name": "client", "label": "Client"}],
        "body": "private",
        "source_text": "private",
    }
    payload = {
        "items": [row],
        "total": 25,
        "limit": 20,
        "offset": 20,
        "has_more": False,
    }
    query = AsyncMock(return_value=payload)
    monkeypatch.setattr(
        router, "list_template_bindings", AsyncMock(return_value=catalog())
    )
    monkeypatch.setattr(library, "binding_usage", query)
    db = AsyncMock()
    result = await router.template_field_library_usage(
        "client.name", 20, 20, db, SimpleNamespace(tenant_id=tenant_id)
    )
    query.assert_awaited_once_with(db, tenant_id, "client.name", 20, 20)
    response = DocumentTemplateFieldUsage.model_validate(result).model_dump_json()
    assert "private" not in response
    assert str(template_id) in response


@pytest.mark.asyncio
async def test_usage_query_is_bounded_and_sorted():
    tenant_id = uuid.uuid4()
    db = AsyncMock()
    db.scalar.return_value = 21
    db.execute.return_value = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = [
        {
            "template_id": uuid.uuid4(),
            "title": "Example",
            "status": "draft",
            "current_version_no": 1,
            "fields": [{"name": "z", "label": None}, {"name": "a", "label": "First"}],
        }
    ]
    result = await library.binding_usage(db, tenant_id, "client.name", 20, 0)
    assert result["has_more"]
    assert [field["name"] for field in result["items"][0]["fields"]] == ["a", "z"]
    statement = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert tenant_id in statement.params.values()
    assert "client.name" in statement.params.values()
    assert "LIMIT" in str(statement) and "OFFSET" in str(statement)
    assert "document_templates.body" not in str(statement)
    assert "jsonb_typeof" in str(statement)
    db.execute.return_value.all.return_value = [("client.name", 21)]
    assert await library.usage_counts(db, tenant_id) == {"client.name": 21}


@pytest.mark.asyncio
async def test_database_usage_is_firm_scoped_complete_and_excludes_false_mappings(
    db_session, test_tenant
):
    tenant_id = test_tenant.id
    for index in range(23):
        db_session.add(
            DocumentTemplate(
                tenant_id=tenant_id,
                title=f"Template {index:02}",
                body="Do not return this body",
                is_active=index % 2 == 0,
                status="draft",
                current_version_no=2,
                variable_schema={
                    "fields": [
                        {"name": "client", "label": "Client", "binding": "client.name"},
                        {"name": "signature_name", "binding": "client.name"},
                        {
                            "name": "excluded",
                            "binding": "matter.court",
                            "included": False,
                        },
                        {
                            "name": "linked",
                            "binding": "matter.court",
                            "value_from": "client",
                        },
                        {"name": "case_number"},
                    ]
                },
            )
        )
    for schema in [
        None,
        {},
        {"fields": None},
        {"fields": "invalid"},
        {"fields": [None, {}, "invalid"]},
    ]:
        db_session.add(
            DocumentTemplate(
                tenant_id=tenant_id, title="Old source", body="", variable_schema=schema
            )
        )
    # The model has no tenant FK; a synthetic foreign tenant tests the query boundary.
    db_session.add(
        DocumentTemplate(
            tenant_id=uuid.uuid4(),
            title="Other firm",
            body="",
            variable_schema={"fields": [{"name": "client", "binding": "client.name"}]},
        )
    )
    await db_session.flush()
    assert await library.usage_counts(db_session, tenant_id) == {"client.name": 23}
    first = await library.binding_usage(db_session, tenant_id, "client.name", 20, 0)
    second = await library.binding_usage(db_session, tenant_id, "client.name", 20, 20)
    assert first["total"] == second["total"] == 23
    assert len(first["items"]) == 20 and first["has_more"]
    assert len(second["items"]) == 3 and not second["has_more"]
    assert first["items"][0]["title"] == "Template 00"
    assert second["items"][-1]["title"] == "Template 22"
    assert len(first["items"][0]["fields"]) == 2
    assert "body" not in first["items"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [False, True])
async def test_field_library_requires_document_capability(monkeypatch, authenticated):
    from app.services import access_control, rbac_service

    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[router.get_db] = lambda: AsyncMock()
    if authenticated:
        monkeypatch.setattr(
            access_control,
            "get_current_user",
            AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        )
        monkeypatch.setattr(
            rbac_service, "get_user_capabilities", AsyncMock(return_value=set())
        )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in [
            "/api/templates/field-library",
            "/api/templates/field-library/usage?binding=client.name",
        ]:
            response = await client.get(path)
            assert response.status_code == (403 if authenticated else 401)
