"""Unregistered, dependency-injected HTTP surface for Studio render jobs."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Protocol, TypeVar
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response as FastAPIResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.schemas.studio_render import (
    StudioArtifactGeometry,
    StudioRenderAccepted,
    StudioRenderCapabilities,
    StudioRenderIntent,
    StudioRenderJobStatus,
    StudioRenderPublicError,
    StudioRenderPublicErrorEnvelope,
)
from app.services.studio_object_storage import StudioObjectStore
from app.services.studio_render_jobs import (
    StudioConsumerAudit,
    StudioRenderArtifactContent,
    StudioRenderJobService,
    StudioRenderServiceError,
    append_studio_render_audit_event,
    run_studio_consumer_transaction,
    studio_render_public_error,
)
from app.services.access_control import require_capability


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
    capabilities: StudioRenderCapabilities
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


_require_studio_user = require_capability("manage_documents")


async def get_studio_render_route_context(
    request: Request,
    current_user=Depends(_require_studio_user),
    db: AsyncSession = Depends(get_db),
) -> StudioRenderRouteContext:
    """Authenticate first, then expose only a fully initialized render runtime."""

    settings = get_settings()
    object_store = getattr(request.app.state, "studio_render_object_store", None)
    manifests = getattr(request.app.state, "studio_render_manifests", None)
    capabilities = getattr(request.app.state, "studio_render_capabilities", None)
    if (
        not settings.TEMPLATE_STUDIO_RENDER_ENABLED
        or object_store is None
        or not isinstance(manifests, dict)
        or not isinstance(capabilities, StudioRenderCapabilities)
    ):
        raise _public_http_error(RuntimeError("Studio rendering is unavailable."))
    try:
        service = StudioRenderJobService(
            db,
            tenant_id=current_user.tenant_id,
            actor_user_id=current_user.id,
            active_job_limit=settings.TEMPLATE_STUDIO_RENDER_ACTIVE_JOB_LIMIT,
            job_ttl=timedelta(
                seconds=settings.TEMPLATE_STUDIO_RENDER_JOB_TTL_SECONDS
            ),
            renderer_manifests=manifests,
            enqueue_rate_limit=settings.TEMPLATE_STUDIO_RENDER_ENQUEUE_RATE_LIMIT,
            enqueue_rate_window=timedelta(
                seconds=settings.TEMPLATE_STUDIO_RENDER_ENQUEUE_RATE_WINDOW_SECONDS
            ),
            queued_byte_limit=settings.TEMPLATE_STUDIO_RENDER_QUEUED_BYTE_LIMIT,
            max_input_binding_bytes=(
                settings.TEMPLATE_STUDIO_RENDER_MAX_INPUT_BINDING_BYTES
            ),
            retained_artifact_limit=(
                settings.TEMPLATE_STUDIO_RENDER_RETAINED_ARTIFACT_LIMIT
            ),
            retained_byte_limit=settings.TEMPLATE_STUDIO_RENDER_RETAINED_BYTE_LIMIT,
            live_artifact_limit=settings.TEMPLATE_STUDIO_RENDER_LIVE_ARTIFACT_LIMIT,
            live_byte_limit=settings.TEMPLATE_STUDIO_RENDER_LIVE_BYTE_LIMIT,
        )
        return StudioRenderRouteContext(
            service=service,
            actor_user_id=current_user.id,
            audit=partial(
                append_studio_render_audit_event,
                db,
                tenant_id=current_user.tenant_id,
                actor_user_id=current_user.id,
            ),
            transaction=partial(run_studio_consumer_transaction, db),
            object_store=object_store,
            backend_url=settings.BACKEND_URL,
            capabilities=capabilities,
            max_download_bytes=settings.TEMPLATE_STUDIO_RENDER_MAX_DOWNLOAD_BYTES,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _public_http_error(exc) from exc


async def require_fresh_studio_render_worker(
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
) -> StudioRenderRouteContext:
    """Gate only admissions that require a worker to accept new work."""

    settings = get_settings()
    if not context.object_store.worker_heartbeat_fresh(
        max_age_seconds=settings.TEMPLATE_STUDIO_RENDER_HEALTH_MAX_AGE_SECONDS
    ):
        raise _public_http_error(RuntimeError("Studio rendering is unavailable."))
    return context


class StudioRenderAPIRoute(APIRoute):
    """Normalize route, dependency, and validation failures into one envelope."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def normalized(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                public = StudioRenderServiceError(
                    422, "invalid_request", "The Studio request is invalid."
                ).to_public_error()
                return JSONResponse(
                    status_code=422,
                    content={"detail": public.model_dump(mode="json")},
                )
            except HTTPException as exc:
                try:
                    public = StudioRenderPublicError.model_validate(exc.detail)
                    status_code = exc.status_code
                except Exception:
                    code = {
                        401: "authentication_required",
                        403: "access_denied",
                        422: "invalid_request",
                    }.get(exc.status_code, "processor_unavailable")
                    error = StudioRenderServiceError(exc.status_code, code, "")
                    public = error.to_public_error()
                    status_code = error.status_code
                return JSONResponse(
                    status_code=status_code,
                    content={"detail": public.model_dump(mode="json")},
                    headers=exc.headers,
                )

        return normalized


router = APIRouter(
    prefix="/api/template-studio",
    tags=["template-studio"],
    route_class=StudioRenderAPIRoute,
)

_OPERATIONAL_ERRORS = {
    status: {"model": StudioRenderPublicErrorEnvelope}
    for status in (401, 403, 404, 409, 410, 413, 422, 429, 503, 504)
}


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


@router.get(
    "/render-capabilities",
    response_model=StudioRenderCapabilities,
    responses=_OPERATIONAL_ERRORS,
)
async def read_studio_render_capabilities(
    context: StudioRenderRouteContext = Depends(require_fresh_studio_render_worker),
):
    return context.capabilities


@router.post(
    "/render-jobs",
    response_model=StudioRenderAccepted,
    status_code=202,
    responses=_OPERATIONAL_ERRORS,
)
async def enqueue_studio_render(
    body: StudioRenderIntent,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=200),
    context: StudioRenderRouteContext = Depends(require_fresh_studio_render_worker),
):
    if body.input_binding_id is not None:
        raise _public_http_error(
            StudioRenderServiceError(
                422,
                "invalid_request",
                "The Studio request is invalid.",
            )
        )
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


@router.get(
    "/render-jobs/{job_id}",
    response_model=StudioRenderJobStatus,
    responses=_OPERATIONAL_ERRORS,
)
async def read_studio_render_job(
    job_id: uuid.UUID,
    response: Response,
    context: StudioRenderRouteContext = Depends(get_studio_render_route_context),
):
    status = await _run(context, lambda: context.service.status(job_id))
    _resource_headers(response, context, status.status_url)
    return status


@router.post(
    "/render-jobs/{job_id}/cancel",
    response_model=StudioRenderJobStatus,
    responses=_OPERATIONAL_ERRORS,
)
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


@router.get(
    "/render-artifacts/{artifact_id}",
    response_model=StudioRenderJobStatus,
    responses=_OPERATIONAL_ERRORS,
)
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
    responses=_OPERATIONAL_ERRORS,
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


@router.get(
    "/render-artifacts/{artifact_id}/content",
    responses=_OPERATIONAL_ERRORS,
)
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
    "require_fresh_studio_render_worker",
    "router",
]
