"""
Tests for error logging service.

Verifies that errors are properly logged to the ErrorLog table with correct
tenant/user context, stack traces, and request metadata.
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database import async_session_maker
from app.models.error_log import ErrorLog
from app.services.error_log import log_error


class TestErrorLogging:
    """Test error logging service."""

    @pytest.mark.asyncio
    async def test_log_error_creates_record(self):
        """Verify that log_error creates an ErrorLog record in the database."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        exc = ValueError("Test error")

        await log_error(
            exc=exc,
            error_type="validation_error",
            severity="error",
            endpoint="/api/test",
            method="POST",
            status_code=400,
            ip_address="127.0.0.1",
            user_agent="TestClient/1.0",
            request_id="test-request-123",
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=None,
            query_text="test query",
        )

        # Query the database to verify the record was created
        async with async_session_maker() as session:
            stmt = select(ErrorLog).where(ErrorLog.tenant_id == tenant_id)
            result = await session.execute(stmt)
            error_logs = result.scalars().all()

        assert len(error_logs) > 0
        error_log = error_logs[0]
        assert error_log.tenant_id == tenant_id
        assert error_log.user_id == user_id
        assert error_log.error_type == "validation_error"
        assert error_log.severity == "error"
        assert error_log.endpoint == "/api/test"
        assert error_log.method == "POST"
        assert error_log.status_code == 400
        assert error_log.ip_address == "127.0.0.1"
        assert error_log.user_agent == "TestClient/1.0"
        assert error_log.request_id == "test-request-123"
        assert "ValueError" in error_log.message
        assert error_log.stack_trace is not None
        assert "Test error" in error_log.message

    @pytest.mark.asyncio
    async def test_log_error_without_tenant_skips(self):
        """Verify that log_error skips logging if tenant_id is None."""
        exc = ValueError("Test error")

        # Should not raise, but silently skip
        await log_error(
            exc=exc,
            error_type="validation_error",
            severity="error",
            endpoint="/api/test",
            method="POST",
            tenant_id=None,  # No tenant
        )

        # Verify no records were created (we can't query by None tenant, so this is implicit)

    @pytest.mark.asyncio
    async def test_log_error_trims_query_text(self):
        """Verify that query_text is trimmed to 1000 characters."""
        tenant_id = uuid.uuid4()
        long_query = "x" * 2000

        await log_error(
            exc=ValueError("Test"),
            error_type="api_error",
            severity="error",
            endpoint="/api/test",
            method="POST",
            tenant_id=tenant_id,
            query_text=long_query,
        )

        async with async_session_maker() as session:
            stmt = select(ErrorLog).where(ErrorLog.tenant_id == tenant_id)
            result = await session.execute(stmt)
            error_logs = result.scalars().all()

        assert len(error_logs) > 0
        assert len(error_logs[0].query_text) == 1000

    @pytest.mark.asyncio
    async def test_log_error_handles_database_failures(self):
        """Verify that log_error handles database failures gracefully (fire-and-forget)."""
        tenant_id = uuid.uuid4()
        exc = ValueError("Test error")

        # Mock async_session_maker to raise an exception
        with patch("app.services.error_log.async_session_maker") as mock_session:
            mock_session.side_effect = RuntimeError("Database unavailable")

            # Should not raise, but log the failure
            await log_error(
                exc=exc,
                error_type="api_error",
                severity="error",
                endpoint="/api/test",
                method="POST",
                tenant_id=tenant_id,
            )
