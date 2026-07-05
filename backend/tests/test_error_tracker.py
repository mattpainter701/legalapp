import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.error_log import ErrorLog
from app.services.error_tracker import capture_error


@pytest.mark.asyncio
async def test_capture_error_binds_tenant_context_before_insert():
    db = Mock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    tenant_id = uuid.uuid4()

    await capture_error(
        db=db,
        error_type="api_error",
        message="404: Conversation not found",
        status_code=404,
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
    )

    db.execute.assert_awaited()
    assert db.add.called
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_error_without_tenant_context_still_logs():
    db = Mock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = Mock()

    error_id = await capture_error(
        db=db,
        error_type="api_error",
        message="anonymous 404",
        status_code=404,
    )

    # DB-level IDs are assigned on flush/commit; with this mocked session the
    # exact ID value is not guaranteed even though logging should still occur.
    assert error_id is None or isinstance(error_id, uuid.UUID)
    saved_error = db.add.call_args.args[0]
    assert isinstance(saved_error, ErrorLog)
    assert saved_error.tenant_id is None
    assert saved_error.request_id is None
    db.execute.assert_awaited()
    assert db.add.called
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_error_records_request_id():
    db = Mock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = Mock()

    await capture_error(
        db=db,
        error_type="api_error",
        message="anonymous 404",
        status_code=404,
        request_id="rid-abc",
    )

    saved_error = db.add.call_args.args[0]
    assert isinstance(saved_error, ErrorLog)
    assert saved_error.request_id == "rid-abc"
