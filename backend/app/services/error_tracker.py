"""Error tracking service — capture and persist error logs with request context.

Usage:
    from app.services.error_tracker import capture_error

    await capture_error(db=db, error_type="api_error", severity="error",
                        message=str(exc), request=request, status_code=500,
                        user_id=user.id, tenant_id=user.tenant_id)
"""

import logging
import traceback
import uuid
from typing import Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_log import ErrorLog

logger = logging.getLogger(__name__)

# Severity mapping for HTTP status codes
HTTP_SEVERITY_MAP = {
    400: "warning",
    401: "warning",
    403: "warning",
    404: "info",
    405: "warning",
    408: "error",
    409: "warning",
    422: "warning",
    429: "warning",
    500: "error",
    502: "error",
    503: "critical",
    504: "error",
}


async def capture_error(
    db: AsyncSession,
    error_type: str,
    message: str,
    severity: str = "error",
    request: Optional[Request] = None,
    status_code: Optional[int] = None,
    user_id: Optional[uuid.UUID] = None,
    tenant_id: Optional[uuid.UUID] = None,
    stack_trace: Optional[str] = None,
    query_text: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
) -> Optional[uuid.UUID]:
    """Persist an error log entry. Returns the error log ID or None on failure.

    Args:
        db: Async database session
        error_type: Classification (api_error, rag_query_error, llm_error, cache_error, etc.)
        message: Human-readable error message
        severity: critical, error, warning, info
        request: FastAPI Request object (for extracting IP, user_agent, endpoint, method)
        status_code: HTTP status code (auto-maps severity if not explicit)
        user_id: Affected user (None for system/anonymous errors)
        tenant_id: Tenant ID
        stack_trace: Exception traceback string
        query_text: User's query text if applicable
        conversation_id: Related conversation ID
        request_id: Correlation/request ID
    """
    try:
        if tenant_id is None:
            logger.debug("Skipping error log without tenant context: %s", message)
            return None

        endpoint = None
        method = None
        ip_address = None
        user_agent = None

        if request:
            endpoint = request.url.path
            method = request.method
            ip_address = request.client.host if request.client else None
            user_agent = (
                request.headers.get("user-agent", "")[:500] if request.headers else None
            )

        # Auto-map severity from status code if not explicitly provided
        if status_code and severity == "error" and status_code not in (500, 502, 504):
            severity = HTTP_SEVERITY_MAP.get(status_code, severity)

        if stack_trace is None:
            stack_trace = traceback.format_exc()
            if stack_trace == "NoneType: None\n":
                stack_trace = None

        error_log = ErrorLog(
            tenant_id=tenant_id,
            user_id=user_id,
            error_type=error_type,
            severity=severity,
            message=str(message)[:4000],  # Truncate very long messages
            stack_trace=stack_trace[:8000] if stack_trace else None,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            conversation_id=conversation_id,
            query_text=query_text[:2000] if query_text else None,
        )
        db.add(error_log)
        await db.commit()
        return error_log.id
    except Exception as exc:
        # Don't let error tracking failures cascade
        logger.error(f"Failed to capture error log: {exc}")
        return None


async def capture_chat_error(
    db: AsyncSession,
    error_type: str,
    message: str,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    request: Optional[Request] = None,
    query_text: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    severity: str = "error",
) -> Optional[uuid.UUID]:
    """Convenience wrapper for chat-specific error capture with standard context."""
    return await capture_error(
        db=db,
        error_type=error_type,
        message=message,
        severity=severity,
        request=request,
        user_id=user_id,
        tenant_id=tenant_id,
        query_text=query_text,
        conversation_id=conversation_id,
    )
