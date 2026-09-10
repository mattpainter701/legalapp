"""Human-readable matter numbers: generation, uniqueness, and immutability.

The matter number is the identifier a firm prints on an engagement letter and
reads over the phone, so the properties worth testing are the ones a client
would notice: it exists from creation, it is unique within the firm, it does
not change, and it resolves back to the matter it names -- and only inside the
firm that issued it.
"""

import asyncio
import importlib.util
import uuid
from pathlib import Path

import pytest

from app.models.plugin import Matter
from app.models.tenant import Tenant
from app.services.matter_number import (
    assign_matter_number,
    derive_tenant_prefix,
    ensure_tenant_prefix,
    format_matter_number,
    generate_matter_number,
    looks_like_matter_number,
    normalize_matter_number,
)


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_prefix_prefers_the_name_on_the_letterhead():
    # company_name is the firm's public name; name is often an internal label.
    assert derive_tenant_prefix("Internal Label", "Smith & Associates") == "SMIT"
    assert derive_tenant_prefix("Smith & Associates", None) == "SMIT"


def test_prefix_drops_punctuation_and_uppercases():
    assert derive_tenant_prefix(None, "O'Brien & Co.") == "OBRI"
    assert derive_tenant_prefix(None, "  jones law  ") == "JONE"


def test_prefix_pads_a_short_name():
    # A two-letter firm still needs a four-character prefix.
    assert derive_tenant_prefix(None, "Ay") == "AYXX"


def test_prefix_falls_back_when_a_name_carries_no_letters():
    assert derive_tenant_prefix("123", "456 Holdings LLC") == "HOLD"
    assert derive_tenant_prefix("123", "456") == "MTTR"
    assert derive_tenant_prefix(None, None) == "MTTR"


def test_format_pads_to_four_digits_without_ever_truncating():
    assert format_matter_number("SMIT", 1) == "SMIT0001"
    assert format_matter_number("SMIT", 9999) == "SMIT9999"
    # Past 9999 the number gets longer rather than wrapping -- dropping a digit
    # would collide with an earlier matter.
    assert format_matter_number("SMIT", 10000) == "SMIT10000"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SMIT0001", "SMIT0001"),
        ("smit0001", "SMIT0001"),
        ("smit-0001", "SMIT0001"),
        (" SMIT 0001 ", "SMIT0001"),
        ("SMIT_0001", "SMIT0001"),
        ("SMI20001", "SMI20001"),
        ("SMIT12345", "SMIT12345"),
    ],
)
def test_normalize_accepts_the_shapes_people_type(raw, expected):
    assert normalize_matter_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "SMIT",
        "SMIT001",
        "0001SMIT",
        "1MIT0001",
        "my",
        "stats",
        "field-options",
        "550e8400-e29b-41d4-a716-446655440000",
        # Hyphen-stripping must not let a digit-heavy UUID pass as a number.
        "abcd8400-1234-4111-8123-446655440000",
    ],
)
def test_normalize_rejects_what_is_not_a_matter_number(raw):
    assert normalize_matter_number(raw) is None
    assert looks_like_matter_number(raw) is False


# ── Generation against the database ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_matter_number_starts_the_series(db_session, test_tenant):
    number, sequence = await generate_matter_number(db_session, test_tenant.id)
    assert number == "TEST0001"
    assert sequence == 1


@pytest.mark.asyncio
async def test_numbers_increment_and_never_repeat(db_session, test_tenant):
    issued = [
        (await generate_matter_number(db_session, test_tenant.id))[0] for _ in range(5)
    ]
    assert issued == [
        "TEST0001",
        "TEST0002",
        "TEST0003",
        "TEST0004",
        "TEST0005",
    ]
    assert len(set(issued)) == 5


