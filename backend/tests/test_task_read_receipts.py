"""Task read-receipt (viewed_at) and customer-contact tracking tests."""

import uuid
from datetime import date

import pytest

from app.models.contact import Contact
from app.models.task import Task
from app.models.user import User


async def _make_task(db_session, tenant_id, *, assigned_to=None, contact_id=None):
    task = Task(
        tenant_id=tenant_id,
        title="Call back caller",
        task_type="follow_up",
        status="pending",
        priority="urgent",
        due_date=date.today(),
        contact_id=contact_id,
        assigned_to_user_id=assigned_to,
        source="intake_dashboard",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


@pytest.mark.asyncio
async def test_assignee_detail_fetch_sets_viewed_at(
    client, db_session, test_tenant, test_user
):
    task = await _make_task(db_session, test_tenant.id, assigned_to=test_user.id)
    assert task.viewed_at is None

    resp = await client.get(f"/api/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.json()["viewed_at"] is not None

    # Idempotent: second fetch keeps the original timestamp.
    first_viewed = resp.json()["viewed_at"]
    resp2 = await client.get(f"/api/tasks/{task.id}")
    assert resp2.json()["viewed_at"] == first_viewed


@pytest.mark.asyncio
async def test_non_assignee_view_does_not_set_viewed_at(
    client, db_session, test_tenant, test_user
):
    other = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="assignee@testfirm.com",
        full_name="Assignee User",
        role="attorney",
        is_active=True,
    )
    db_session.add(other)
    await db_session.commit()
    task = await _make_task(db_session, test_tenant.id, assigned_to=other.id)

    # test_user (admin, not the assignee) fetching detail must not mark it read.
    resp = await client.get(f"/api/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.json()["viewed_at"] is None

    view_resp = await client.post(f"/api/tasks/{task.id}/view")
    assert view_resp.status_code == 200
    assert view_resp.json()["viewed_at"] is None


@pytest.mark.asyncio
async def test_view_endpoint_marks_assignee_read(
    client, db_session, test_tenant, test_user
):
    task = await _make_task(db_session, test_tenant.id, assigned_to=test_user.id)
    resp = await client.post(f"/api/tasks/{task.id}/view")
    assert resp.status_code == 200
    assert resp.json()["viewed_at"] is not None


@pytest.mark.asyncio
async def test_mark_customer_contacted(client, db_session, test_tenant, test_user):
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Doe",
        phone="701-555-2222",
        created_by_user_id=test_user.id,
    )
    db_session.add(contact)
    await db_session.flush()
    task = await _make_task(
        db_session, test_tenant.id, assigned_to=test_user.id, contact_id=contact.id
    )

    resp = await client.post(
        f"/api/tasks/{task.id}/contacted",
        json={"method": "call", "note": "Left detailed voicemail, will retry."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_contacted_at"] is not None
    assert body["customer_contact_method"] == "call"
    # Logging contact implies the assignee saw the task and started work.
    assert body["viewed_at"] is not None
    assert body["status"] == "in_progress"
    assert "Left detailed voicemail" in body["description"]

    # First-contact timestamp is preserved; method may be updated.
    first_contacted = body["customer_contacted_at"]
    resp2 = await client.post(
        f"/api/tasks/{task.id}/contacted", json={"method": "email"}
    )
    assert resp2.json()["customer_contacted_at"] == first_contacted
    assert resp2.json()["customer_contact_method"] == "email"


@pytest.mark.asyncio
async def test_contacted_rejects_unrelated_non_admin(
    db_session, test_tenant, test_user
):
    from datetime import datetime, timedelta, timezone

    from httpx import ASGITransport, AsyncClient
    from jose import jwt as jose_jwt

    from app.config import get_settings
    from app.database import get_db
    from app.main import app

    settings = get_settings()
    other = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="other-staff@testfirm.com",
        full_name="Other Staff",
        role="attorney",
        is_active=True,
    )
    db_session.add(other)
    await db_session.commit()
    task = await _make_task(db_session, test_tenant.id, assigned_to=test_user.id)

    token = jose_jwt.encode(
        {
            "sub": str(other.id),
            "tenant_id": str(test_tenant.id),
            "role": "attorney",
            "email": other.email,
            "billing_tier": "payg",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        resp = await ac.post(
            f"/api/tasks/{task.id}/contacted", json={"method": "call"}
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 403
