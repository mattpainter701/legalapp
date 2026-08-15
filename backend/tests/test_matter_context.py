import pytest

from app.services.cache import ExpertiseCacheManager
from app.services.matter_context import MatterContextService


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.deleted = []

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def delete(self, *keys):
        self.deleted.extend(keys)
        for key in keys:
            self.values.pop(key, None)


class FakeSettingResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSettingDb:
    def __init__(self, value):
        self.value = value

    async def execute(self, _query):
        return FakeSettingResult(self.value)


def test_format_injects_high_value_matter_fields():
    context = MatterContextService().format_matter_context(
        {
            "matter_name": "Acme acquisition",
            "description": "Acme is acquiring Northstar.",
            "memory_content": "CEO needs same-day approval advice.",
            "initial_posture": "Seek a narrow indemnity cap.",
            "key_dates": {"Signing": "2026-09-01", "Closing": "2026-10-15"},
            "risk_level": "high",
            "materiality": "material",
            "exposure_range": "$2m-$5m",
            "retainers": [
                {"type": "evergreen", "amount": 15000, "current_balance": 12000}
            ],
            "recent_communications": [
                {
                    "direction": "outbound",
                    "channel": "email",
                    "subject": "Indemnity proposal",
                    "summary": "Counterparty requested a cap revision.",
                }
            ],
        }
    )

    assert "Description: Acme is acquiring Northstar." in context
    assert "Matter Memory: CEO needs same-day approval advice." in context
    assert "Initial Posture: Seek a narrow indemnity cap." in context
    assert "Key Dates:" in context
    assert "Exposure Range: $2m-$5m" in context
    assert "Active Retainers:" in context
    assert "Recent Communications:" in context


def test_privacy_mode_redacts_new_free_text_context_fields():
    service = MatterContextService()
    scrubbed = service.scrub_matter_context(
        {
            "description": "Client Jane Doe is acquiring Northstar.",
            "memory_content": "Jane's direct number is 555-0100.",
            "initial_posture": "Call Jane before settlement.",
            "recent_events": [{"content": "Jane approved the proposal."}],
            "recent_communications": [
                {"subject": "Jane's approval", "summary": "Call 555-0100"}
            ],
        },
        privacy_mode=True,
    )
    rendered = service.format_matter_context(scrubbed, scrubbed=True)

    assert "Jane" not in rendered
    assert "555-0100" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [(None, True), (True, True), (False, False)],
)
async def test_matter_context_feature_setting_defaults_on(stored_value, expected):
    service = MatterContextService()

    assert await service.is_enabled(FakeSettingDb(stored_value), "tenant-a") is expected


@pytest.mark.asyncio
async def test_invalidate_matter_context_removes_only_targeted_entry():
    cache = ExpertiseCacheManager()
    cache.cache_enabled = True
    cache.redis_client = FakeRedis()
    target = cache._make_key("matter", "tenant-a", "matter-a", "standard")
    other = cache._make_key("matter", "tenant-a", "matter-b", "standard")
    cache.redis_client.values[target] = "stale"
    cache.redis_client.values[other] = "keep"

    assert await cache.invalidate_matter_context("matter-a", "tenant-a")
    assert target not in cache.redis_client.values
    assert cache.redis_client.values[other] == "keep"


@pytest.mark.asyncio
async def test_next_chat_context_is_fresh_after_invalidation():
    cache = ExpertiseCacheManager()
    cache.cache_enabled = True
    cache.redis_client = FakeRedis()

    await cache.set_cached_matter_context("matter-a", "tenant-a", "old memory")
    assert await cache.get_cached_matter_context("matter-a", "tenant-a") == "old memory"

    await cache.invalidate_matter_context("matter-a", "tenant-a")
    assert await cache.get_cached_matter_context("matter-a", "tenant-a") is None

    await cache.set_cached_matter_context("matter-a", "tenant-a", "updated memory")
    assert await cache.get_cached_matter_context("matter-a", "tenant-a") == "updated memory"


@pytest.mark.asyncio
async def test_privacy_context_uses_a_separate_cache_entry():
    cache = ExpertiseCacheManager()
    cache.cache_enabled = True
    cache.redis_client = FakeRedis()

    await cache.set_cached_matter_context(
        "matter-a", "tenant-a", "full", privacy_mode=False
    )
    await cache.set_cached_matter_context(
        "matter-a", "tenant-a", "redacted", privacy_mode=True
    )

    assert await cache.get_cached_matter_context("matter-a", "tenant-a") == "full"
    assert (
        await cache.get_cached_matter_context("matter-a", "tenant-a", privacy_mode=True)
        == "redacted"
    )
