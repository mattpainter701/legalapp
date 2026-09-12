"""Saving a card binding through the editor's own route.

The Studio editor saves a "Fills from" choice with ``PATCH /templates/{id}``.
These call that handler directly with a mocked async session, the way the
template-set and sample-template routes are covered, so the check is on the
route the picker actually uses rather than on a helper in isolation.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.document_template import DocumentTemplate
from app.routers import document_templates as router
from app.schemas.document_template import DocumentTemplateUpdate

pytestmark = pytest.mark.asyncio


TENANT = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4(), tenant_id=TENANT)


@pytest.fixture(autouse=True)
def _no_database_side_effects(monkeypatch):
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "record_version", AsyncMock())


def _template() -> DocumentTemplate:
    now = datetime.now(timezone.utc)
    return DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        title="Engagement letter",
        body="Dear {{client_name}},",
        category="engagement_letter",
        format="markdown",
        status="draft",
        variable_schema={"fields": [{"name": "client_name", "label": "Client"}]},
        is_active=False,
        current_version_no=1,
        created_at=now,
        updated_at=now,
    )


def _session(template: DocumentTemplate) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: template)
    )
    return db


async def _patch_binding(binding: str):
    template = _template()
    payload = DocumentTemplateUpdate(
        variable_schema={
            "fields": [{"name": "client_name", "label": "Client", "binding": binding}]
        }
    )
    response = await router.update_template(
        template.id, payload, current_user=USER, db=_session(template)
    )
    return response, template


class TestCardBindingsSave:
    @pytest.mark.parametrize(
        "binding",
        [
            "client.full_name",  # what the picker emits for the client card
            "defendant.full_name",
            "defendant.2.full_name",  # a role instance
            "preparer.prepared_by",
            "party.defendant.name",  # a pre-card path an older template carries
            "manual",
        ],
    )
    async def test_the_route_keeps_the_binding(self, binding):
        response, template = await _patch_binding(binding)
        assert response.variable_schema["fields"][0]["binding"] == binding
        assert template.variable_schema["fields"][0]["binding"] == binding

    async def test_an_unknown_path_is_still_refused_with_the_path_named(self):
        with pytest.raises(HTTPException) as caught:
            await _patch_binding("client.middle_initial")
        assert caught.value.status_code == 422
        assert "client.middle_initial" in caught.value.detail
