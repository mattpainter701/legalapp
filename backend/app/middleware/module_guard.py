"""Fail-closed API module enforcement keyed off the JWT plan claim.

The plan claim is server-signed (set at token issuance), so it is trustworthy.
A request to a module-scoped API prefix is rejected with 403 when that module
is not part of the tenant's plan. Tokens with no plan claim default to the full
platform, so pre-existing sessions are never blocked.
"""

from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.services.plans import DEFAULT_PLAN_ID, get_plan

settings = get_settings()

# Module-scoped API prefixes. Anything not listed (auth, me, users,
# notifications, intake, admin, plugins listing, health, portal) is shared
# infrastructure and passes. New module routers MUST be added here.
API_MODULE_MAP = {
    "/api/matters": "matters",
    "/api/chat": "chat",
    "/api/calendar": "calendar",
    "/api/tasks": "tasks",
    "/api/communications": "communications",
    "/api/contacts": "contacts",
    "/api/templates": "templates",
    "/api/time-tracking": "time-tracking",
    "/api/invoices": "invoices",
    "/api/trust": "trust",
    "/api/reports": "reports",
    "/api/mcp": "mcp",
}


def _required_module(path: str) -> str | None:
    for prefix, module in API_MODULE_MAP.items():
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


class ModuleGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        module = _required_module(request.url.path)
        if module is None:
            return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            auth = request.headers.get("Authorization", "")
            token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else None
        if not token:
            # Unauthenticated — let the route's auth dependency return 401.
            return await call_next(request)

        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
        except JWTError:
            return await call_next(request)

        plan = get_plan(payload.get("plan")) or get_plan(DEFAULT_PLAN_ID)
        allowed = set(plan.modules)
        if payload.get("role") in {"admin", "accountant"}:
            allowed.add("admin")
        if module not in allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "Module not available on your plan"},
            )
        return await call_next(request)
