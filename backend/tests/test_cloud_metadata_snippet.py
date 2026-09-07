import pytest
from unittest.mock import AsyncMock

from app.models.cloud_metadata import CloudMetadata
from app.services.cloud_sync import CloudSyncService


def test_cloud_metadata_snippet_column_is_bounded():
    assert CloudMetadata.__table__.c.snippet.type.length == 500


@pytest.mark.asyncio
async def test_upsert_bounds_oversize_unicode_and_preserves_null():
    service = CloudSyncService()
    service._latest_completed_migration = AsyncMock(return_value=None)
    captured = {}

    class _Db:
        async def execute(self, statement):
            captured["values"] = statement.compile().params

    await service._upsert(
        _Db(),
        "00000000-0000-0000-0000-000000000001",
        provider="google",
        object_type="file",
        object_id="object-1",
        snippet="😀" * 600,
    )
    assert captured["values"]["snippet"] == "😀" * 500
    assert len(captured["values"]["snippet"]) == 500

    captured.clear()
    await service._upsert(
        _Db(),
        "00000000-0000-0000-0000-000000000001",
        provider="google",
        object_type="file",
        object_id="object-2",
        snippet=None,
    )
    assert captured["values"]["snippet"] is None


@pytest.mark.asyncio
async def test_upsert_keeps_small_snippet_exactly():
    service = CloudSyncService()
    service._latest_completed_migration = AsyncMock(return_value=None)
    captured = {}

    class _Db:
        async def execute(self, statement):
            captured["values"] = statement.compile().params

    value = "Résumé — café"
    await service._upsert(
        _Db(),
        "00000000-0000-0000-0000-000000000001",
        provider="microsoft",
        object_type="email",
        object_id="object-3",
        snippet=value,
    )
    assert captured["values"]["snippet"] == value
