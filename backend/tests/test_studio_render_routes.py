"""Database-free route tests for the unregistered Studio render surface."""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.studio_render import (
    StudioRenderRouteContext,
    _run,
    get_studio_render_route_context,
    router,
)
from app.schemas.studio_render import (
    StudioArtifactGeometry,
    StudioGeometryManifest,
    StudioPageGeometry,
    StudioRenderAccepted,
    StudioRenderOptions,
    StudioRenderPublicErrorEnvelope,
    StudioRenderSourceContract,
)
from app.services.studio_render_jobs import StudioRenderArtifactContent


class _FakeService:
    def __init__(self):
        self.enqueued = None
        self.idempotency_key = None
        self.geometry = StudioGeometryManifest(
            artifact_page_count=1,
            document_page_count=3,
            pages=[
                StudioPageGeometry(
                    page_number=2,
                    coordinate_space="pixels",
                    width_px=20,
                    height_px=30,
                    dpi_x=150,
                    dpi_y=150,
                )
            ],
        )

    async def enqueue(self, request, *, idempotency_key, audit):
        self.enqueued = request
        self.idempotency_key = idempotency_key
        await audit("studio_render_enqueued", uuid.uuid4())
        job_id = uuid.uuid4()
        return StudioRenderAccepted(
            job_id=job_id,
            status_url=f"/api/template-studio/render-jobs/{job_id}",
            job_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def artifact_geometry(self, artifact_id):
        return StudioArtifactGeometry(
            artifact_id=artifact_id,
            geometry_manifest=self.geometry,
            geometry_manifest_sha256=self.geometry.sha256,
        )

    async def artifact_content(self, artifact_id, *, object_store, max_bytes):
        content = b"verified-output"
        return StudioRenderArtifactContent(
            artifact_id=artifact_id,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="application/pdf",
        )


async def _transaction(operation):
    return await operation()


async def _audit(_event, _job_id):
    return None


def _context(service, actor_id):
    return StudioRenderRouteContext(
        service=service,
        actor_user_id=actor_id,
        audit=_audit,
        transaction=_transaction,
        object_store=object(),
        backend_url="https://configured.example",
    )


def _intent():
    return {
        "kind": "studio_page_preview",
        "draft_id": str(uuid.uuid4()),
        "expected_revision": 3,
        "identity_sha256": "b" * 64,
        "snapshot_id": str(uuid.uuid4()),
        "content_sha256": "c" * 64,
        "source": StudioRenderSourceContract(
            artifact_id=uuid.uuid4(),
            sha256="a" * 64,
            media_type="text/markdown",
            format="markdown",
        ).model_dump(mode="json"),
        "render_options": StudioRenderOptions(
            page_number=2, max_pages=3
        ).model_dump(mode="json"),
    }


@pytest.mark.asyncio
async def test_enqueue_binds_actor_hash_and_uses_only_configured_backend_origin():
    service = _FakeService()
    actor_id = uuid.uuid4()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_studio_render_route_context] = lambda: _context(
        service, actor_id
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://attacker.invalid",
        headers={"host": "attacker.invalid"},
    ) as client:
        response = await client.post(
            "/api/template-studio/render-jobs",
            headers={"Idempotency-Key": "intent-key-123"},
            json=_intent(),
        )
    assert response.status_code == 202
    assert response.headers["location"].startswith(
        "https://configured.example/api/template-studio/render-jobs/"
    )
    assert "attacker.invalid" not in response.headers["location"]
    assert service.enqueued.requested_by == actor_id
    assert service.enqueued.request_sha256
    assert service.idempotency_key == "intent-key-123"


@pytest.mark.asyncio
async def test_routes_reject_client_actor_and_serve_hash_verified_geometry_content():
    service = _FakeService()
    actor_id = uuid.uuid4()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_studio_render_route_context] = lambda: _context(
        service, actor_id
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test.invalid"
    ) as client:
        forged = await client.post(
            "/api/template-studio/render-jobs",
            headers={"Idempotency-Key": "intent-key-123"},
            json={**_intent(), "requested_by": str(uuid.uuid4())},
        )
        artifact_id = uuid.uuid4()
        geometry = await client.get(
            f"/api/template-studio/render-artifacts/{artifact_id}/geometry"
        )
        content = await client.get(
            f"/api/template-studio/render-artifacts/{artifact_id}/content"
        )
    assert forged.status_code == 422
    assert geometry.status_code == 200
    assert geometry.json()["geometry_manifest_sha256"] == service.geometry.sha256
    assert content.content == b"verified-output"
    assert content.headers["etag"].startswith('"sha256:')
    assert content.headers["content-location"].startswith(
        "https://configured.example/"
    )


@pytest.mark.asyncio
async def test_route_transaction_propagates_task_cancellation():
    context = _context(_FakeService(), uuid.uuid4())

    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _run(context, cancelled)


@pytest.mark.asyncio
async def test_unavailable_route_uses_its_declared_public_error_envelope():
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test.invalid"
    ) as client:
        response = await client.post(
            "/api/template-studio/render-jobs",
            headers={"Idempotency-Key": "intent-key-123"},
            json=_intent(),
        )
    assert response.status_code == 503
    error = StudioRenderPublicErrorEnvelope.model_validate(response.json())
    assert error.detail.code == "processor_unavailable"
    response_schema = app.openapi()["paths"]["/api/template-studio/render-jobs"][
        "post"
    ]["responses"]["503"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/StudioRenderPublicErrorEnvelope")


def test_route_context_rejects_request_paths_and_non_https_origins():
    with pytest.raises(ValueError, match="HTTPS BACKEND_URL"):
        StudioRenderRouteContext(
            service=_FakeService(),
            actor_user_id=uuid.uuid4(),
            audit=_audit,
            transaction=_transaction,
            object_store=object(),
            backend_url="http://configured.example",
        )
