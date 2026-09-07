import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.routers import document_templates as routes
from app.services import template_source_preview as previews
from app.services.docx_to_pdf import DocxToPdfError


@pytest.mark.asyncio
async def test_cache_scopes_source_tenant_template_and_converter_settings(monkeypatch):
    convert = AsyncMock(return_value=b"pdf")
    monkeypatch.setattr(previews, "docx_to_pdf_bytes", convert)
    cache = previews.SourcePreviewCache()
    options = dict(tenant_id="firm", template_id="template", max_pages=10)
    assert await cache.render(b"source", **options) == b"pdf"
    assert await cache.render(b"source", **options) == b"pdf"
    assert convert.await_count == 1
    for source, changed in [
        (b"changed", {}),
        (b"source", {"tenant_id": "other"}),
        (b"source", {"template_id": "other"}),
        (b"source", {"max_pages": 9}),
    ]:
        await cache.render(source, **(options | changed))
    assert convert.await_count == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes,max_entries", [(6, 10), (100, 2)])
async def test_cache_evicts_least_recently_used_and_bounds_total_size(
    monkeypatch, max_bytes, max_entries
):
    convert = AsyncMock(return_value=b"pdf")
    monkeypatch.setattr(previews, "docx_to_pdf_bytes", convert)
    cache = previews.SourcePreviewCache(max_bytes=max_bytes, max_entries=max_entries)

    async def render(source):
        return await cache.render(source, tenant_id="firm", template_id="template")

    await render(b"a")
    await render(b"b")
    await render(b"a")
    await render(b"c")
    await render(b"a")
    assert convert.await_count == 3
    await render(b"b")
    assert convert.await_count == 4
    assert cache._bytes <= max_bytes
    assert len(cache._entries) <= max_entries


@pytest.mark.asyncio
async def test_oversized_output_is_returned_without_retaining_it(monkeypatch):
    convert = AsyncMock(return_value=b"large")
    monkeypatch.setattr(previews, "docx_to_pdf_bytes", convert)
    cache = previews.SourcePreviewCache(max_bytes=2)
    for _ in range(2):
        assert await cache.render(b"source", tenant_id="a", template_id="b") == b"large"
    assert convert.await_count == 2
    assert cache._bytes == 0


@pytest.mark.asyncio
async def test_busy_misses_fail_promptly_and_cancellation_releases_slot(monkeypatch):
    started = asyncio.Event()

    async def convert(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(previews, "docx_to_pdf_bytes", convert)
    cache = previews.SourcePreviewCache()
    task = asyncio.create_task(cache.render(b"a", tenant_id="a", template_id="b"))
    await started.wait()
    with pytest.raises(DocxToPdfError, match="busy"):
        await cache.render(b"b", tenant_id="a", template_id="b")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(previews, "docx_to_pdf_bytes", AsyncMock(return_value=b"pdf"))
    assert await cache.render(b"a", tenant_id="a", template_id="b") == b"pdf"


@pytest.mark.asyncio
async def test_conversion_failure_is_not_cached_and_releases_slot(monkeypatch):
    convert = AsyncMock(side_effect=[DocxToPdfError("unavailable"), b"pdf"])
    monkeypatch.setattr(previews, "docx_to_pdf_bytes", convert)
    cache = previews.SourcePreviewCache()
    with pytest.raises(DocxToPdfError):
        await cache.render(b"a", tenant_id="a", template_id="b")
    assert await cache.render(b"a", tenant_id="a", template_id="b") == b"pdf"


@pytest.fixture
def preview_context(tmp_path, monkeypatch):
    tenant_id, template_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(routes.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes.settings, "DOCX_PDF_CONVERSION_ENABLED", True)
    directory = tmp_path / str(tenant_id) / "templates" / str(template_id)
    directory.mkdir(parents=True)
    source = directory / "original.docx"
    source.write_bytes(b"original source")
    template = SimpleNamespace(
        id=template_id,
        tenant_id=tenant_id,
        format="docx",
        source_storage_path=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=template))
    user = SimpleNamespace(tenant_id=tenant_id)
    context = AsyncMock()
    monkeypatch.setattr(routes, "set_tenant_context", context)
    monkeypatch.setattr(routes, "source_preview_cache", previews.SourcePreviewCache())
    convert = AsyncMock(return_value=b"%PDF-test")
    monkeypatch.setattr(previews, "docx_to_pdf_bytes", convert)
    return SimpleNamespace(
        template=template,
        db=db,
        user=user,
        source=source,
        convert=convert,
        context=context,
    )


@pytest.mark.asyncio
async def test_endpoint_cache_hit_rechecks_source_and_does_not_mutate_template(
    preview_context,
):
    ctx = preview_context
    before = vars(ctx.template).copy()
    for _ in range(2):
        response = await routes.preview_template_source(
            ctx.template.id, ctx.user, ctx.db
        )
        assert response.body == b"%PDF-test"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.media_type == "application/pdf"
    assert ctx.convert.await_count == 1
    assert vars(ctx.template) == before
    ctx.source.write_bytes(b"tampered")
    with pytest.raises(HTTPException) as exc:
        await routes.preview_template_source(ctx.template.id, ctx.user, ctx.db)
    assert exc.value.status_code == 409
    assert "integrity" in exc.value.detail
    assert ctx.convert.await_count == 1


@pytest.mark.asyncio
async def test_endpoint_scopes_query_and_missing_template_returns_404(preview_context):
    ctx = preview_context
    ctx.db.scalar.return_value = None
    with pytest.raises(HTTPException) as exc:
        await routes.preview_template_source(ctx.template.id, ctx.user, ctx.db)
    assert exc.value.status_code == 404
    statement = ctx.db.scalar.call_args.args[0].compile()
    assert ctx.template.id in statement.params.values()
    assert ctx.user.tenant_id in statement.params.values()
    assert "document_templates.tenant_id =" in str(statement)
    ctx.context.assert_awaited_once_with(ctx.db, str(ctx.user.tenant_id))
    ctx.convert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [("format", 422), ("missing", 409), ("disabled", 503), ("converter", 503)],
)
async def test_endpoint_failure_paths(preview_context, monkeypatch, failure, status):
    ctx = preview_context
    if failure == "format":
        ctx.template.format = "pdf"
    elif failure == "missing":
        ctx.source.unlink()
    elif failure == "disabled":
        monkeypatch.setattr(routes.settings, "DOCX_PDF_CONVERSION_ENABLED", False)
    else:
        ctx.convert.side_effect = DocxToPdfError(
            "The Word document could not be converted to PDF."
        )
    with pytest.raises(HTTPException) as exc:
        await routes.preview_template_source(ctx.template.id, ctx.user, ctx.db)
    assert exc.value.status_code == status
    assert str(ctx.source) not in exc.value.detail


@pytest.mark.asyncio
async def test_endpoint_enforces_actual_authentication_and_capability(monkeypatch):
    from app.database import get_db
    from app.services import access_control, rbac_service

    application = FastAPI()
    application.include_router(routes.router)

    # A minimal app exercises the route's real capability dependency, without
    # needing a live database or the full app's unrelated middleware.
    async def database():
        return AsyncMock()

    application.dependency_overrides[get_db] = database
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        url = f"/api/templates/{uuid.uuid4()}/preview-render"
        response = await client.get(url)
        assert response.status_code == 401
        monkeypatch.setattr(
            access_control,
            "get_current_user",
            AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        )
        monkeypatch.setattr(
            rbac_service, "get_user_capabilities", AsyncMock(return_value=set())
        )
        response = await client.get(url)
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing capability: manage_documents"
