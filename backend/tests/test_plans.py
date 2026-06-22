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
