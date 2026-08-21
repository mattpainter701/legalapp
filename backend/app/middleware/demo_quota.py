"""Reserve one demo quota slot around each user-initiated AI response."""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.database import async_session_maker
from app.services.demo_quota import (
    DemoOperationDuplicate,
    DemoQuotaExceeded,
    release_demo_operation,
    reserve_demo_operation,
    settle_demo_operation,
)
from app.services.plugins.manifest import get_plugin_manifest, valid_plugin_names

settings = get_settings()


_DEMO_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEMO_PROVIDER_GET_ROUTES = frozenset(
    {
        "/api/integrations/microsoft/connect",
        "/api/integrations/microsoft/callback",
        "/api/integrations/google/connect",
        "/api/integrations/qbo/connect",
        "/api/integrations/qbo/callback",
        "/api/integrations/qbo/items",
        "/api/integrations/google/callback",
        "/api/integrations/zoom/connect",
        "/api/integrations/zoom/callback",
        "/api/integrations/zoom-phone/connect",
        "/api/integrations/zoom-phone/callback",
    }
)


def _is_blocked_demo_action(path: str, method: str) -> bool:
    """Return whether a demo request can cause provider/outbound side effects.

    The broad matter and mediation prefixes are narrowed below so synthetic
    matter/task/document editing remains available while direct email and
    approved mediation sends stay fail-closed.
    """
    method = method.upper()
    if path.startswith(("/api/auth/microsoft", "/api/auth/google")):
        # OAuth connect/callback routes are provider-bound even when they use
        # GET; status/profile reads are deliberately outside these prefixes.
        return True
    if method == "GET" and path in _DEMO_PROVIDER_GET_ROUTES:
        # These endpoints redirect to, exchange credentials with, or fetch
        # records from an external provider. They are distinct from read-only
        # integration status endpoints.
        return True
    if (
        path.startswith(
            (
                "/api/integrations",
                "/api/sync/",
                "/api/v1/smb",
                "/api/admin/cloud-search",
                "/api/admin/sharepoint",
                "/api/admin/smb",
                "/api/mcp",
                # The email agent router is mounted at /api/email; it reads the
                # connected mailbox and reuses the calendar sync entrypoint.
                "/api/email/",
                "/api/calendar/sync",
                "/api/calendar/scheduled-events",
                "/api/billing/checkout-session",
                "/api/billing/portal",
            )
        )
        and method in _DEMO_MUTATING_METHODS
    ):
        return True
    if (
        path.endswith("/email-client") or path.endswith("/cloud-folder/sync")
    ) and method in _DEMO_MUTATING_METHODS:
        return True
    if (
        method in _DEMO_MUTATING_METHODS
        and path.endswith("/send")
        and (
            path.startswith("/api/plugins/mediation/")
            or path.startswith("/api/matters/")
        )
    ):
        return True
    if (
        method in _DEMO_MUTATING_METHODS
        and path.startswith("/api/billing/invoices/")
        and path.endswith("/payment-link")
    ):
        return True
    if method in _DEMO_MUTATING_METHODS and path.startswith(
        (
            "/api/intake/dashboard/zoom-phone/sync",
            # The scheduler router is mounted under the /api prefix.
            "/api/scheduler/agents/",
        )
    ):
        return True
    if method in _DEMO_MUTATING_METHODS and (
        path == "/api/admin/users/invite"
        or path == "/api/auth/forgot-password"
        or path.endswith("/portal/invite")
        or path.endswith("/remind")
        or (path.startswith("/api/plugins/mediation/") and path.endswith("/invite"))
    ):
        return True
    return False


def _surface(path: str, method: str) -> str | None:
    if method != "POST":
        return None
    parts = [part for part in path.split("/") if part]
    if (
        len(parts) in {4, 5}
        and parts[:2] == ["api", "conversations"]
        and parts[3] == "messages"
        and (len(parts) == 4 or parts[4] == "stream")
    ):
        return "chat"
    if parts == ["api", "office", "plans"]:
        return "office"
    if (
        len(parts) == 4
        and parts[:2] == ["api", "plugins"]
        and parts[2] in valid_plugin_names()
    ):
        manifest = get_plugin_manifest(parts[2])
        if parts[3] == "cold-start" or (
            manifest is not None and parts[3] in manifest.skills
        ):
            return "plugin"
    return None


def _demo_tenant(request: Request) -> uuid.UUID | None:
    token = request.cookies.get("access_token")
    if not token:
        authorization = request.headers.get("authorization", "")
        token = (
            authorization.split(" ", 1)[1]
            if authorization.startswith("Bearer ")
            else None
        )
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        return None
    if payload.get("plan") != "demo" or not payload.get("tenant_id"):
        return None
    try:
        return uuid.UUID(payload["tenant_id"])
    except (TypeError, ValueError):
        return None


class DemoQuotaMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        surface = _surface(request.url.path, request.method)
        demo_tenant_id = _demo_tenant(request)
        if demo_tenant_id is not None and _is_blocked_demo_action(
            request.url.path, request.method
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Live integrations are disabled in demo workspaces"},
            )
        tenant_id = demo_tenant_id if surface else None
        if tenant_id is None:
            return await call_next(request)
        key = request.headers.get("x-idempotency-key") or getattr(
            request.state, "request_id", str(uuid.uuid4())
        )
        try:
            async with async_session_maker() as db:
                reservation = await reserve_demo_operation(
                    db,
                    tenant_id=tenant_id,
                    idempotency_key=key,
                    surface=surface,
                )
        except DemoQuotaExceeded as exc:
            return JSONResponse(status_code=429, content={"detail": str(exc)})
        except DemoOperationDuplicate as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        if reservation is None:
            return await call_next(request)

        try:
            response = await call_next(request)
        except Exception:
            async with async_session_maker() as db:
                await release_demo_operation(db, reservation)
            raise

        original_iterator = response.body_iterator

        async def accounted_body():
            stream_terminal = not request.url.path.endswith("/messages/stream")
            stream_failed = False
            stream_tail = b""
            try:
                async for chunk in original_iterator:
                    if isinstance(chunk, str):
                        encoded = chunk.encode("utf-8", errors="ignore")
                    else:
                        encoded = bytes(chunk)
                    scan = stream_tail + encoded
                    stream_failed = stream_failed or b"data: [ERROR" in scan
                    stream_terminal = (
                        stream_terminal or b"data: [STREAM_COMPLETE]" in scan
                    )
                    stream_tail = scan[-256:]
                    yield chunk
            except BaseException:
                async with async_session_maker() as db:
                    await release_demo_operation(db, reservation)
                raise
            else:
                async with async_session_maker() as db:
                    if (
                        response.status_code < 400
                        and stream_terminal
                        and not stream_failed
                    ):
                        await settle_demo_operation(db, reservation)
                    else:
                        await release_demo_operation(db, reservation)

        response.body_iterator = accounted_body()
        return response
