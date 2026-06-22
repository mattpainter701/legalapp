from app.services.plans import get_plan, public_plans, plan_for_config


def test_intake_only_plan_shape():
    plan = get_plan("intake-only")
    assert plan.modules == ["intake-dashboard"]
    assert plan.default_module == "intake-dashboard"
    assert plan.public_signup is True
    assert plan.upsell_target == "full-platform"
    assert plan.billing_tier == "intake_trial"


def test_full_platform_is_not_public():
    assert get_plan("full-platform").public_signup is False


def test_public_plans_only_returns_signup_enabled():
    ids = {p.id for p in public_plans()}
    assert "intake-only" in ids
    assert "full-platform" not in ids


def test_plan_for_config_defaults_to_full_platform():
    assert plan_for_config(None).id == "full-platform"
    assert plan_for_config({}).id == "full-platform"
    assert plan_for_config({"plan": "intake-only"}).id == "intake-only"
    assert plan_for_config({"plan": "bogus"}).id == "full-platform"


def test_get_plan_unknown_returns_none():
    assert get_plan("nope") is None


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
    # admin user also gets the admin module via _with_finance_admin
    assert "intake-dashboard" in modules
    assert "plugins" not in modules
    assert route == "/intake/dashboard"


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
