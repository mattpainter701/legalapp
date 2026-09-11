"""Follow-up tasks for anything a matter is waiting on a client for.

Intake grew the only version of this and nothing past intake could borrow it,
so a signature sent in month four had no deadline and nothing chased it. The
identity rule is what these tests pin: a task's id is derived from the thing it
chases, so "ensure" is idempotent from any number of passes and "close" can
find the task again without storing a link.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.contact import Contact
from app.models.plugin import Matter
from app.models.task import Task
from app.models.user import User
from app.services.matter_followups import (
    close_followup_task,
    ensure_followup_task,
    followup_task_id,
)

DUE = datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def matter(db_session, test_tenant, test_user):
    row = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"followup-{uuid.uuid4().hex[:8]}",
        matter_name="Okafor purchase",
        status="open",
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _ensure(db_session, matter, user, **overrides):
    values = dict(
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        namespace=matter.id,
        kind="due:fee_agreement",
        title="Fee agreement due from client",
        due=DUE,
        owner_id=user.id,
        created_by=user.id,
    )
    values.update(overrides)
    return await ensure_followup_task(db_session, **values)


@pytest.mark.asyncio
async def test_the_task_id_is_derived_from_what_it_chases(
    db_session, matter, test_user
):
    task = await _ensure(db_session, matter, test_user)
    await db_session.commit()

    assert task.id == followup_task_id(matter.id, "due:fee_agreement")
    assert task.status == "pending" and task.task_type == "follow_up"
    assert task.priority == "high"
    assert task.external_ref == f"matter:{matter.id}:due:fee_agreement"


@pytest.mark.asyncio
async def test_ensuring_twice_raises_one_task(db_session, matter, test_user):
    first = await _ensure(db_session, matter, test_user)
    await db_session.commit()
    second = await _ensure(db_session, matter, test_user, title="A different title")
    await db_session.commit()

    assert first.id == second.id
    # The second pass returns the existing task untouched rather than editing it.
    assert second.title == "Fee agreement due from client"
    count = await db_session.scalar(
        select(Task.id).where(Task.id == first.id).with_only_columns(Task.id)
    )
    assert count == first.id


@pytest.mark.asyncio
async def test_different_kinds_are_different_tasks(db_session, matter, test_user):
    fee = await _ensure(db_session, matter, test_user)
    questionnaire = await _ensure(
        db_session, matter, test_user, kind="due:questionnaire"
    )
    await db_session.commit()

    assert fee.id != questionnaire.id


@pytest.mark.asyncio
async def test_the_deadline_lands_in_the_client_timezone(
    db_session, matter, test_user
):
    task = await _ensure(
        db_session,
        matter,
        test_user,
        kind="due:tz",
        timezone_name="America/Chicago",
    )
    await db_session.commit()

    # 22:00 UTC is 5pm in Chicago: the same day, not the next one.
    assert task.due_date.isoformat() == "2026-09-18"
    assert task.due_time.hour == 17


@pytest.mark.asyncio
async def test_an_unresolvable_timezone_still_produces_a_deadline(
    db_session, matter, test_user
):
    task = await _ensure(
        db_session, matter, test_user, kind="due:bad-tz", timezone_name="Mars/Olympus"
    )
    await db_session.commit()

    # UTC is wrong by hours; absent is wrong by the whole task.
    assert task.due_date.isoformat() == "2026-09-18"
    assert task.due_time.hour == 22


@pytest.mark.asyncio
async def test_the_owner_is_assigned_when_they_can_open_the_matter(
    db_session, matter, test_user
):
    task = await _ensure(db_session, matter, test_user, kind="due:assigned")
    await db_session.commit()

    assert task.assigned_to_user_id == test_user.id


@pytest.mark.asyncio
async def test_an_unknown_owner_leaves_the_task_unassigned(
    db_session, matter, test_user
):
    task = await _ensure(
        db_session, matter, test_user, kind="due:ghost", owner_id=uuid.uuid4()
    )
    await db_session.commit()

    # Better a task nobody owns than a task assigned to somebody who cannot
    # open the matter it belongs to.
    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_no_owner_at_all_is_allowed(db_session, matter, test_user):
    task = await _ensure(
        db_session, matter, test_user, kind="due:nobody", owner_id=None
    )
    await db_session.commit()

    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_a_deactivated_owner_is_not_assigned(
    db_session, matter, test_tenant, test_user
):
    former = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"former-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        full_name="Former Paralegal",
        role="admin",
        is_active=False,
    )
    db_session.add(former)
    await db_session.commit()

    task = await _ensure(
        db_session, matter, test_user, kind="due:former", owner_id=former.id
    )
    await db_session.commit()

    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_a_contact_is_carried_onto_the_task(
    db_session, matter, test_tenant, test_user
):
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Ada",
        last_name="Okafor",
        email="ada@example.com",
    )
    db_session.add(contact)
    await db_session.commit()

    task = await _ensure(
        db_session, matter, test_user, kind="due:contact", contact_id=contact.id
    )
    await db_session.commit()

    assert task.contact_id == contact.id


@pytest.mark.asyncio
async def test_closing_cancels_the_task_it_finds_by_identity(
    db_session, matter, test_user
):
    task = await _ensure(db_session, matter, test_user, kind="due:closes")
    await db_session.commit()

    closed = await close_followup_task(
        db_session,
        tenant_id=matter.tenant_id,
        namespace=matter.id,
        kind="due:closes",
        reason="The client signed it.",
        actor_user_id=test_user.id,
    )
    await db_session.commit()

    assert closed is not None and closed.id == task.id
    assert closed.status == "cancelled"


@pytest.mark.asyncio
async def test_closing_something_that_was_never_raised_is_a_no_op(
    db_session, matter, test_user
):
    closed = await close_followup_task(
        db_session,
        tenant_id=matter.tenant_id,
        namespace=matter.id,
        kind="due:never-existed",
        reason="Nothing to cancel.",
    )

    assert closed is None


@pytest.mark.asyncio
async def test_closing_twice_only_closes_once(db_session, matter, test_user):
    await _ensure(db_session, matter, test_user, kind="due:twice")
    await db_session.commit()

    first = await close_followup_task(
        db_session,
        tenant_id=matter.tenant_id,
        namespace=matter.id,
        kind="due:twice",
        reason="Signed.",
    )
    await db_session.commit()
    second = await close_followup_task(
        db_session,
        tenant_id=matter.tenant_id,
        namespace=matter.id,
        kind="due:twice",
        reason="Signed again.",
    )

    assert first is not None and second is None


@pytest.mark.asyncio
async def test_a_description_is_supplied_when_none_is_given(
    db_session, matter, test_user
):
    task = await _ensure(
        db_session, matter, test_user, kind="due:described", description=None
    )
    await db_session.commit()

    assert DUE.isoformat() in task.description
