from app.services.capabilities import CAPABILITIES, is_valid_capability


def test_catalog_contains_core_capabilities():
    for cap in [
        "manage_users",
        "manage_roles",
        "manage_billing",
        "view_billing",
        "manage_matters",
        "manage_intake",
        "manage_documents",
        "manage_integrations",
        "admin_settings",
        "use_premium_ai",
    ]:
        assert cap in CAPABILITIES


def test_is_valid_capability():
    assert is_valid_capability("manage_roles") is True
    assert is_valid_capability("not_a_real_cap") is False
