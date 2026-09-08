"""Firm values are explicit, tenant scoped, and independent of matter data."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.routers import document_templates
from app.services import template_firm_fields as firm
from app.services.template_bindings import catalogue, is_valid_binding


@pytest.mark.asyncio
async def test_profile_is_tenant_scoped_and_uses_existing_branding_fallbacks():
    tenant_id = uuid.uuid4()
    tenant = SimpleNamespace(id=tenant_id, name="Example Firm", address="100 Main St")
    settings = SimpleNamespace(
        firm_name=None, firm_address=None, firm_phone=" 555-0100 ",
        firm_email="office@example.com", firm_website="https://example.com",
        firm_logo_url=None, firm_pdf_footer=None,
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=lambda: tenant),
        MagicMock(scalar_one_or_none=lambda: settings),
    ]))
    bindings = {f"custom_name_{i}": path for i, path in enumerate(firm.FIRM_FIELDS)}
    result = await firm.suggestions(db, tenant_id, bindings)
    assert [item.suggested_value for item in result.values()] == [
        "Example Firm", "100 Main St", "555-0100", "office@example.com", "https://example.com",
    ]
    for name, item in result.items():
        assert item.variable == name
        assert item.source_type == "firm_profile"
        assert item.provenance["binding"] == bindings[name]
        assert item.provenance["record_id"] == str(tenant_id)
        assert item.provenance["status"] == "configured"
        assert item.confidence == 1 and item.review_required is False
    for call in db.execute.await_args_list:
        compiled = call.args[0].compile(dialect=postgresql.dialect())
        assert tenant_id in compiled.params.values()
        assert "WHERE" in str(compiled)


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [False, True])
async def test_missing_profile_values_stay_missing(monkeypatch, exists):
    tenant_id = uuid.uuid4()
    tenant = SimpleNamespace(id=tenant_id) if exists else None
    db = SimpleNamespace(execute=AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: tenant)))
    branding = AsyncMock(return_value={"firm_email": "   "})
    monkeypatch.setattr(firm, "get_firm_branding", branding)
    result = await firm.suggestions(db, tenant_id, {"email": "firm.email", "phone": "firm.phone"})
    for item in result.values():
        assert item.suggested_value is None
        assert item.provenance["status"] == "firm_profile_missing"
        assert item.review_required and item.confidence == 0
    assert branding.await_count == int(exists)


@pytest.mark.asyncio
async def test_unbound_manual_and_unknown_fields_do_not_read_profile():
    db = SimpleNamespace(execute=AsyncMock())
    assert await firm.suggestions(db, uuid.uuid4(), {
        "firm_name": "manual", "office": "firm.unknown", "client": "client.name",
    }) == {}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_smart_fill_without_matter_refreshes_profile_and_ignores_sample_names(monkeypatch):
    tenant_id = uuid.uuid4()
    tenant = SimpleNamespace(id=tenant_id)
    db = SimpleNamespace(execute=AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: tenant)))
    profile = {"firm_name": "Original Firm"}
    monkeypatch.setattr(firm, "get_firm_branding", AsyncMock(side_effect=lambda *_: dict(profile)))
    template = SimpleNamespace(body="", variable_schema={"fields": [
        {"name": "letterhead", "binding": "firm.name"},
        {"name": "firm_name", "binding": "manual"},
        {"name": "firm_email"},
    ]})
    async def resolve():
        matter_id, values = await document_templates.build_variable_suggestions(
            template=template, requested_variables=None, matter_id=None,
            tenant_id=tenant_id, current_user=SimpleNamespace(), db=db,
        )
        assert matter_id is None
        return {item.variable: item for item in values}
    first = await resolve()
    profile["firm_name"] = "Updated Firm"
    second = await resolve()
    assert first["letterhead"].suggested_value == "Original Firm"
    assert second["letterhead"].suggested_value == "Updated Firm"
    assert second["firm_name"].suggested_value is None
    assert second["firm_email"].suggested_value is None


def test_firm_fields_are_selectable_and_do_not_include_arbitrary_paths():
    entries = {entry.path: entry for entry in catalogue()}
    for path in firm.FIRM_FIELDS:
        assert is_valid_binding(path)
        assert entries[path].group == "Firm profile"
    assert not is_valid_binding("firm.secret_key")
