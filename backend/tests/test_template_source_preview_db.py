from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import HTTPException
import pytest

from app.models.document_template import DocumentTemplate
from app.routers import document_templates as routes


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_even_a_cached_source_preview(
    db_session, test_tenant, monkeypatch
):
    template = DocumentTemplate(
        tenant_id=test_tenant.id, title="Private source", body="", format="docx"
    )
    db_session.add(template)
    await db_session.flush()
    template_id = template.id
    source = AsyncMock(return_value=b"verified source")
    cache = SimpleNamespace(render=AsyncMock(return_value=b"cached pdf"))
    monkeypatch.setattr(routes, "_verified_template_source", source)
    monkeypatch.setattr(routes, "source_preview_cache", cache)
    monkeypatch.setattr(routes.settings, "DOCX_PDF_CONVERSION_ENABLED", True)
    own_user = SimpleNamespace(tenant_id=test_tenant.id)
    result = await routes.preview_template_source(template_id, own_user, db_session)
    assert result.body == b"cached pdf"
    other_user = SimpleNamespace(tenant_id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await routes.preview_template_source(template_id, other_user, db_session)
    assert exc.value.status_code == 404
    assert cache.render.await_count == 1
    assert source.await_count == 1
