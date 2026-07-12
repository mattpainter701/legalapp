"""Cross-tenant regression coverage for task and intake write references."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User


async def _foreign_references(db_session):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Tenant",
        domain=f"other-{uuid.uuid4().hex}.example",
        is_active=True,
    )
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"other-{uuid.uuid4().hex}@example.com",
        full_name="Other Tenant User",
        role="attorney",
        is_active=True,
    )
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        first_name="Other",
        last_name="Contact",
        created_by_user_id=user.id,
    )
    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        contact_id=contact.id,
        status="new",
        created_by_user_id=user.id,
    )
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"other-matter-{uuid.uuid4().hex}",
        matter_name="Other Tenant Matter",
        matter_type="general",
        status="open",
    )
    # Persist FK parents explicitly; these models expose IDs rather than ORM
    # relationships, so SQLAlchemy cannot infer every unit-of-work dependency.
    db_session.add(tenant)
    await db_session.commit()
    db_session.add(user)
    await db_session.commit()
    db_session.add(contact)
    await db_session.commit()
    db_session.add_all([lead, matter])
    await db_session.commit()
    return {
        "tenant": tenant,
        "user": user,
        "contact": contact,
        "lead": lead,
        "matter": matter,
    }


async def _inactive_tenant_user(db_session, tenant_id):
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=f"inactive-{uuid.uuid4().hex}@example.com",
        full_name="Inactive Tenant User",
        role="attorney",
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_task_create_rejects_every_cross_tenant_reference(client, db_session):
    foreign = await _foreign_references(db_session)
    cases = (
        ("matter_id", foreign["matter"].id, "Matter not found"),
        ("contact_id", foreign["contact"].id, "Contact not found"),
        ("assigned_to_user_id", foreign["user"].id, "Assigned user not found"),
    )

    for field, value, detail in cases:
        response = await client.post(
            "/api/tasks",
            json={"title": f"Forbidden {field}", field: str(value)},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == detail

    count = await db_session.scalar(select(func.count()).select_from(Task))
    assert count == 0


@pytest.mark.asyncio
async def test_task_create_accepts_tenant_owned_references(
    client, db_session, test_tenant, test_user
):
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Local",
        last_name="Client",
        created_by_user_id=test_user.id,
    )
    matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"local-matter-{uuid.uuid4().hex}",
        matter_name="Local Matter",
        matter_type="general",
        status="open",
    )
    db_session.add_all([contact, matter])
    await db_session.commit()

    response = await client.post(
        "/api/tasks",
        json={
            "title": "Valid linked task",
            "matter_id": str(matter.id),
            "contact_id": str(contact.id),
            "assigned_to_user_id": str(test_user.id),
        },
    )

    assert response.status_code == 201
    assert response.json()["matter_id"] == str(matter.id)
    assert response.json()["contact_id"] == str(contact.id)
    assert response.json()["assigned_to_user_id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_task_create_and_update_reject_inactive_assignee(
    client, db_session, test_tenant
):
    inactive = await _inactive_tenant_user(db_session, test_tenant.id)

    create_response = await client.post(
        "/api/tasks",
        json={
            "title": "Inactive assignment",
            "assigned_to_user_id": str(inactive.id),
        },
    )
    assert create_response.status_code == 404
    assert create_response.json()["detail"] == "Assigned user not found"

    task = Task(
        tenant_id=test_tenant.id,
        title="Unassigned task",
        status="pending",
        priority="medium",
    )
    db_session.add(task)
    await db_session.commit()
    update_response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"assigned_to_user_id": str(inactive.id)},
    )
    assert update_response.status_code == 404
    assert update_response.json()["detail"] == "Assigned user not found"
    await db_session.refresh(task)
    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_task_update_rejects_every_cross_tenant_reference_without_mutation(
    client, db_session, test_tenant
):
    foreign = await _foreign_references(db_session)
    task = Task(
        tenant_id=test_tenant.id,
        title="Tenant-owned task",
        status="pending",
        priority="medium",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    cases = (
        ("matter_id", foreign["matter"].id, "Matter not found"),
        ("contact_id", foreign["contact"].id, "Contact not found"),
        ("assigned_to_user_id", foreign["user"].id, "Assigned user not found"),
    )
    for field, value, detail in cases:
        response = await client.patch(
            f"/api/tasks/{task.id}",
            json={field: str(value)},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == detail

    await db_session.refresh(task)
    assert task.matter_id is None
    assert task.contact_id is None
    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_task_update_allows_unrelated_change_with_preexisting_foreign_reference(
    client, db_session, test_tenant
):
    foreign = await _foreign_references(db_session)
    task = Task(
        tenant_id=test_tenant.id,
        title="Legacy invalid task",
        status="pending",
        priority="medium",
        contact_id=foreign["contact"].id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await client.patch(
        f"/api/tasks/{task.id}", json={"title": "Legacy reference retained"}
    )

    assert response.status_code == 200
    assert response.json()["contact_id"] == str(foreign["contact"].id)
    await db_session.refresh(task)
    assert task.title == "Legacy reference retained"


@pytest.mark.asyncio
async def test_task_with_historically_deactivated_assignee_can_be_completed(
    client, db_session, test_tenant
):
    assignee = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"historical-{uuid.uuid4().hex}@testfirm.com",
        full_name="Historical Assignee",
        role="attorney",
        is_active=True,
    )
    db_session.add(assignee)
    await db_session.commit()
    task = Task(
        tenant_id=test_tenant.id,
        title="Complete after staff departure",
        status="pending",
        priority="medium",
        assigned_to_user_id=assignee.id,
    )
    db_session.add(task)
    await db_session.commit()
    assignee.is_active = False
    await db_session.commit()

    response = await client.patch(f"/api/tasks/{task.id}", json={"status": "completed"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["assigned_to_user_id"] == str(assignee.id)


@pytest.mark.asyncio
async def test_task_update_explicit_null_clears_optional_references(
    client, db_session, test_tenant, test_user
):
    inactive = await _inactive_tenant_user(db_session, test_tenant.id)
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Clearable",
        last_name="Contact",
        created_by_user_id=test_user.id,
    )
    matter = Matter(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"clearable-matter-{uuid.uuid4().hex}",
        matter_name="Clearable Matter",
        matter_type="general",
        status="open",
    )
    db_session.add_all([contact, matter])
    await db_session.commit()
    task = Task(
        tenant_id=test_tenant.id,
        title="Clear invalid assignment",
        status="pending",
        priority="medium",
        matter_id=matter.id,
        contact_id=contact.id,
        assigned_to_user_id=inactive.id,
    )
    db_session.add(task)
    await db_session.commit()

    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={
            "matter_id": None,
            "contact_id": None,
            "assigned_to_user_id": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["matter_id"] is None
    assert response.json()["contact_id"] is None
    assert response.json()["assigned_to_user_id"] is None
    await db_session.refresh(task)
    assert task.matter_id is None
    assert task.contact_id is None
    assert task.assigned_to_user_id is None


@pytest.mark.asyncio
async def test_task_reference_malformed_uuids_remain_validation_errors(
    client, db_session, test_tenant
):
    task = Task(
        tenant_id=test_tenant.id,
        title="Validation target",
        status="pending",
        priority="medium",
    )
    db_session.add(task)
    await db_session.commit()

    for field in ("matter_id", "contact_id", "assigned_to_user_id"):
        create_response = await client.post(
            "/api/tasks",
            json={"title": "Malformed reference", field: "not-a-uuid"},
        )
        update_response = await client.patch(
            f"/api/tasks/{task.id}",
            json={field: "not-a-uuid"},
        )
        assert create_response.status_code == 422
        assert update_response.status_code == 422

    task_count = await db_session.scalar(select(func.count()).select_from(Task))
    assert task_count == 1


@pytest.mark.asyncio
async def test_intake_call_rejects_cross_tenant_contact_without_writing(
    client, db_session
):
    foreign = await _foreign_references(db_session)

    response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Cross Tenant Caller",
            "outcome": "log_only",
            "task_mode": "none",
            "existing_contact_id": str(foreign["contact"].id),
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Contact not found"
    count = await db_session.scalar(select(func.count()).select_from(CommunicationLog))
    assert count == 0


@pytest.mark.asyncio
async def test_intake_call_accepts_matching_tenant_contact_and_lead(
    client, db_session, test_tenant, test_user
):
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Valid",
        last_name="Caller",
        created_by_user_id=test_user.id,
    )
    db_session.add(contact)
    await db_session.commit()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        status="new",
        created_by_user_id=test_user.id,
    )
    db_session.add(lead)
    await db_session.commit()

    response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Valid Caller",
            "outcome": "log_only",
            "task_mode": "none",
            "existing_contact_id": str(contact.id),
            "existing_lead_id": str(lead.id),
        },
    )

    assert response.status_code == 201
    assert response.json()["contact_id"] == str(contact.id)
    assert response.json()["lead_id"] == str(lead.id)
    log = await db_session.get(
        CommunicationLog, uuid.UUID(response.json()["communication_id"])
    )
    assert log is not None
    assert log.tenant_id == test_tenant.id
    assert log.contact_id == contact.id


@pytest.mark.asyncio
async def test_intake_call_rejects_cross_tenant_lead_and_staff_references(
    client, db_session
):
    foreign = await _foreign_references(db_session)

    lead_response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Cross Tenant Lead",
            "outcome": "log_only",
            "task_mode": "none",
            "existing_lead_id": str(foreign["lead"].id),
        },
    )
    staff_response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Cross Tenant Assignee",
            "outcome": "log_only",
            "task_mode": "specific_staff",
            "task_assigned_to_user_id": str(foreign["user"].id),
        },
    )

    assert lead_response.status_code == 404
    assert lead_response.json()["detail"] == "Lead not found"
    assert staff_response.status_code == 422
    assert (
        staff_response.json()["detail"] == "Select an active staff member for the task"
    )
    log_count = await db_session.scalar(
        select(func.count()).select_from(CommunicationLog)
    )
    task_count = await db_session.scalar(select(func.count()).select_from(Task))
    assert log_count == 0
    assert task_count == 0


@pytest.mark.asyncio
async def test_intake_call_does_not_copy_foreign_lead_assignee_into_task(
    client, db_session, test_tenant, test_user
):
    foreign = await _foreign_references(db_session)
    contact = Contact(
        tenant_id=test_tenant.id,
        first_name="Local",
        last_name="Caller",
        created_by_user_id=test_user.id,
    )
    db_session.add(contact)
    await db_session.commit()
    lead = Lead(
        tenant_id=test_tenant.id,
        contact_id=contact.id,
        assigned_to_user_id=foreign["user"].id,
        status="new",
        created_by_user_id=test_user.id,
    )
    db_session.add(lead)
    await db_session.commit()

    response = await client.post(
        "/api/intake/dashboard/calls",
        json={
            "caller_name": "Local Caller",
            "outcome": "create_lead",
            "task_mode": "partner_rotation",
            "existing_lead_id": str(lead.id),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Lead assigned user is not active in this tenant"
    )
    log_count = await db_session.scalar(
        select(func.count()).select_from(CommunicationLog)
    )
    task_count = await db_session.scalar(select(func.count()).select_from(Task))
    assert log_count == 0
    assert task_count == 0
