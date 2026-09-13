"""Bounds on how long an authenticated firm session may live.

Three bounds, kept separate because each answers a different question and none
of them implies the others:

``idle``
    How long a session survives without being used. Enforced as the TTL on each
    rotating refresh token, so every rotation restarts the clock.

``absolute``
    How long one rotation chain may live at all, however continuously it is
    used. Enforced by carrying the chain's origin timestamp through every
    rotation, because a TTL that every rotation renews cannot express it.

``session epoch``
    A per-user instant before which every credential the user holds is void.
    Checked against an access token's ``iat`` and against a chain's origin.

The two duration bounds limit the damage from a credential nobody knows is
stolen. Only the epoch ends a session someone has *decided* to end — a password
reset, or signing out everywhere — which neither duration bound can do, since
both are satisfied by a chain that is young and in active use. A product
holding privileged client matter data needs all three.

Refusals are returned as a reason rather than raised so the caller chooses the
status code, and so the reason can be audited without parsing a message.
"""

from datetime import datetime, timezone

from app.config import get_settings

settings = get_settings()

#: A credential minted before the user's session epoch.
SESSION_REVOKED = "session_revoked"
#: A rotation chain that has reached its absolute lifetime.
ABSOLUTE_LIFETIME_EXCEEDED = "absolute_lifetime_exceeded"
#: A credential that cannot be placed in time, so cannot be shown to be valid.
ORIGIN_UNKNOWN = "origin_unknown"


def idle_ttl_seconds() -> int:
    """Seconds a session may sit unused before it must be signed in again."""
    return settings.SESSION_IDLE_TIMEOUT_HOURS * 3600


def absolute_ttl_seconds() -> int:
    """Seconds one rotation chain may live, however active the session is."""
    return settings.SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600


def session_epoch_now() -> datetime:
    """The cutoff to stamp on a user when ending that user's sessions.

    Truncated to a whole second because a JWT ``iat`` is whole seconds. An epoch
    carrying a fractional part would sort *after* the ``iat`` of a token minted
    microseconds later in the same second, so stamping it would void the very
    credential the caller is about to be issued.

    Truncating down is what makes that work, and it leaves a sub-second window:
    a credential minted earlier in the same wall-clock second as the stamp
    survives. Exploiting it would mean holding a session created inside that
    same second, which is not something an attacker can arrange — and rounding
    up instead would sign out the person who asked to sign out everyone else,
    every time. The window is the cost of that, and it is the cheaper side.
    """
    return datetime.now(timezone.utc).replace(microsecond=0)


def _epoch_seconds(moment: datetime | None) -> float | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        # The column is timestamptz; a naive value can only come from a driver
        # that dropped the zone, and UTC is what was stored.
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def access_token_refusal_reason(
    *,
    issued_at: int | float | None,
    sessions_valid_after: datetime | None,
) -> str | None:
    """Why this access token is no longer acceptable, or None if it is.

    A user who has never ended a session has no epoch, and every token they hold
    stands on its own expiry alone.
    """
    cutoff = _epoch_seconds(sessions_valid_after)
    if cutoff is None:
        return None
    if issued_at is None:
        # Unplaceable in time. Once a user has ended their sessions, a token
        # that cannot be shown to post-date that decision must not be honoured.
        return ORIGIN_UNKNOWN
    return SESSION_REVOKED if issued_at < cutoff else None


def rotation_refusal_reason(
    *,
    family_issued_at: int | float | None,
    sessions_valid_after: datetime | None,
    now: float | None = None,
) -> str | None:
    """Why this rotation chain may not be rotated again, or None if it may."""
    if family_issued_at is None:
        # Chains minted before this policy existed carry no origin, so their age
        # is unknowable and their absolute bound unenforceable. Refusing costs
        # one sign-in; admitting them grandfathers an unbounded session.
        return ORIGIN_UNKNOWN
    moment = now if now is not None else datetime.now(timezone.utc).timestamp()
    if moment - family_issued_at >= absolute_ttl_seconds():
        return ABSOLUTE_LIFETIME_EXCEEDED
    cutoff = _epoch_seconds(sessions_valid_after)
    if cutoff is not None and family_issued_at < cutoff:
        return SESSION_REVOKED
    return None
