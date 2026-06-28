import uuid
from unittest.mock import AsyncMock, Mock

import pytest

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
async def test_capture_error_without_tenant_context_skips_insert():
    db = Mock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    error_id = await capture_error(
        db=db,
        error_type="api_error",
        message="anonymous 404",
        status_code=404,
    )

    assert error_id is None
    db.execute.assert_not_awaited()
    assert not db.add.called
    db.commit.assert_not_awaited()
