import pytest

from app.services.smb_search import get_content_result


@pytest.mark.asyncio
async def test_legacy_smb_result_lookup_requires_tenant_and_file_binding():
    with pytest.raises(ValueError, match="tenant_id and file_id"):
        await get_content_result("task-id", redis_client=object())
