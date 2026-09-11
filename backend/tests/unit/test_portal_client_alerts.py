"""The nudge that tells a client something is waiting in the portal.

The property worth pinning is what the email does NOT contain. The portal is
where privileged material belongs, so the nudge carries a matter name and a
link and nothing else; an email reproducing the document or the message body
would quietly create a second, less protected copy of it.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services import portal_client_alerts
from app.services.portal_client_alerts import (
    new_message_headline,
    notify_client_portal_update,
    shared_document_headline,
)


class Db:
    def __init__(self, contact=None):
        self._contact = contact

    async def scalar(self, _statement):
        return self._contact


def matter(**overrides):
    values = dict(
        id=uuid.uuid4(),
        matter_name="Alvarez v. Brightline",
        client_contact_id=uuid.uuid4(),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def sent(monkeypatch):
    calls = {}

    async def send(_db, **kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(portal_client_alerts, "send_client_email", send)
    return calls


@pytest.mark.asyncio
async def test_the_nudge_carries_a_link_and_never_the_content(sent):
    secret = "The settlement offer is $250,000."

    delivered = await notify_client_portal_update(
        Db(SimpleNamespace(email="jane@example.com")),
        tenant_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        matter=matter(),
        subject="New message about Alvarez v. Brightline",
        headline=new_message_headline(matter()),
    )

    assert delivered is True
    assert sent["to"] == ["jane@example.com"]
    assert secret not in sent["html_body"] and secret not in sent["text_body"]
    assert "/portal/client/matter" in sent["text_body"]
    assert "Alvarez v. Brightline" in sent["text_body"]


@pytest.mark.asyncio
async def test_a_matter_with_no_client_contact_sends_nothing(sent):
    delivered = await notify_client_portal_update(
        Db(),
        tenant_id=uuid.uuid4(),
        actor_user_id=None,
        matter=matter(client_contact_id=None),
        subject="Anything",
        headline="Anything",
    )

    assert delivered is False and sent == {}


@pytest.mark.asyncio
async def test_a_contact_with_no_address_sends_nothing(sent):
    delivered = await notify_client_portal_update(
        Db(SimpleNamespace(email="   ")),
        tenant_id=uuid.uuid4(),
        actor_user_id=None,
        matter=matter(),
        subject="Anything",
        headline="Anything",
    )

    assert delivered is False and sent == {}


@pytest.mark.asyncio
async def test_a_delivery_failure_is_swallowed(monkeypatch):
    """Whatever prompted the nudge is already durable; the email is a courtesy."""

    async def explode(_db, **_kwargs):
        raise RuntimeError("the provider is down")

    monkeypatch.setattr(portal_client_alerts, "send_client_email", explode)

    delivered = await notify_client_portal_update(
        Db(SimpleNamespace(email="jane@example.com")),
        tenant_id=uuid.uuid4(),
        actor_user_id=None,
        matter=matter(),
        subject="New message",
        headline="New message",
    )

    assert delivered is False


@pytest.mark.asyncio
async def test_a_headline_with_markup_is_escaped(sent):
    await notify_client_portal_update(
        Db(SimpleNamespace(email="jane@example.com")),
        tenant_id=uuid.uuid4(),
        actor_user_id=None,
        matter=matter(),
        subject="New document",
        headline=shared_document_headline(matter(), "<script>alert(1)</script>.pdf"),
    )

    assert "<script>" not in sent["html_body"]
    assert "&lt;script&gt;" in sent["html_body"]


def test_the_headlines_name_the_matter_and_fall_back():
    assert "Alvarez v. Brightline" in new_message_headline(matter())
    assert "your matter" in new_message_headline(matter(matter_name=None))
    shared = shared_document_headline(matter(), "Order.pdf")
    assert "Order.pdf" in shared and "Alvarez v. Brightline" in shared
    assert "your matter" in shared_document_headline(matter(matter_name=""), "x.pdf")
