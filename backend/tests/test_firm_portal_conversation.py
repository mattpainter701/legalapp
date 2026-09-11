"""The firm's half of the client portal thread.

The client half shipped first: a client could write in and the team got an
alert. Nothing could write the firm's half, so replies left the matter through
personal mailboxes. These endpoints close that loop, and what matters about
them is the unread arithmetic and the fact that an alert cannot lose a message.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.communication_log import CommunicationLog
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter

FIRM = "/api/matters"


@pytest_asyncio.fixture
async def portal_matter(db_session, test_tenant, test_user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"firm-portal-{uuid.uuid4().hex[:8]}",
        matter_name="Alvarez v. Brightline",
        status="open",
        portal_enabled=True,
    )
    db_session.add(matter)
    db_session.add(
        MatterAssignment(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=matter.id,
            user_id=test_user.id,
            role="lead",
            is_primary=True,
        )
    )
    await db_session.commit()
    await db_session.refresh(matter)
    return matter


def _message(matter, *, direction, body, minutes_ago):
    return CommunicationLog(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        direction=direction,
        channel="portal",
        status="sent",
        subject="About your case",
        body=body,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


@pytest.mark.asyncio
async def test_the_thread_reads_oldest_first_and_counts_what_is_unread(
    client, db_session, portal_matter
):
    db_session.add(_message(portal_matter, direction="inbound", body="First", minutes_ago=30))
    db_session.add(_message(portal_matter, direction="outbound", body="Ours", minutes_ago=20))
    db_session.add(_message(portal_matter, direction="inbound", body="Second", minutes_ago=10))
    await db_session.commit()

    body = (await client.get(f"{FIRM}/{portal_matter.id}/portal/messages")).json()

    assert [m["body"] for m in body["messages"]] == ["First", "Ours", "Second"]
    assert body["total"] == 3 and body["has_more"] is False
    # Only what the client wrote can be unread by the firm.
    assert body["unread_count"] == 2
    assert [m["unread"] for m in body["messages"]] == [True, False, True]


@pytest.mark.asyncio
async def test_marking_read_clears_the_badge_without_touching_the_thread(
    client, db_session, portal_matter
):
    db_session.add(_message(portal_matter, direction="inbound", body="Please call", minutes_ago=5))
    await db_session.commit()

    read = await client.post(f"{FIRM}/{portal_matter.id}/portal/messages/read")
    assert read.status_code == 200, read.text
    assert read.json()["messages_seen_at"] is not None

    after = (await client.get(f"{FIRM}/{portal_matter.id}/portal/messages")).json()
    assert after["unread_count"] == 0
    assert after["total"] == 1
    assert after["messages"][0]["unread"] is False


@pytest.mark.asyncio
async def test_a_message_arriving_after_the_read_is_unread_again(
    client, db_session, portal_matter
):
    await client.post(f"{FIRM}/{portal_matter.id}/portal/messages/read")
    db_session.add(_message(portal_matter, direction="inbound", body="One more thing", minutes_ago=0))
    await db_session.commit()

    body = (await client.get(f"{FIRM}/{portal_matter.id}/portal/messages")).json()

    assert body["unread_count"] == 1


@pytest.mark.asyncio
async def test_the_firm_reply_is_stored_and_the_client_is_told(
    client, db_session, portal_matter
):
    with patch(
        "app.routers.client_portal.notify_client_portal_update", new=AsyncMock()
    ) as notify:
        response = await client.post(
            f"{FIRM}/{portal_matter.id}/portal/messages",
            json={"body": "The hearing moved to the 3rd."},
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["direction"] == "outbound"
    assert payload["subject"] == "Message from your legal team"
    notify.assert_awaited_once()

    result = await db_session.execute(
        select(CommunicationLog).where(
            CommunicationLog.matter_id == portal_matter.id,
            CommunicationLog.direction == "outbound",
        )
    )
    stored = result.scalars().all()
    assert [row.body for row in stored] == ["The hearing moved to the 3rd."]
    assert stored[0].channel == "portal" and stored[0].status == "sent"


@pytest.mark.asyncio
async def test_a_failed_alert_never_loses_the_message(
    client, db_session, portal_matter
):
    """Writing the firm's half is the durable act; the email is the courtesy."""
    with patch(
        "app.routers.client_portal.notify_client_portal_update",
        new=AsyncMock(side_effect=RuntimeError("smtp is down")),
    ):
        with pytest.raises(RuntimeError):
            await client.post(
                f"{FIRM}/{portal_matter.id}/portal/messages",
                json={"body": "Filed today."},
            )

    result = await db_session.execute(
        select(CommunicationLog).where(
            CommunicationLog.matter_id == portal_matter.id,
            CommunicationLog.direction == "outbound",
        )
    )
    # The commit happened before the alert, so the message survived it.
    assert [row.body for row in result.scalars().all()] == ["Filed today."]


@pytest.mark.asyncio
async def test_a_firm_subject_is_kept_when_one_is_given(client, portal_matter):
    with patch(
        "app.routers.client_portal.notify_client_portal_update", new=AsyncMock()
    ):
        response = await client.post(
            f"{FIRM}/{portal_matter.id}/portal/messages",
            json={"subject": "Hearing date", "body": "Moved to the 3rd."},
        )

    assert response.json()["subject"] == "Hearing date"


@pytest.mark.asyncio
async def test_the_portal_must_be_open_before_the_firm_can_write(
    client, db_session, portal_matter
):
    portal_matter.portal_enabled = False
    await db_session.commit()

    response = await client.post(
        f"{FIRM}/{portal_matter.id}/portal/messages", json={"body": "Hello"}
    )

    assert response.status_code == 409
    assert "Invite the client" in response.json()["detail"]


@pytest.mark.asyncio
async def test_paging_reports_more_to_come(client, db_session, portal_matter):
    for minute in range(5):
        db_session.add(
            _message(portal_matter, direction="inbound", body=f"m{minute}", minutes_ago=minute)
        )
    await db_session.commit()

    body = (
        await client.get(
            f"{FIRM}/{portal_matter.id}/portal/messages", params={"limit": 2}
        )
    ).json()

    assert body["total"] == 5 and body["has_more"] is True
    assert len(body["messages"]) == 2


@pytest.mark.asyncio
async def test_a_matter_that_is_not_ours_is_not_found(client):
    response = await client.get(f"{FIRM}/{uuid.uuid4()}/portal/messages")

    assert response.status_code == 404
