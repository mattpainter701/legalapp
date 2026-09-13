"""Bounds on session lifetime: idle, absolute, and the per-user epoch.

These are the pure-policy tests — no database, no Redis. The endpoint wiring
that consults them is covered in ``test_auth_session.py`` (helpers) and
``test_auth_session_epoch.py`` (the full refresh/reset paths).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services import session_policy

settings = get_settings()


# ── The epoch stamp ───────────────────────────────────────────────────────────


def test_session_epoch_is_truncated_to_whole_seconds():
    """A fractional epoch would void the token minted right after it.

    JWT ``iat`` is whole seconds, so an epoch of 10:00:00.9 sorts *after* the
    ``iat`` of a token minted at 10:00:00.95 — which floors to 10:00:00. The
    caller of "sign out everywhere" would be signed out by their own request.
    """
    epoch = session_policy.session_epoch_now()
    assert epoch.microsecond == 0
    assert epoch.tzinfo is not None

    minted_just_after = int((epoch + timedelta(milliseconds=950)).timestamp())
    assert (
        session_policy.access_token_refusal_reason(
            issued_at=minted_just_after, sessions_valid_after=epoch
        )
        is None
    )


# ── Access tokens against the epoch ───────────────────────────────────────────


def test_access_token_without_an_epoch_is_untouched():
    """A user who never ended a session stands on token expiry alone."""
    assert (
        session_policy.access_token_refusal_reason(
            issued_at=1_600_000_000, sessions_valid_after=None
        )
        is None
    )


def test_access_token_minted_before_the_epoch_is_refused():
    epoch = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    stale = int((epoch - timedelta(seconds=1)).timestamp())
    assert (
        session_policy.access_token_refusal_reason(
            issued_at=stale, sessions_valid_after=epoch
        )
        == session_policy.SESSION_REVOKED
    )


def test_access_token_minted_at_the_epoch_survives():
    epoch = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    assert (
        session_policy.access_token_refusal_reason(
            issued_at=int(epoch.timestamp()), sessions_valid_after=epoch
        )
        is None
    )


def test_access_token_with_no_issue_time_is_refused_once_an_epoch_exists():
    """Unplaceable in time must mean refused, never admitted by default."""
    epoch = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    assert (
        session_policy.access_token_refusal_reason(
            issued_at=None, sessions_valid_after=epoch
        )
        == session_policy.ORIGIN_UNKNOWN
    )


def test_naive_epoch_is_read_as_utc_not_local():
    """A driver that drops the zone must not shift the cutoff by hours."""
    aware = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    naive = aware.replace(tzinfo=None)
    stale = int(aware.timestamp()) - 1
    assert session_policy.access_token_refusal_reason(
        issued_at=stale, sessions_valid_after=naive
    ) == session_policy.access_token_refusal_reason(
        issued_at=stale, sessions_valid_after=aware
    )


# ── Rotation against the absolute bound and the epoch ─────────────────────────


def test_a_young_unrevoked_chain_rotates():
    now = 1_800_000_000.0
    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=now - 60,
            sessions_valid_after=None,
            now=now,
        )
        is None
    )


def test_chain_at_its_absolute_lifetime_is_refused_however_active():
    """This is the bound a rotating TTL cannot express.

    Every rotation restarts the idle TTL, so a chain used daily renews forever.
    Only the origin carried through rotation can stop it.
    """
    now = 1_800_000_000.0
    origin = now - session_policy.absolute_ttl_seconds()
    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=origin, sessions_valid_after=None, now=now
        )
        == session_policy.ABSOLUTE_LIFETIME_EXCEEDED
    )


def test_chain_one_second_inside_its_absolute_lifetime_still_rotates():
    now = 1_800_000_000.0
    origin = now - session_policy.absolute_ttl_seconds() + 1
    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=origin, sessions_valid_after=None, now=now
        )
        is None
    )


def test_chain_predating_the_epoch_is_refused():
    now = 1_800_000_000.0
    origin = now - 60
    epoch = datetime.fromtimestamp(now - 30, tz=timezone.utc)
    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=origin, sessions_valid_after=epoch, now=now
        )
        == session_policy.SESSION_REVOKED
    )


def test_chain_minted_after_the_epoch_rotates():
    """The caller of "sign out everywhere" keeps rotating on this device."""
    now = 1_800_000_000.0
    epoch = datetime.fromtimestamp(now - 30, tz=timezone.utc)
    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=now - 29, sessions_valid_after=epoch, now=now
        )
        is None
    )


def test_chain_with_no_origin_is_refused_rather_than_grandfathered():
    """Chains minted before this policy carry no origin.

    Their age is unknowable, so their absolute bound is unenforceable. Refusing
    costs one sign-in; admitting them would grandfather unbounded sessions.
    """
    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=None, sessions_valid_after=None
        )
        == session_policy.ORIGIN_UNKNOWN
    )


def test_rotation_defaults_to_the_current_clock():
    """``now`` is injectable for tests but must not be required in production."""
    import time

    assert (
        session_policy.rotation_refusal_reason(
            family_issued_at=time.time() - 5, sessions_valid_after=None
        )
        is None
    )


# ── Configuration ─────────────────────────────────────────────────────────────


def test_ttl_helpers_track_configuration(monkeypatch):
    monkeypatch.setattr(session_policy.settings, "SESSION_IDLE_TIMEOUT_HOURS", 8)
    monkeypatch.setattr(session_policy.settings, "SESSION_ABSOLUTE_TIMEOUT_HOURS", 72)
    assert session_policy.idle_ttl_seconds() == 8 * 3600
    assert session_policy.absolute_ttl_seconds() == 72 * 3600


@pytest.mark.parametrize(
    "idle,absolute",
    [
        (0, 720),  # idle below the floor
        (24 * 15, 720),  # idle above the ceiling
        (12, 0),  # absolute below the floor
        (12, 24 * 366),  # absolute above the ceiling
        (48, 24),  # absolute shorter than idle silently replaces it
    ],
)
def test_invalid_session_lifetimes_are_rejected(idle, absolute):
    from types import SimpleNamespace

    from app.config import validate_session_lifetimes

    with pytest.raises(ValueError):
        validate_session_lifetimes(
            SimpleNamespace(
                SESSION_IDLE_TIMEOUT_HOURS=idle,
                SESSION_ABSOLUTE_TIMEOUT_HOURS=absolute,
            )
        )


def test_shipped_session_lifetimes_are_valid():
    from app.config import validate_session_lifetimes

    validate_session_lifetimes(settings)