@pytest.mark.asyncio
async def test_sequences_are_scoped_per_tenant(db_session, test_tenant):
    other = Tenant(
        id=uuid.uuid4(),
        name="Jones Law",
        domain="joneslaw.example",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()

    first_for_test, _ = await generate_matter_number(db_session, test_tenant.id)
    first_for_other, seq_for_other = await generate_matter_number(db_session, other.id)

    # Each firm counts from one, under its own prefix.
    assert first_for_test == "TEST0001"
    assert first_for_other == "JONE0001"
    assert seq_for_other == 1


@pytest.mark.asyncio
async def test_a_colliding_prefix_is_varied_rather_than_duplicated(
    db_session, test_tenant
):
    # Two firms deriving the same stem cannot share a prefix -- the column is
    # unique, and two firms' SMIT0001 would be the same string.
    test_tenant.matter_prefix = "SMIT"
    await db_session.flush()

    twin = Tenant(
        id=uuid.uuid4(),
        name="Smith Legal",
        domain="smithlegal.example",
        company_name="Smith Legal",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(twin)
    await db_session.flush()

    prefix = await ensure_tenant_prefix(db_session, twin)
    assert prefix == "SMI2"
    assert prefix != test_tenant.matter_prefix


@pytest.mark.asyncio
async def test_prefix_is_derived_once_and_then_reused(db_session, test_tenant):
    test_tenant.matter_prefix = None
    await db_session.flush()

    first = await ensure_tenant_prefix(db_session, test_tenant)
    # Renaming the firm must not renumber it -- the prefix is already claimed.
    test_tenant.name = "Completely Different Firm"
    second = await ensure_tenant_prefix(db_session, test_tenant)
    assert first == second == "TEST"


@pytest.mark.asyncio
async def test_generate_rejects_an_unknown_tenant(db_session):
    with pytest.raises(ValueError):
        await generate_matter_number(db_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_assign_is_idempotent_so_a_matter_is_never_renumbered(
    db_session, test_tenant, test_user
):
    matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="already-numbered",
        matter_name="Already numbered",
    )
    await assign_matter_number(db_session, matter)
    original = matter.matter_number

    await assign_matter_number(db_session, matter)
    assert matter.matter_number == original


@pytest.mark.asyncio
async def test_concurrent_creations_do_not_share_a_number(
    db_session, test_tenant, test_engine
):
    """Two sessions allocating at once must get different numbers.

    The counter is incremented by ``UPDATE ... RETURNING``, which holds a row
    lock on the tenant for the rest of the transaction, so the second
    allocation waits rather than reading a stale value.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    tenant_id = test_tenant.id
    await db_session.commit()

    async def allocate():
        async with factory() as session:
            number, _ = await generate_matter_number(session, tenant_id)
            await session.commit()
            return number

    first, second = await asyncio.gather(allocate(), allocate())
    assert first != second
    assert {first, second} == {"TEST0001", "TEST0002"}


# ── The migration ────────────────────────────────────────────────────────────


def _migration_source() -> tuple[object, str]:
    path = (
        Path(__file__).resolve().parents[1] / "migrations/versions/170_matter_number.py"
    )
    spec = importlib.util.spec_from_file_location("matter_number_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text()


def test_migration_extends_the_single_head():
    module, _ = _migration_source()
    assert module.revision == "170_matter_number"
    assert module.down_revision == "169_automation_services"


def test_migration_backfills_and_guards():
    _, source = _migration_source()
    # Existing matters are numbered in creation order, so the oldest is 0001.
    assert "ORDER BY m.created_at, m.id" in source
    # The counter continues the backfilled series instead of restarting at 1.
    assert "max(m.matter_number_seq)" in source
    # Uniqueness and immutability hold even against a direct UPDATE.
    assert "uq_matters_tenant_matter_number" in source
    assert "Matter number is immutable once assigned" in source
    assert "BEFORE UPDATE ON matters" in source


def test_migration_is_reversible():
    _, source = _migration_source()
    assert "def downgrade()" in source
    for dropped in (
        "trg_matter_number_immutable",
        "guard_matter_number_immutable",
        "matter_number_seq",
        "matter_prefix",
        "matter_sequence_counter",
    ):
        assert dropped in source.split("def downgrade()")[1]
