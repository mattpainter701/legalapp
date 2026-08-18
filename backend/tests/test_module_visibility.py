from types import SimpleNamespace

import pytest

from app.services.module_visibility import resolve_enabled_modules, resolve_plan_meta
from app.services.plans import DEFAULT_PLAN_ID, plan_for_config


def test_plan_for_config_ignores_legacy_scalar_config() -> None:
    assert plan_for_config("legacy-value").id == DEFAULT_PLAN_ID


@pytest.mark.asyncio
async def test_module_resolution_ignores_legacy_scalar_config() -> None:
    class Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(custom_config="legacy-value")

        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class Db:
        async def execute(self, _statement):
            return Result()

    modules, route = await resolve_enabled_modules(
        Db(),
        "00000000-0000-0000-0000-000000000001",
        user=SimpleNamespace(license_active=True, role="user"),
    )
    assert "chat" in modules
    assert route
    plan_id, _ = await resolve_plan_meta(Db(), "00000000-0000-0000-0000-000000000001")
    assert plan_id == DEFAULT_PLAN_ID
