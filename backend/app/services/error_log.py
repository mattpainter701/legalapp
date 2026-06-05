"""Error logging service for troubleshooting and support."""

import asyncio
import logging
import traceback
from typing import Optional
from uuid import UUID


from app.database import async_session_maker
from app.models.error_log import ErrorLog

logger = logging.getLogger(__name__)


async def log_error(
    exc: Exception,
    error_type: str,
    severity: str = "error",
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    conversation_id: Optional[UUID] = None,
    query_text: Optional[str] = None,
) -> None:
    """
    Write an error to the ErrorLog table.

    This is designed to be called from exception handlers as a fire-and-forget task.
    If the tenant_id is None, the error is still logged but without tenant context.

    Args:
        exc: The exception that was raised
        error_type: Classification of the error (e.g., "api_error", "llm_error")
        severity: One of "critical", "error", "warning", "info"
        endpoint: The API endpoint that failed
        method: The HTTP method (GET, POST, etc.)
        status_code: The HTTP response status code
        ip_address: Client IP address
        user_agent: Client user agent
        request_id: Unique request identifier
        tenant_id: Tenant ID (required if user is authenticated)
        user_id: User ID (optional, for system errors)
        conversation_id: Associated conversation ID (optional)
        query_text: The user's query that triggered the error (optional, trimmed for length)
    """
    try:
        # Extract message and stack trace from exception
        message = f"{type(exc).__name__}: {exc}"
        stack_trace = traceback.format_exc()

        # If tenant_id is None but error is from an API, skip logging
        # (unauthenticated requests won't have tenant context)
        if tenant_id is None:
            logger.warning(
                f"Skipping error log for {error_type}: no tenant_id available"
            )
            return

        # Create async session
        async with async_session_maker() as session:
            error_log = ErrorLog(
                tenant_id=tenant_id,
                user_id=user_id,
                error_type=error_type,
                severity=severity,
                message=message,
                stack_trace=stack_trace,
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                conversation_id=conversation_id,
                query_text=query_text[:1000]
                if query_text
                else None,  # Trim to 1000 chars
            )
            session.add(error_log)
            await session.commit()
            logger.debug(f"Error logged: {error_log.id}")

    except Exception as log_exc:
        # Silently fail on error logging to avoid cascading failures
        logger.exception(
            f"Failed to log error to database: {log_exc}. Original error: {exc}"
        )


def schedule_error_log(
    app,
    exc: Exception,
    error_type: str,
    severity: str = "error",
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    conversation_id: Optional[UUID] = None,
    query_text: Optional[str] = None,
) -> None:
    """
    Schedule an error log write as a background task (fire-and-forget).

    This wraps log_error in asyncio.create_task to avoid blocking the response.

    Args:
        app: FastAPI app instance (used to access event loop)
        exc: The exception that was raised
        error_type: Classification of the error
        severity: One of "critical", "error", "warning", "info"
        endpoint: The API endpoint that failed
        method: The HTTP method (GET, POST, etc.)
        status_code: The HTTP response status code
        ip_address: Client IP address
        user_agent: Client user agent
        request_id: Unique request identifier
        tenant_id: Tenant ID (required if user is authenticated)
        user_id: User ID (optional, for system errors)
        conversation_id: Associated conversation ID (optional)
        query_text: The user's query that triggered the error (optional)
    """
    # Schedule the coroutine without waiting for it
    asyncio.create_task(
        log_error(
            exc=exc,
            error_type=error_type,
            severity=severity,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            query_text=query_text,
        )
    )
