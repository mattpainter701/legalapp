"""Complete request-level audit trail for the operator API."""

import logging

from starlette.middleware.base import BaseHTTPMiddleware

from app.database import async_session_maker
from app.services.operator_audit import record_operator_audit


logger = logging.getLogger(__name__)


class PlatformAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not request.url.path.startswith("/api/platform"):
            return await call_next(request)
        response = None
        raised = False
        try:
            response = await call_next(request)
            return response
        except Exception:
            raised = True
            raise
        finally:
            try:
                async with async_session_maker() as db:
                    await record_operator_audit(
                        db,
                        request,
                        action="platform.request",
                        resource_type="platform_endpoint",
                        resource_id=request.url.path[:120],
                        actor_type="platform_token",
                        actor_id=getattr(request.state, "platform_actor_id", None),
                        metadata={
                            "method": request.method,
                            "status_code": response.status_code if response else 500,
                            "scope": getattr(request.state, "platform_scope", None),
                            "token_jti": getattr(
                                request.state, "platform_token_jti", None
                            ),
                            "raised": raised,
                        },
                    )
                    await db.commit()
            except Exception:
                logger.exception("Unable to persist platform request audit event")
