"""Unregistered, dependency-injected HTTP surface for Studio render jobs."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import Response as FastAPIResponse

from app.schemas.studio_render import (
    StudioArtifactGeometry,
    StudioRenderAccepted,
    StudioRenderIntent,
    StudioRenderJobStatus,
    StudioRenderPublicErrorEnvelope,
)
from app.services.studio_object_storage import StudioObjectStore
from app.services.studio_render_jobs import (
    StudioConsumerAudit,
    StudioRenderArtifactContent,
    studio_render_public_error,
)


_Result = TypeVar("_Result")
StudioTransaction = Callable[
    [Callable[[], Awaitable[_Result]]], Awaitable[_Result]
]


class StudioRenderRouteService(Protocol):
    async def enqueue(self, request, *, idempotency_key, audit): ...
    async def status(self, job_id: uuid.UUID) -> StudioRenderJobStatus: ...
    async def artifact_result(self, artifact_id: uuid.UUID) -> StudioRenderJobStatus: ...
    async def artifact_geometry(self, artifact_id: uuid.UUID) -> StudioArtifactGeometry: ...
    async def artifact_content(
        self,
        artifact_id: uuid.UUID,
        *,
        object_store: StudioObjectStore,
        max_bytes: int,
    ) -> StudioRenderArtifactContent: ...
    async def request_cancel(self, job_id: uuid.UUID, *, audit): ...


@dataclass(frozen=True)
class StudioRenderRouteContext:
    """Authorized request dependencies supplied by gated shared integration."""

    service: StudioRenderRouteService
    actor_user_id: uuid.UUID
    audit: StudioConsumerAudit
    transaction: StudioTransaction
    object_store: StudioObjectStore
    backend_url: str
    max_download_bytes: int = 100 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_user_id", uuid.UUID(str(self.actor_user_id)))
        parsed = urlsplit(self.backend_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Studio routes require a configured HTTPS BACKEND_URL origin")
        if not 1 <= self.max_download_bytes <= 100 * 1024 * 1024:
            raise ValueError("Studio route download bound is invalid")

    def absolute_url(self, resource: str) -> str:
        if not resource.startswith("/api/") or ".." in resource or "//" in resource:
            raise ValueError("Studio resource path is invalid")
        return f"{self.backend_url.rstrip('/')}{resource}"


async def get_studio_render_route_context() -> StudioRenderRouteContext:
    """Fail closed until shared registration supplies auth/config/storage wiring."""

    public = studio_render_public_error(RuntimeError("Studio rendering is unavailable."))
    raise HTTPException(
        status_code=503,
        detail=public.model_dump(mode="json", exclude_none=True),
    )


router = APIRouter(prefix="/api/template-studio", tags=["template-studio"])


def _public_http_error(error: BaseException) -> HTTPException:
    public = studio_render_public_error(error)
    return HTTPException(
        status_code=(getattr(error, "status_code", 503)),
        detail=public.model_dump(mode="json", exclude_none=True),
    )


async def _run(context: StudioRenderRouteContext, operation):
    try:
        return await context.transaction(operation)
    except HTTPException:
        raise
    except Exception as exc:
        raise _public_http_error(exc) from exc


def _resource_headers(
    response: Response,
    context: StudioRenderRouteContext,
    resource: str,
) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Location"] = context.absolute_url(resource)


@router.post(
    "/render-jobs",
    response_model=StudioRenderAccepted,
    status_code=202,
    responses={503: {"model": StudioRenderPublicErrorEnvelope}},
)
async def enqueue_studio_render(
    body: StudioRenderIntent,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=200),
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
):
    request = body.bind_actor(context.actor_user_id)
    accepted = await _run(
        context,
        lambda: context.service.enqueue(
            request,
            idempotency_key=idempotency_key,
            audit=context.audit,
        ),
    )
    _resource_headers(response, context, accepted.status_url)
    response.headers["Location"] = context.absolute_url(accepted.status_url)
    return accepted


@router.get("/render-jobs/{job_id}", response_model=StudioRenderJobStatus)
async def read_studio_render_job(
    job_id: uuid.UUID,
    response: Response,
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
):
    status = await _run(context, lambda: context.service.status(job_id))
    _resource_headers(response, context, status.status_url)
    return status


@router.post("/render-jobs/{job_id}/cancel", response_model=StudioRenderJobStatus)
async def cancel_studio_render_job(
    job_id: uuid.UUID,
    response: Response,
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
):
    status = await _run(
        context,
        lambda: context.service.request_cancel(job_id, audit=context.audit),
    )
    _resource_headers(response, context, status.status_url)
    return status


@router.get("/render-artifacts/{artifact_id}", response_model=StudioRenderJobStatus)
async def read_studio_render_artifact(
    artifact_id: uuid.UUID,
    response: Response,
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
):
    status = await _run(
        context, lambda: context.service.artifact_result(artifact_id)
    )
    if status.result_url is None:
        raise _public_http_error(RuntimeError("missing artifact resource"))
    _resource_headers(response, context, status.result_url)
    return status


@router.get(
    "/render-artifacts/{artifact_id}/geometry",
    response_model=StudioArtifactGeometry,
)
async def read_studio_render_geometry(
    artifact_id: uuid.UUID,
    response: Response,
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
):
    geometry = await _run(
        context, lambda: context.service.artifact_geometry(artifact_id)
    )
    _resource_headers(
        response,
        context,
        f"/api/template-studio/render-artifacts/{artifact_id}/geometry",
    )
    return geometry


@router.get("/render-artifacts/{artifact_id}/content")
async def download_studio_render_artifact(
    artifact_id: uuid.UUID,
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
) -> FastAPIResponse:
    result = await _run(
        context,
        lambda: context.service.artifact_content(
            artifact_id,
            object_store=context.object_store,
            max_bytes=context.max_download_bytes,
        ),
    )
    resource = f"/api/template-studio/render-artifacts/{artifact_id}/content"
    return FastAPIResponse(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Location": context.absolute_url(resource),
            "ETag": f'"sha256:{result.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = [
    "StudioRenderRouteContext",
    "get_studio_render_route_context",
    "router",
]
