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
    StudioRenderCapabilities,
    StudioRenderCapability,
    StudioRenderJobStatus,
    StudioRenderOptions,
    StudioRenderSourceContract,
    StudioRendererComponent,
    StudioRendererManifest,
    canonical_effective_render_request_hash,
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
        self.manifest = None

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

    async def status(self, job_id):
        now = datetime.now(timezone.utc)
        options = StudioRenderOptions(page_number=2, max_pages=3)
        request_sha256 = "d" * 64
        return StudioRenderJobStatus(
            job_id=job_id,
            status_url=f"/api/template-studio/render-jobs/{job_id}",
            kind="studio_page_preview",
            state="pending",
            progress=0,
            attempts=0,
            max_attempts=3,
            created_at=now,
            updated_at=now,
            draft_id=uuid.uuid4(),
            rendered_revision=3,
            identity_sha256="b" * 64,
            snapshot_id=uuid.uuid4(),
            snapshot_content_sha256="c" * 64,
            source=StudioRenderSourceContract(
                artifact_id=uuid.uuid4(),
                sha256="a" * 64,
                media_type="text/markdown",
                format="markdown",
            ),
            render_options=options,
            render_options_sha256=options.sha256,
            request_sha256=request_sha256,
            effective_request_sha256=canonical_effective_render_request_hash(
                request_sha256=request_sha256,
                input_binding_sha256=None,
                input_binding_version=None,
            ),
            renderer_manifest=self.manifest,
            runtime_manifest_sha256=self.manifest.sha256,
            job_expires_at=now + timedelta(hours=1),
        )

    async def request_cancel(self, job_id, *, audit):
        await audit("studio_render_cancel_requested", job_id)
        return await self.status(job_id)

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


class _RouteObjectStore:
    def __init__(self, *, fresh=True):
        self.fresh = fresh

    def worker_heartbeat_fresh(self, *, max_age_seconds):
        assert 20 <= max_age_seconds <= 600
        return self.fresh


def _context(service, actor_id, *, worker_fresh=True):
    def component(name, value):
        return StudioRendererComponent(
            name=name, version="1.0.0", content_sha256=value * 64
        )

    manifest = StudioRendererManifest(
        isolation_policy_id="studio-test-v1",
        launcher_sha256="1" * 64,
        sandbox_policy_sha256="2" * 64,
        fixed_arguments_sha256="3" * 64,
        environment_sha256="4" * 64,
        runtime_bundle_sha256="5" * 64,
        font_pack_sha256="6" * 64,
        renderer=component("renderer", "7"),
        rasterizer=component("rasterizer", "8"),
        converter=component("converter", "9"),
        validator=component("validator", "a"),
    )
    service.manifest = manifest
    return StudioRenderRouteContext(
        service=service,
        actor_user_id=actor_id,
        audit=_audit,
        transaction=_transaction,
        object_store=_RouteObjectStore(fresh=worker_fresh),
        backend_url="https://configured.example",
        capabilities=StudioRenderCapabilities(
            capabilities=[
                StudioRenderCapability(
                    kind="studio_page_preview",
                    source_format="markdown",
                    output_media_type="image/png",
                    renderer_manifest=manifest,
                )
            ]
        ),
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
        "render_options": StudioRenderOptions(page_number=2, max_pages=3).model_dump(
            mode="json"
        ),
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
    assert content.headers["content-location"].startswith("https://configured.example/")


@pytest.mark.asyncio
async def test_capability_route_exposes_server_owned_dispatch_combinations():
    context = _context(_FakeService(), uuid.uuid4())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_studio_render_route_context] = lambda: context
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test.invalid"
    ) as client:
        response = await client.get("/api/template-studio/render-capabilities")
    assert response.status_code == 200
    assert response.json()["capabilities"][0]["source_format"] == "markdown"
    assert response.json()["capabilities"][0]["output_media_type"] == "image/png"


@pytest.mark.asyncio
async def test_stale_worker_blocks_admission_but_not_existing_resources():
    service = _FakeService()
    context = _context(service, uuid.uuid4(), worker_fresh=False)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_studio_render_route_context] = lambda: context
    job_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test.invalid"
    ) as client:
        capabilities = await client.get("/api/template-studio/render-capabilities")
        enqueue = await client.post(
            "/api/template-studio/render-jobs",
            headers={"Idempotency-Key": "intent-key-123"},
            json=_intent(),
        )
        status = await client.get(f"/api/template-studio/render-jobs/{job_id}")
        cancel = await client.post(f"/api/template-studio/render-jobs/{job_id}/cancel")
        content = await client.get(
            f"/api/template-studio/render-artifacts/{artifact_id}/content"
        )
    assert capabilities.status_code == 503
    assert enqueue.status_code == 503
    assert capabilities.json()["detail"]["code"] == "processor_unavailable"
    assert status.status_code == 200
    assert cancel.status_code == 200
    assert content.status_code == 200


@pytest.mark.asyncio
async def test_route_transaction_propagates_task_cancellation():
    context = _context(_FakeService(), uuid.uuid4())

    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _run(context, cancelled)


@pytest.mark.asyncio
async def test_unavailable_route_authenticates_before_runtime_disclosure():
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
    assert response.status_code == 401
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
            capabilities=_context(_FakeService(), uuid.uuid4()).capabilities,
        )
