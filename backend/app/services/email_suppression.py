"""Platform-wide outbound email suppression.

Every hard bounce and spam complaint the mail provider reports is recorded
here, and every system send is filtered against it. Continuing to mail an
address that has permanently failed is the single fastest way to lose sending
reputation for the whole domain, which would take down password reset for
every customer at once.

These helpers open their own short-lived session by default: ``EmailService``
is a module-level singleton called from routes, schedulers and background jobs
alike, and most of those callers have no session to lend. Callers that already
hold one should pass it to avoid taking a second connection.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.email_suppression import (
    PERMANENT_SUPPRESSION_REASONS,
    EmailSuppression,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _session_maker():
    """Resolve the session factory at call time, not at import time.

    The test suite binds its own factory to an isolated engine; reading the
    attribute through the module keeps a self-opened session pointed at
    whichever database the process is actually using.
    """
    from app import database

    return database.async_session_maker


def normalize_address(email: str) -> str:
    """Normalize for comparison: trim, strip a display name, lower-case.

    Only case and surrounding whitespace are normalized. Local parts are
    case-sensitive per RFC 5321 and providers disagree about plus-addressing,
    so no further canonicalization is attempted — two addresses that differ by
    more than case are treated as different mailboxes.
    """
    value = (email or "").strip()
    if value.endswith(">") and "<" in value:
        value = value[value.rindex("<") + 1 : -1].strip()
    return value.lower()


async def _suppressed_subset(db: AsyncSession, addresses: list[str]) -> set[str]:
    if not addresses:
        return set()
    rows = await db.execute(
        select(EmailSuppression.email).where(
            EmailSuppression.email.in_(addresses),
            EmailSuppression.released_at.is_(None),
        )
    )
    return {row for row in rows.scalars().all()}


async def filter_suppressed(
    recipients: list[str],
    *,
    db: AsyncSession | None = None,
) -> tuple[list[str], list[str]]:
    """Split recipients into (deliverable, suppressed).

    Fails open. A suppression lookup that errors must not block a password
    reset: the cost of one message to a dead address is far lower than locking
    every user out of account recovery because the table is unreachable.
    """
    if not settings.EMAIL_SUPPRESSION_ENABLED:
        return list(recipients), []

    normalized = {normalize_address(r): r for r in recipients if r}
    if not normalized:
        return list(recipients), []

    try:
        if db is not None:
            blocked = await _suppressed_subset(db, list(normalized))
        else:
            async with _session_maker()() as session:
                blocked = await _suppressed_subset(session, list(normalized))
    except Exception as exc:
        logger.error(
            "Suppression lookup failed; allowing send (error_type=%s)",
            type(exc).__name__,
        )
        return list(recipients), []

    deliverable = [
        original for key, original in normalized.items() if key not in blocked
    ]
    suppressed = [original for key, original in normalized.items() if key in blocked]
    return deliverable, suppressed


async def record_suppression(
    email: str,
    *,
    reason: str,
    provider: str | None = None,
    detail: str | None = None,
    provider_payload: dict | None = None,
    db: AsyncSession | None = None,
) -> bool:
    """Add an address to the suppression list. Returns True when newly added.

    A repeat report for an address already suppressed is a no-op, and never
    reverses an operator's manual release — a customer who fixed their mailbox
    and was released stays released until a *new* bounce arrives after that
    release, which the provider will send on the next real attempt.
    """
    if reason not in PERMANENT_SUPPRESSION_REASONS:
        raise ValueError(f"Unsupported suppression reason: {reason}")

    normalized = normalize_address(email)
    if not normalized or "@" not in normalized:
        return False

    async def _write(session: AsyncSession) -> bool:
        stmt = (
            pg_insert(EmailSuppression)
            .values(
                email=normalized,
                reason=reason,
                provider=provider,
                detail=detail,
                provider_payload=provider_payload,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(EmailSuppression.id)
        )
        inserted = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
        return inserted is not None

    if db is not None:
        return await _write(db)
    async with _session_maker()() as session:
        return await _write(session)


async def release_suppression(
    email: str,
    *,
    released_by_user_id=None,
    db: AsyncSession | None = None,
) -> bool:
    """Lift a suppression so the address can receive system email again.

    The row is retained with ``released_at`` set rather than deleted, so the
    history of why an address was ever blocked survives the release.
    """
    normalized = normalize_address(email)
    if not normalized:
        return False

    async def _write(session: AsyncSession) -> bool:
        row = await session.scalar(
            select(EmailSuppression).where(
                EmailSuppression.email == normalized,
                EmailSuppression.released_at.is_(None),
            )
        )
        if row is None:
            return False
        row.released_at = datetime.now(timezone.utc)
        row.released_by_user_id = released_by_user_id
        await session.commit()
        return True

    if db is not None:
        return await _write(db)
    async with _session_maker()() as session:
        return await _write(session)
