"""Human-readable matter numbers.

A matter number is ``{PREFIX}{SEQUENCE}`` — a four-character tenant prefix
followed by a zero-padded per-tenant sequence, e.g. ``SMIT0001``.  It is the
identifier a firm says on the phone, prints on an engagement letter, and pastes
into an email.  The UUID stays the primary key and the storage key; the matter
number is the one people read.

Two invariants make it safe to quote:

* **Unique within a tenant.**  ``uq_matters_tenant_matter_number`` enforces it
  in the database; the sequence counter makes a collision impossible in the
  first place.
* **Immutable.**  Once assigned, a matter keeps its number even if the matter
  is renamed or the tenant prefix is later changed.  Nothing in the API surface
  can set or change it — it is assigned here and nowhere else.

The sequence is allocated by an ``UPDATE ... RETURNING`` on the tenant row,
which takes a row lock for the remainder of the transaction.  Two concurrent
matter creations for one tenant therefore serialize on that row and receive
consecutive numbers rather than the same one.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant

#: Width the sequence is padded to.  A tenant past ``9999`` matters simply gets
#: a longer number (``SMIT10000``) rather than a wrapped or truncated one.
SEQUENCE_WIDTH = 4

#: Used when a tenant name carries no letters at all (e.g. "123 Holdings").
FALLBACK_PREFIX = "MTTR"

#: Filler for a tenant name shorter than the prefix ("Ay" -> "AYXX").
_PAD_CHAR = "X"

_NON_ALPHA = re.compile(r"[^A-Za-z]")

#: A four-character prefix — always letter-initial, possibly carrying collision
#: digits in its tail — then at least four digits of sequence.  Anchored, so it
#: can be used to tell a matter number from a UUID when routing.
_MATTER_NUMBER = re.compile(r"^[A-Z][A-Z0-9]{3}[0-9]{4,}$")


def format_matter_number(prefix: str, sequence: int) -> str:
    """Render ``prefix`` + ``sequence`` as the display string.

    Padding never truncates: a sequence wider than ``SEQUENCE_WIDTH`` keeps all
    of its digits, because dropping one would collide with another matter.
    """
    return f"{prefix}{sequence:0{SEQUENCE_WIDTH}d}"


def normalize_matter_number(value: str | None) -> str | None:
    """Fold user-typed input to canonical form, or ``None`` if it cannot be one.

    Accepts the shapes a person actually types — lowercase, padded with spaces,
    hyphenated (``smit-0001``) — because this is what arrives from a URL bar or
    a phone call.  Anything that is not shaped like a matter number returns
    ``None`` so the caller can fall through to UUID handling.
    """
    if not value:
        return None
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        pass
    else:
        # A UUID is never a matter number, and stripping its hyphens must not
        # let a digit-heavy one masquerade as one.
        return None
    candidate = re.sub(r"[\s\-_]", "", str(value)).upper()
    if not _MATTER_NUMBER.match(candidate):
        return None
    return candidate


def looks_like_matter_number(value: str | None) -> bool:
    """True when ``value`` should be resolved as a matter number, not a UUID."""
    return normalize_matter_number(value) is not None


def derive_tenant_prefix(name: str | None, company_name: str | None = None) -> str:
    """Derive a tenant's four-character prefix from its display names.

    The firm's ``company_name`` wins when present — it is the name on the
    letterhead, and the ``name`` is often an internal label.  Non-letters are
    dropped rather than transliterated, so "O'Brien & Associates" yields
    ``OBRI``.
    """
    for source in (company_name, name):
        letters = _NON_ALPHA.sub("", source or "")
        if letters:
            return letters[:4].upper().ljust(4, _PAD_CHAR)
    return FALLBACK_PREFIX


async def _claim_unique_prefix(db: AsyncSession, base: str) -> str:
    """Return ``base``, or the first free variant of it.

    Two firms named "Smith" would both derive ``SMIT``.  The prefix column is
    four characters wide, so a collision cannot append a digit — it replaces
    the tail: ``SMIT`` -> ``SMI2`` -> ``SMI3`` ... ``SM10`` ... ``S100``.
    """
    taken = set(
        (
            await db.execute(
                select(Tenant.matter_prefix).where(Tenant.matter_prefix.isnot(None))
            )
        )
        .scalars()
        .all()
    )
    if base not in taken:
        return base
    for suffix in range(2, 10000):
        tail = str(suffix)
        candidate = (base[: 4 - len(tail)] + tail)[:4]
        if candidate not in taken:
            return candidate
    # 9998 firms sharing one four-letter stem is not a real scenario, but the
    # caller must still get a usable prefix rather than an exception.
    return FALLBACK_PREFIX


async def ensure_tenant_prefix(db: AsyncSession, tenant: Tenant) -> str:
    """Return the tenant's prefix, deriving and persisting one on first use.

    Tenants created before this feature — and any created without going through
    onboarding — carry no prefix until their first matter needs one.
    """
    if tenant.matter_prefix:
        return tenant.matter_prefix
    prefix = await _claim_unique_prefix(
        db, derive_tenant_prefix(tenant.name, tenant.company_name)
    )
    tenant.matter_prefix = prefix
    await db.flush()
    return prefix


async def generate_matter_number(
    db: AsyncSession, tenant_id: uuid.UUID | str
) -> tuple[str, int]:
    """Allocate the next matter number for ``tenant_id``.

    Returns ``(matter_number, sequence)``.  The counter increment and the
    caller's matter INSERT belong to the same transaction, so a rolled-back
    creation burns a number rather than handing it to two matters — gaps are
    acceptable, duplicates are not.
    """
    tenant_uuid = uuid.UUID(str(tenant_id))
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_uuid))
    ).scalar_one_or_none()
    if tenant is None:
        raise ValueError(f"Unknown tenant {tenant_id}")

    prefix = await ensure_tenant_prefix(db, tenant)

    # UPDATE ... RETURNING locks the tenant row until this transaction ends, so
    # a concurrent creation for the same tenant waits here instead of reading a
    # stale counter and duplicating a number.
    sequence = (
        await db.execute(
            update(Tenant)
            .where(Tenant.id == tenant_uuid)
            .values(matter_sequence_counter=Tenant.matter_sequence_counter + 1)
            .returning(Tenant.matter_sequence_counter)
        )
    ).scalar_one()

    return format_matter_number(prefix, sequence), sequence


async def assign_matter_number(db: AsyncSession, matter) -> None:
    """Stamp a freshly constructed, not-yet-flushed ``matter`` with its number.

    Call this at every matter creation path.  It is a no-op for a matter that
    already carries a number, so a caller that re-runs it cannot renumber one.
    """
    if getattr(matter, "matter_number", None):
        return
    number, sequence = await generate_matter_number(db, matter.tenant_id)
    matter.matter_number = number
    matter.matter_number_seq = sequence
