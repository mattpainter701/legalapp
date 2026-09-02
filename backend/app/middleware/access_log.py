"""Middleware that logs every API request to api_access_logs — metadata only, no payloads."""

import time

from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.database import async_session_maker, set_tenant_context
from app.models.api_access_log import ApiAccessLog

SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/platform",
    "/static",
)


class ApiAccessLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(SKIP_PREFIXES):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        tenant_id = getattr(request.state, "tenant_id", None)
        user_id = getattr(request.state, "user_id", None)

        if tenant_id is None:
            return response

        # Audit writes must finish inside the request lifetime. An untracked
        # create_task can be lost on worker shutdown and can retain database
        # locks after the response/test session has otherwise completed.
        await _write_log(
            tenant_id=str(tenant_id),
            user_id=str(user_id) if user_id else None,
            endpoint=request.url.path,
            method=request.method,
            status_code=response.status_code,
            latency_ms=round(elapsed_ms, 2),
            ip_address=request.client.host if request.client else None,
            user_agent_short=_truncate(request.headers.get("user-agent", ""), 300),
            request_id=getattr(request.state, "request_id", None),
        )

        return response


async def _write_log(
    tenant_id: str,
    user_id: str | None,
    endpoint: str,
    method: str,
    status_code: int,
    latency_ms: float,
    ip_address: str | None,
    user_agent_short: str | None,
    request_id: str | None = None,
) -> None:
    try:
        async with async_session_maker() as db:
            # This is a new session, so it does not inherit the request's
            # transaction-local RLS context. Establish it before the insert.
            await set_tenant_context(db, tenant_id)
            # Best-effort: never let an access-log write hold up the response.
            # An in-flight SMS dispatch holds a FOR UPDATE lock on the actor's
            # user row across provider I/O (send_sms authorization fence); this
            # insert's user foreign key takes a KEY SHARE on that same row and
            # would otherwise block the whole request until the provider call
            # resolves. Bound the wait so the row simply isn't written.
            await db.execute(text("SET LOCAL lock_timeout = '2s'"))
            db.add(
                ApiAccessLog(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    endpoint=endpoint[:255],
                    method=method,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ip_address=ip_address,
                    user_agent_short=user_agent_short,
                    request_id=request_id[:100] if request_id else None,
                )
            )
            await db.commit()
    except Exception:
        pass


def _truncate(s: str, max_len: int) -> str:
    return s[:max_len] if s else ""
