from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.workspace_mcp import (
    GetClientArgs,
    GetIntakeArgs,
    GetTaskArgs,
    SearchClientsArgs,
    SearchIntakesArgs,
    SearchMattersArgs,
    SearchTasksArgs,
)
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services import workspace_lifecycle_capabilities as lifecycle


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def scalars(self):
        return self

    def one_or_none(self):
        if not self.values:
            return None
        assert len(self.values) == 1
        return self.values[0]


class _DB:
    def __init__(self, *, scalar_values=(), result_values=()):
        self.scalar_values = list(scalar_values)
        self.result_values = list(result_values)
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.scalar_values.pop(0)

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.result_values.pop(0))


def _context(db, tenant_id, *, role="user"):
    return CapabilityContext(
        db=db,
        user=SimpleNamespace(id=uuid4(), tenant_id=tenant_id, role=role),
        channel="workspace_mcp",
        granted_scopes=frozenset(
            {"contacts:read", "intakes:read", "matters:read", "tasks:read"}
        ),
    )


def _contact(*, tenant_id, **overrides):
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "entity_type": "person",
        "contact_type": "client",
        "first_name": "Avery",
        "last_name": "Client",
        "preferred_name": None,
        "organization_name": None,
        "email": "avery@example.test",
        "phone": "555-0100",
        "client_number": "C-100",
        "client_status": "active",
        "preferred_contact_method": "email",
        "preferred_language": "English",
        "is_active": True,
        "address": {"city": "Fargo", "state": "ND"},
        "tags": ["priority"],
        "client_since": date(2025, 1, 1),
        "preferred_contact_window": "afternoon",
        "preferred_contact_timezone": "America/Chicago",
        "referral_source": "website",
        "notes": "Client-provided notes",
        "is_primary_client_contact": False,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    contact = SimpleNamespace(**values)
    contact.display_name = contact.organization_name or " ".join(
        item for item in (contact.first_name, contact.last_name) if item
    )
    return contact


def _matter(*, tenant_id, client_id, **overrides):
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "client_contact_id": client_id,
        "matter_name": "Client v. Counterparty",
        "matter_type": "litigation",
        "practice_area": "civil",
        "status": "open",
        "stage": "discovery",
        "role": "plaintiff",
        "counterparty": "Counterparty",
        "jurisdiction": "North Dakota",
        "court": "District Court",
        "case_number": "CV-100",
        "is_closed": False,
        "updated_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _lead(*, tenant_id, contact_id, **overrides):
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "contact_id": contact_id,
        "status": "qualified",
        "source": "website",
        "practice_area": "civil",
        "description": "Prospect described a claim",
        "estimated_value": 1000,
        "assigned_to_user_id": uuid4(),
        "conflict_check_status": "cleared",
        "conflict_check_notes": "No conflict located",
        "matter_id": None,
        "declined_reason": None,
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _task(*, tenant_id, **overrides):
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "title": "Review generated pleading",
        "description": "Review exact cloud bytes",
        "task_type": "review",
        "status": "review",
        "priority": "high",
        "due_date": date(2026, 9, 1),
        "due_time": None,
        "matter_id": uuid4(),
        "contact_id": None,
        "assigned_to_user_id": uuid4(),
        "reviewer_user_id": uuid4(),
        "review_policy": "staff_then_attorney",
        "review_stage": "staff",
        "staff_reviewer_user_id": uuid4(),
        "attorney_reviewer_user_id": uuid4(),
        "version": 1,
        "source": "assistant",
        "waiting_reason": None,
        "closed_reason": None,
        "completed_at": None,
        "pending_action": {
            "type": "matter_document_draft",
            "document_id": str(uuid4()),
            "body": "must not be copied into task metadata",
        },
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_client_search_and_detail_return_linked_matters_without_billing_ids():
    tenant_id = uuid4()
    client = _contact(tenant_id=tenant_id)
    related = _contact(
        tenant_id=tenant_id,
        contact_type="other",
        first_name="Riley",
        last_name="Authorized",
        client_number=None,
    )
    matter = _matter(tenant_id=tenant_id, client_id=client.id)
    context = _context(
        _DB(scalar_values=[client], result_values=[[client], [related], [matter]]),
        tenant_id,
    )

    searched = await lifecycle.search_clients(context, SearchClientsArgs(query="Avery"))
    detailed = await lifecycle.get_client(context, GetClientArgs(client_id=client.id))

    assert searched["clients"][0]["client_id"] == str(client.id)
    assert detailed["matters"][0]["matter_id"] == str(matter.id)
    assert detailed["related_contacts"][0]["display_name"] == "Riley Authorized"
    assert "qbo_customer_id" not in detailed["client"]
    assert "tenant-provided source material" in detailed["content_warning"]


@pytest.mark.asyncio
async def test_intake_search_and_detail_include_prospect_and_conversion_state():
    tenant_id = uuid4()
    prospect = _contact(
        tenant_id=tenant_id,
        contact_type="prospect",
        client_status="prospect",
    )
    lead = _lead(tenant_id=tenant_id, contact_id=prospect.id)
    context = _context(
        _DB(result_values=[[(lead, prospect)], [(lead, prospect)]]), tenant_id
    )

    searched = await lifecycle.search_intakes(
        context, SearchIntakesArgs(query="Avery", status="qualified")
    )
    detailed = await lifecycle.get_intake(context, GetIntakeArgs(intake_id=lead.id))

    assert searched["intakes"][0]["intake_id"] == str(lead.id)
    assert detailed["contact"]["email"] == prospect.email
    assert detailed["intake"]["conflict_check_status"] == "cleared"


@pytest.mark.asyncio
async def test_matter_and_task_searches_are_bounded_and_task_detail_hides_body():
    tenant_id = uuid4()
    client = _contact(tenant_id=tenant_id)
    matter = _matter(tenant_id=tenant_id, client_id=client.id)
    task = _task(tenant_id=tenant_id, matter_id=matter.id)
    event = SimpleNamespace(
        id=uuid4(),
        event_type="created",
        actor_user_id=task.assigned_to_user_id,
        from_status=None,
        to_status="review",
        note="Awaiting human review",
        created_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    context = _context(
        _DB(
            scalar_values=[task],
            result_values=[[(matter, client)], [task], [(event, "Pat", None)]],
        ),
        tenant_id,
    )

    matters = await lifecycle.search_matters(
        context, SearchMattersArgs(query="Client", limit=5)
    )
    tasks = await lifecycle.search_tasks(
        context, SearchTasksArgs(matter_id=matter.id, limit=5)
    )
    detail = await lifecycle.get_task(context, GetTaskArgs(task_id=task.id))

    assert matters["matters"][0]["client"] == client.display_name
    assert tasks["tasks"][0]["task_url"].endswith(f"/tasks/{task.id}")
    assert detail["events"][0]["actor_label"] == "Pat"
    assert "body" not in detail["task"]["pending_action"]
    task_search_sql = str(context.db.statements[1])
    task_detail_sql = str(context.db.statements[2])
    assert "matter_assignments" in task_search_sql
    assert "matter_assignments" in task_detail_sql


@pytest.mark.asyncio
async def test_missing_lifecycle_records_fail_without_cross_tenant_detail():
    tenant_id = uuid4()
    with pytest.raises(CapabilityError) as exc_info:
        await lifecycle.get_client(
            _context(_DB(scalar_values=[None]), tenant_id),
            GetClientArgs(client_id=uuid4()),
        )
    assert exc_info.value.code == "client_not_found"
