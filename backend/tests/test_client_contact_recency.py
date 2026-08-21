import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.contact import Contact
from app.routers.communications import _record_client_contact


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_related_contact_activity_advances_the_canonical_account():
    tenant_id = uuid.uuid4()
    account = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_type="organization",
        contact_type="client",
        organization_name="Northstar Analytics",
    )
    related = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_type="person",
        contact_type="client_contact",
        client_account_id=account.id,
        first_name="Avery",
        last_name="Nguyen",
    )
    occurred_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(related), _Result(account)])
    )

    await _record_client_contact(db, tenant_id, related.id, occurred_at)

    assert related.last_contacted_at == occurred_at
    assert account.last_contacted_at == occurred_at
