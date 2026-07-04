from types import SimpleNamespace

from app.services.integration_observability import (
    apply_scope_audit,
    health_from_missing,
    missing_scopes,
    normalize_scope_list,
)


def _scope_matcher(scope, granted, provider):
    del provider
    return scope in granted


def test_normalize_scope_list_accepts_strings_and_iterables():
    assert normalize_scope_list("offline_access  Mail.Read") == [
        "offline_access",
        "Mail.Read",
    ]
    assert normalize_scope_list([" email ", "", "profile"]) == ["email", "profile"]


def test_missing_scopes_uses_provider_scope_matcher():
    missing = missing_scopes(
        "microsoft",
        "offline_access Mail.Read",
        "offline_access User.Read.All Mail.Read",
        _scope_matcher,
    )

    assert missing == ["User.Read.All"]


def test_apply_scope_audit_persists_gap_and_health():
    row = SimpleNamespace(
        scopes="offline_access Mail.Read",
        missing_scopes=None,
        health="healthy",
        is_active=True,
    )

    missing = apply_scope_audit(
        row,
        "microsoft",
        "offline_access User.Read.All Mail.Read",
        _scope_matcher,
    )

    assert missing == ["User.Read.All"]
    assert row.missing_scopes == "User.Read.All"
    assert row.health == "missing_scopes"


def test_scope_audit_does_not_overwrite_revoked_health():
    row = SimpleNamespace(
        scopes="offline_access",
        missing_scopes=None,
        health="revoked",
        is_active=False,
    )

    apply_scope_audit(row, "microsoft", "offline_access", _scope_matcher)

    assert row.missing_scopes is None
    assert row.health == "revoked"
    assert health_from_missing([], active=False) == "revoked"
