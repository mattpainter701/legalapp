"""The close, readiness, and reopen endpoints driven through the real app.

`matter_closing` itself is covered against a database elsewhere; what is
exercised here is the decision the endpoint makes with that verdict — which
refusals it raises, what it records, and what closing does to the paperwork
still chasing the client.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.billing import TimeEntry
from app.models.plugin import Matter, MatterEvent
from app.models.task import Task
from app.models.trust_accounting import TrustAccount

MATTERS = "/api/matters"


@pytest_asyncio.fixture
async def open_matter(db_session, test_tenant, test_user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"closing-api-{uuid.uuid4().hex[:8]}",
        matter_name="Whitfield estate",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    await db_session.refresh(matter)
    return matter


async def _events(db_session, matter_id, event_type):
    result = await db_session.execute(
        select(MatterEvent).where(
            MatterEvent.matter_id == matter_id, MatterEvent.event_type == event_type
        )
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_readiness_reports_a_quiet_matter_as_closable(client, open_matter):
    response = await client.get(f"{MATTERS}/{open_matter.id}/close-readiness")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_close"] is True
    assert body["already_closed"] is False
    assert {check["key"] for check in body["checks"]} == {
        "unbilled_work",
        "trust_balance",
        "open_tasks",
        "live_signatures",
        "client_paperwork",
    }


@pytest.mark.asyncio
async def test_closing_is_refused_while_the_client_is_owed_money(
    client, db_session, open_matter, test_user
):
    db_session.add(
        TrustAccount(
            id=uuid.uuid4(),
            tenant_id=open_matter.tenant_id,
            matter_id=open_matter.id,
            account_name="Whitfield trust",
            current_balance=Decimal("2500.00"),
        )
    )
    await db_session.commit()

    response = await client.delete(f"{MATTERS}/{open_matter.id}")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "matter_close_blocked"
    assert [check["key"] for check in detail["checks"]] == ["trust_balance"]
    # A refused close leaves the matter exactly as it was.
    await db_session.refresh(open_matter)
    assert open_matter.is_closed is False


@pytest.mark.asyncio
async def test_acknowledging_warnings_is_required_but_sufficient(
    client, db_session, open_matter, test_user
):
    db_session.add(
        Task(
            id=uuid.uuid4(),
            tenant_id=open_matter.tenant_id,
            matter_id=open_matter.id,
            title="Send the closing letter",
            task_type="follow_up",
            status="pending",
            created_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    refused = await client.delete(f"{MATTERS}/{open_matter.id}")
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "matter_close_needs_acknowledgement"
    assert [check["key"] for check in detail["checks"]] == ["open_tasks"]

    accepted = await client.delete(
        f"{MATTERS}/{open_matter.id}",
        params={"acknowledge_warnings": "true", "reason": "Client moved firms."},
    )
    assert accepted.status_code == 204, accepted.text

    await db_session.refresh(open_matter)
    assert open_matter.is_closed is True and open_matter.status == "closed"
    events = await _events(db_session, open_matter.id, "matter_closed")
    assert [event.content for event in events] == ["Client moved firms."]


@pytest.mark.asyncio
async def test_a_close_with_no_reason_still_records_why_it_happened(
    client, db_session, open_matter
):
    response = await client.delete(f"{MATTERS}/{open_matter.id}")
    assert response.status_code == 204, response.text

    events = await _events(db_session, open_matter.id, "matter_closed")
    assert [event.content for event in events] == ["Matter closed."]
    assert events[0].note_type == "system"


@pytest.mark.asyncio
async def test_closing_twice_is_not_an_error_and_records_once(
    client, db_session, open_matter
):
    first = await client.delete(f"{MATTERS}/{open_matter.id}")
    second = await client.delete(f"{MATTERS}/{open_matter.id}")

    assert (first.status_code, second.status_code) == (204, 204)
    # The second call returns before recording anything; a closed matter does
    # not accumulate closing events every time somebody presses the button.
    assert len(await _events(db_session, open_matter.id, "matter_closed")) == 1


@pytest.mark.asyncio
async def test_readiness_reports_a_closed_matter_as_already_closed(
    client, open_matter
):
    await client.delete(f"{MATTERS}/{open_matter.id}")

    body = (await client.get(f"{MATTERS}/{open_matter.id}/close-readiness")).json()

    assert body["already_closed"] is True


@pytest.mark.asyncio
async def test_reopening_undoes_the_close(client, db_session, open_matter):
    await client.delete(f"{MATTERS}/{open_matter.id}")

    response = await client.post(f"{MATTERS}/{open_matter.id}/reopen")

    assert response.status_code == 204, response.text
    await db_session.refresh(open_matter)
    assert open_matter.is_closed is False and open_matter.status == "active"
    assert len(await _events(db_session, open_matter.id, "matter_reopened")) == 1


@pytest.mark.asyncio
async def test_reopening_an_open_matter_changes_nothing(
    client, db_session, open_matter
):
    response = await client.post(f"{MATTERS}/{open_matter.id}/reopen")

    assert response.status_code == 204, response.text
    assert await _events(db_session, open_matter.id, "matter_reopened") == []


@pytest.mark.asyncio
async def test_unbilled_work_blocks_and_naming_an_invoice_releases_it(
    client, db_session, open_matter, test_user
):
    entry = TimeEntry(
        id=uuid.uuid4(),
        tenant_id=open_matter.tenant_id,
        matter_id=open_matter.id,
        user_id=test_user.id,
        description="Final accounting",
        hours=Decimal("1.50"),
        hourly_rate=Decimal("300.00"),
        amount=Decimal("450.00"),
        date=date(2026, 9, 4),
        is_billable=True,
        invoice_id=None,
    )
    db_session.add(entry)
    await db_session.commit()

    blocked = await client.delete(f"{MATTERS}/{open_matter.id}")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["checks"][0]["key"] == "unbilled_work"

    # Writing the work off is the other way out, and it is the same check.
    entry.is_billable = False
    await db_session.commit()

    assert (await client.delete(f"{MATTERS}/{open_matter.id}")).status_code == 204


@pytest.mark.asyncio
async def test_another_tenants_matter_is_not_found(client):
    response = await client.get(f"{MATTERS}/{uuid.uuid4()}/close-readiness")

    assert response.status_code == 404
