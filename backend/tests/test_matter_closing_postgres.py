"""Closing a matter must not strand money that belongs to somebody else.

Closing was previously two assignments behind an endpoint no screen called,
so nothing checked what closing would bury. The classification is the whole
point of these tests: unbilled billable work and a held trust balance block
outright, everything else is shown and acknowledged.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Expense, Invoice, TimeEntry
from app.models.contact import Contact
from app.models.plugin import Matter
from app.models.task import Task
from app.models.trust_accounting import TrustAccount
from app.services.matter_closing import close_readiness


def check(readiness, key):
    return next(row for row in readiness["checks"] if row["key"] == key)


async def make_matter(db_session: AsyncSession, user) -> Matter:
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=f"closing-{uuid.uuid4().hex[:8]}",
        matter_name="Smith",
        client_contact_id=contact.id,
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    return matter


def time_entry(matter, user, **overrides):
    values = dict(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        user_id=user.id,
        description="Drafting",
        hours=Decimal("2.00"),
        hourly_rate=Decimal("250.00"),
        amount=Decimal("500.00"),
        date=date(2026, 9, 1),
        is_billable=True,
        invoice_id=None,
    )
    values.update(overrides)
    return TimeEntry(**values)


@pytest.mark.asyncio
async def test_a_quiet_matter_is_ready_to_close(db_session, test_user):
    matter = await make_matter(db_session, test_user)

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    assert readiness["can_close"] is True
    assert readiness["blocking_count"] == 0 and readiness["warning_count"] == 0
    assert readiness["already_closed"] is False
    assert all(row["clear"] for row in readiness["checks"])


@pytest.mark.asyncio
async def test_unbilled_billable_work_blocks_the_close(db_session, test_user):
    matter = await make_matter(db_session, test_user)
    db_session.add(time_entry(matter, test_user))
    db_session.add(
        Expense(
            id=uuid.uuid4(),
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            user_id=test_user.id,
            description="Filing fee",
            amount=Decimal("75.00"),
            date=date(2026, 9, 2),
            is_billable=True,
            invoice_id=None,
        )
    )
    await db_session.commit()

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    unbilled = check(readiness, "unbilled_work")
    assert readiness["can_close"] is False
    assert unbilled["blocking"] is True and unbilled["clear"] is False
    # Time and client expenses are counted together: both are money the client
    # was never asked for.
    assert unbilled["count"] == 2
    assert Decimal(unbilled["amount"]) == Decimal("575.00")


@pytest.mark.asyncio
async def test_invoiced_and_non_billable_work_does_not_block(db_session, test_user):
    matter = await make_matter(db_session, test_user)
    invoice = Invoice(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        invoice_number="INV-0001",
        issue_date=date(2026, 9, 3),
        due_date=date(2026, 10, 3),
        subtotal=Decimal("500.00"),
        total=Decimal("500.00"),
        created_by=test_user.id,
    )
    db_session.add(invoice)
    await db_session.flush()
    db_session.add(time_entry(matter, test_user, invoice_id=invoice.id))
    db_session.add(time_entry(matter, test_user, is_billable=False))
    await db_session.commit()

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    unbilled = check(readiness, "unbilled_work")
    assert unbilled["clear"] is True and unbilled["count"] == 0
    assert readiness["can_close"] is True


@pytest.mark.asyncio
async def test_a_held_trust_balance_blocks_the_close(db_session, test_user):
    matter = await make_matter(db_session, test_user)
    db_session.add(
        TrustAccount(
            id=uuid.uuid4(),
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            account_name="Smith trust",
            current_balance=Decimal("1200.00"),
        )
    )
    await db_session.commit()

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    trust = check(readiness, "trust_balance")
    assert readiness["can_close"] is False
    assert trust["blocking"] is True and trust["clear"] is False
    assert Decimal(trust["amount"]) == Decimal("1200.00")


@pytest.mark.asyncio
async def test_a_zero_trust_balance_is_clear(db_session, test_user):
    matter = await make_matter(db_session, test_user)
    db_session.add(
        TrustAccount(
            id=uuid.uuid4(),
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            account_name="Smith trust",
            current_balance=Decimal("0.00"),
        )
    )
    await db_session.commit()

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    assert check(readiness, "trust_balance")["clear"] is True
    assert readiness["can_close"] is True


@pytest.mark.asyncio
async def test_open_tasks_warn_without_blocking(db_session, test_user):
    matter = await make_matter(db_session, test_user)
    db_session.add(
        Task(
            id=uuid.uuid4(),
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            title="Chase the client",
            task_type="follow_up",
            status="pending",
            created_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    tasks = check(readiness, "open_tasks")
    assert tasks["blocking"] is False and tasks["clear"] is False
    assert tasks["count"] == 1
    # A firm closes over open tasks all the time; it is told, not stopped.
    assert readiness["can_close"] is True and readiness["warning_count"] == 1


@pytest.mark.asyncio
async def test_another_matters_money_is_never_counted(db_session, test_user):
    """Every check is scoped to its own matter, not the tenant."""
    matter = await make_matter(db_session, test_user)
    other = await make_matter(db_session, test_user)
    db_session.add(time_entry(other, test_user))
    db_session.add(
        TrustAccount(
            id=uuid.uuid4(),
            tenant_id=other.tenant_id,
            matter_id=other.id,
            account_name="Other trust",
            current_balance=Decimal("900.00"),
        )
    )
    await db_session.commit()

    readiness = await close_readiness(db_session, test_user.tenant_id, matter)

    assert readiness["can_close"] is True
    assert check(readiness, "unbilled_work")["count"] == 0
    assert Decimal(check(readiness, "trust_balance")["amount"]) == Decimal("0.00")
