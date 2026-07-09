"""Middleware that logs every API request to api_access_logs — metadata only, no payloads."""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.database import async_session_maker
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
) -> None:
    try:
        async with async_session_maker() as db:
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
                )
            )
            await db.commit()
    except Exception:
        pass


def _truncate(s: str, max_len: int) -> str:
    return s[:max_len] if s else ""
