from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.routers import template_intake_preview as preview
from app.services.docx_to_pdf import DocxToPdfError


@pytest.fixture
def preview_app(monkeypatch):
    app = FastAPI()
    app.include_router(preview.router)
    dependency = preview.router.routes[0].dependant.dependencies[0].call
    app.dependency_overrides[dependency] = lambda: SimpleNamespace(
        tenant_id="tenant-one"
    )
    monkeypatch.setattr(preview.settings, "DOCX_PDF_CONVERSION_ENABLED", True)
    renderer = AsyncMock(return_value=b"%PDF-1.4 synthetic")
    monkeypatch.setattr(preview.source_preview_cache, "render", renderer)
    return app, dependency, renderer


async def upload(app, content=b"word document", name="sample.docx"):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/templates/intake/preview-render",
            files={"file": (name, content, "application/octet-stream")},
        )


@pytest.mark.asyncio
async def test_unsaved_word_preview_is_private_and_tenant_scoped(preview_app):
    app, dependency, renderer = preview_app
    first = await upload(app)
    assert first.status_code == 200
    assert first.content == b"%PDF-1.4 synthetic"
    assert first.headers["content-type"] == "application/pdf"
    assert first.headers["cache-control"] == "private, no-store"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert renderer.await_args.kwargs["tenant_id"] == "tenant-one"
    assert renderer.await_args.args == (b"word document",)
    assert (
        renderer.await_args.kwargs["max_pages"]
        == preview.settings.DOCX_PDF_CONVERSION_MAX_PAGES
    )
    app.dependency_overrides[dependency] = lambda: SimpleNamespace(
        tenant_id="tenant-two"
    )
    await upload(app)
    assert renderer.await_args.kwargs["tenant_id"] == "tenant-two"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,name,status",
    [(b"", "empty.docx", 400), (b"text", "notes.txt", 422), (b"bad", "bad.exe", 400)],
)
async def test_invalid_upload_never_starts_converter(
    preview_app, content, name, status
):
    app, _, renderer = preview_app
    response = await upload(app, content, name)
    assert response.status_code == status
    renderer.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_converter_explains_failure(preview_app, monkeypatch):
    app, _, renderer = preview_app
    monkeypatch.setattr(preview.settings, "DOCX_PDF_CONVERSION_ENABLED", False)
    response = await upload(app)
    assert response.status_code == 503
    renderer.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversion_error_does_not_expose_internal_details(preview_app):
    app, _, renderer = preview_app
    renderer.side_effect = DocxToPdfError("private converter directory")
    response = await upload(app)
    assert response.status_code == 503
    assert "private converter" not in response.text
    assert "Fields" in response.json()["detail"]


@pytest.mark.asyncio
async def test_capability_denial_cannot_render(preview_app):
    app, dependency, renderer = preview_app

    def forbidden():
        raise HTTPException(status_code=403, detail="Not permitted")

    app.dependency_overrides[dependency] = forbidden
    response = await upload(app)
    assert response.status_code == 403
    renderer.assert_not_awaited()
