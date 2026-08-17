from app.services.plans import (
    INTAKE_MODULES,
    MODULES,
    get_plan,
    public_plans,
    plan_for_config,
)
from app.services.module_visibility import GENERAL_MODULES


def test_intake_only_plan_shape():
    plan = get_plan("intake-only")
    assert plan.modules == list(INTAKE_MODULES)
    assert plan.modules == ["tasks", "intake-dashboard"]
    assert set(plan.api_dependencies) == {
        "intake",
        "contacts",
        "communications",
        "tasks",
    }
    assert plan.default_module == "intake-dashboard"
    assert plan.public_signup is True
    assert plan.upsell_target == "full-platform"
    assert plan.billing_tier == "intake_trial"


def test_full_platform_is_not_public():
    assert get_plan("full-platform").public_signup is False


def test_demo_plan_is_full_platform_but_not_public_or_premium():
    plan = get_plan("demo")
    assert plan.modules == list(MODULES)
    assert plan.default_module == "matters"
    assert plan.billing_tier == "demo"
    assert plan.public_signup is False
    assert plan.upsell_target == "full-platform"


def test_mcp_only_plan_shape():
    plan = get_plan("mcp-only")
    assert plan.modules == ["mcp"]
    assert plan.default_module == "mcp"
    assert plan.public_signup is False
    assert plan.upsell_target == "full-platform"
    assert plan.billing_tier == "payg"


def test_public_plans_only_returns_signup_enabled():
    ids = {p.id for p in public_plans()}
    assert "intake-only" in ids
    assert "mcp-only" not in ids
    assert "full-platform" not in ids
    assert "demo" not in ids


def test_plan_for_config_defaults_to_full_platform():
    assert plan_for_config(None).id == "full-platform"
    assert plan_for_config({}).id == "full-platform"
    assert plan_for_config({"plan": "intake-only"}).id == "intake-only"
    assert plan_for_config({"plan": "mcp-only"}).id == "mcp-only"
    assert plan_for_config({"plan": "demo"}).id == "demo"
    assert plan_for_config({"plan": "bogus"}).id == "full-platform"


def test_get_plan_unknown_returns_none():
    assert get_plan("nope") is None


def test_every_plan_module_has_a_catalog_entry_and_valid_default():
    for plan_id in ("demo", "intake-only", "mcp-only", "full-platform"):
        plan = get_plan(plan_id)
        assert plan.default_module in plan.modules
        assert all(module_id in MODULES for module_id in plan.modules)


def test_plugin_api_namespace_is_plan_gated():
    assert MODULES["plugins"].api_prefixes == ("/api/plugins",)
    assert "plugins" not in get_plan("intake-only").modules
    assert "plugins" not in get_plan("intake-only").api_dependencies


import pytest  # noqa: E402

from app.models.tenant import TenantSettings  # noqa: E402
from app.services.module_visibility import (  # noqa: E402
    resolve_enabled_modules,
    resolve_plan_meta,
)


@pytest.mark.asyncio
async def test_resolve_intake_only_from_plan(db_session, test_tenant, test_user):
    db_session.add(
        TenantSettings(tenant_id=test_tenant.id, custom_config={"plan": "intake-only"})
    )
    await db_session.commit()
    modules, route = await resolve_enabled_modules(
        db_session, test_tenant.id, user=test_user
    )
    # admin user also gets the admin module via _with_finance_admin.
    for module in INTAKE_MODULES:
        assert module in modules
    assert "plugins" not in modules
    assert route == "/intake/dashboard"


@pytest.mark.asyncio
async def test_resolve_mcp_only_from_plan(db_session, test_tenant, test_user):
    db_session.add(
        TenantSettings(tenant_id=test_tenant.id, custom_config={"plan": "mcp-only"})
    )
    await db_session.commit()
    modules, route = await resolve_enabled_modules(
        db_session, test_tenant.id, user=test_user
    )
    assert modules == ["admin", "mcp"]
    assert route == "/mcp"


@pytest.mark.asyncio
async def test_legacy_module_config_keeps_general_workspace(
    db_session, test_tenant, test_user
):
    db_session.add(
        TenantSettings(
            tenant_id=test_tenant.id,
            custom_config={"enabled_modules": ["matters", "chat", "calendar"]},
        )
    )
    await db_session.commit()

    modules, route = await resolve_enabled_modules(
        db_session, test_tenant.id, user=test_user
    )

    for module in GENERAL_MODULES:
        assert module in modules
    assert route == "/matters"


@pytest.mark.asyncio
async def test_resolve_plan_meta_exposes_upsell(db_session, test_tenant):
    db_session.add(
        TenantSettings(tenant_id=test_tenant.id, custom_config={"plan": "intake-only"})
    )
    await db_session.commit()
    plan_id, upsell = await resolve_plan_meta(db_session, test_tenant.id)
    assert plan_id == "intake-only"
    assert upsell == "full-platform"


@pytest.mark.asyncio
async def test_resolve_no_config_is_full_platform(db_session, test_tenant):
    plan_id, upsell = await resolve_plan_meta(db_session, test_tenant.id)
    assert plan_id == "full-platform"
    assert upsell is None
