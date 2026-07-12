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
from app.services.plans import DEFAULT_PLAN_ID, MODULES, get_plan

settings = get_settings()

# Module-scoped API prefixes. Anything not listed (auth, me, users,
# notifications, health, portal) is shared infrastructure and passes. New
# module routers MUST be added here.
API_MODULE_MAP = {
    prefix: module.id for module in MODULES.values() for prefix in module.api_prefixes
}


def _is_read_only_plugin_catalog(request) -> bool:
    """Allow the add-on catalog/upgrade surface without granting plugin APIs.

    Unlicensed users are deliberately routed to ``/plugins`` as their basic
    portal.  That page needs exactly the catalog GET; every nested read,
    execution, setup, entitlement, and mutation endpoint remains plan-gated.
    """

    return request.method == "GET" and request.url.path.rstrip("/") == "/api/plugins"


def _required_module(path: str) -> str | None:
    for prefix, module in API_MODULE_MAP.items():
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


class ModuleGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if _is_read_only_plugin_catalog(request):
            return await call_next(request)

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
        allowed = set(plan.modules) | set(plan.api_dependencies)
        if payload.get("role") in {"admin", "accountant"}:
            allowed.add("admin")
        if module not in allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "Module not available on your plan"},
            )
        return await call_next(request)
