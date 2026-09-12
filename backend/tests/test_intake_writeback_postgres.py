"""PostgreSQL coverage for intake questionnaire write-back proposals (issue #402)."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models.conflict_check import ConflictCheckRecord
from app.models.contact import Contact
from app.models.matter_intake import MatterIntake
from app.models.plugin import Matter, MatterEvent
from app.models.task import Task
from app.routers import matter_intake as routes
from app.routers.client_portal import ClientPortalContext
from app.schemas.matter_intake import IntakeAnswers, IntakeChangeDecision, IntakeStart
from app.services import intake_writeback, matter_intake as service

QUESTIONS = [
    {"key": "client_phone", "label": "Mobile phone", "required": False},
    {"key": "client_street", "label": "Street address", "required": False},
    {"key": "matter_court", "label": "Court or agency", "required": False},
    {"key": "matter_case_number", "label": "Case number", "required": False},
    {"key": "matter_judge", "label": "Judge", "required": False},
    {"key": "matter_jurisdiction", "label": "Jurisdiction", "required": False},
    {"key": "conflict_spouse", "label": "Spouse or partner", "required": False},
    {"key": "conflict_businesses", "label": "Businesses involved", "required": False},
]


def staff_user(test_user):
    return SimpleNamespace(
        id=test_user.id, tenant_id=test_user.tenant_id, role=test_user.role
    )


async def make_packet(db_session, test_user, monkeypatch, **contact_overrides):
    user = staff_user(test_user)
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
        **contact_overrides,
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=f"writeback-{uuid.uuid4().hex[:8]}",
        matter_name="Smith matter",
        client_contact_id=contact.id,
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    monkeypatch.setattr(
        service,
        "store_file",
        AsyncMock(
            return_value=SimpleNamespace(
                succeeded=True,
                storage_path="provider/path",
                provider="google_drive",
                backend="google_drive",
                provider_item_id="item",
                drive_id="drive",
                parent_id="parent",
            )
        ),
    )
    monkeypatch.setattr(
        service, "get_user_capabilities", AsyncMock(return_value={"manage_matters"})
    )
    options = IntakeStart(
        email=contact.email,
        channels=["email"],
        include_questionnaire=True,
        questions=QUESTIONS,
        confirm_send=True,
    )
    packet = await service.start_packet(
        db_session, user, matter, options, "fee.pdf", b"%PDF-reviewed"
    )
    ctx = ClientPortalContext(
        tenant_id=str(user.tenant_id),
        matter_id=str(matter.id),
        contact_id=str(contact.id),
        email=contact.email,
        invite_id=str(packet.invite_id),
    )
    return SimpleNamespace(
        user=user, contact=contact, matter=matter, packet=packet, ctx=ctx
    )


async def submit(db_session, env, answers):
    return await routes.submit(
        IntakeAnswers(answers=answers, confirm_complete=True),
        (env.ctx, env.matter),
        db_session,
    )


def pending_changes(env):
    return [
        change
        for change in (env.packet.proposed_changes or {}).get("changes", [])
        if change["status"] == "pending"
    ]


@pytest.mark.asyncio
async def test_empty_fields_produce_fill_proposals_and_review_task(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    await submit(
        db_session,
        env,
        {
            "client_phone": "(312) 555-0142",
            "client_street": "418 Prairie Rose Lane",
            "matter_court": "Cook County Circuit Court",
            "matter_case_number": "2026-D-1234",
            "matter_judge": "Judge Rivera",
            "matter_jurisdiction": "Illinois",
        },
    )
    changes = {change["id"]: change for change in pending_changes(env)}
    assert set(changes) == {
        "contact.phone",
        "contact.address.street",
        "matter.court",
        "matter.case_number",
        "matter.judge",
        "matter.jurisdiction",
    }
    assert all(change["kind"] == "fill" for change in changes.values())
    assert changes["matter.court"]["proposed"] == "Cook County Circuit Court"
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    assert task is not None
    assert task.title == "Review intake updates: Smith matter"
    assert task.task_type == "review"
    assert task.source == "intake"
    assert task.status == "pending"
    assert task.pending_action["type"] == "intake_writeback"
    # Nothing is written back before staff review.
    await db_session.refresh(env.contact)
    await db_session.refresh(env.matter)
    assert env.contact.phone is None
    assert env.matter.court is None
    # The client-facing payload never leaks staff proposals.
    result = await submit(
        db_session,
        env,
        {
            "client_phone": "(312) 555-0142",
            "client_street": "418 Prairie Rose Lane",
            "matter_court": "Cook County Circuit Court",
            "matter_case_number": "2026-D-1234",
            "matter_judge": "Judge Rivera",
            "matter_jurisdiction": "Illinois",
        },
    )
    assert "proposed_changes" not in result
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Task).where(Task.source == "intake")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_differing_curated_values_propose_conflicts_without_applying(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch, phone="+1 312 555 0100")
    env.matter.court = "DuPage County Court"
    await db_session.commit()
    await submit(
        db_session,
        env,
        {"client_phone": "(312) 555-9999", "matter_court": "Cook County Court"},
    )
    changes = {change["id"]: change for change in pending_changes(env)}
    assert changes["contact.phone"]["kind"] == "conflict"
    assert changes["contact.phone"]["current"] == "+1 312 555 0100"
    assert changes["contact.phone"]["proposed"] == "(312) 555-9999"
    assert changes["matter.court"]["kind"] == "conflict"
    await db_session.refresh(env.contact)
    await db_session.refresh(env.matter)
    assert env.contact.phone == "+1 312 555 0100"
    assert env.matter.court == "DuPage County Court"


@pytest.mark.asyncio
async def test_accept_applies_only_accepted_fields_and_closes_task(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    await submit(
        db_session,
        env,
        {"client_phone": "(312) 555-0142", "matter_court": "Cook County Court"},
    )
    result = await routes.accept_proposed_changes(
        env.matter.id,
        IntakeChangeDecision(change_id="contact.phone"),
        db_session,
        env.user,
    )
    assert result["pending_count"] == 1
    await db_session.refresh(env.contact)
    await db_session.refresh(env.matter)
    assert env.contact.phone == "(312) 555-0142"
    assert env.matter.court is None
    state = (await db_session.get(MatterIntake, env.packet.id)).proposed_changes
    by_id = {change["id"]: change for change in state["changes"]}
    assert by_id["contact.phone"]["status"] == "accepted"
    assert by_id["matter.court"]["status"] == "pending"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterEvent)
            .where(
                MatterEvent.matter_id == env.matter.id,
                MatterEvent.event_type == "intake_writeback",
            )
        )
        == 1
    )
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    assert task.status == "pending"
    # Accepting the remaining change completes the review task.
    result = await routes.accept_proposed_changes(
        env.matter.id, IntakeChangeDecision(all=True), db_session, env.user
    )
    assert result["pending_count"] == 0
    await db_session.refresh(task)
    await db_session.refresh(env.matter)
    assert task.status == "completed"
    assert env.matter.court == "Cook County Court"


@pytest.mark.asyncio
async def test_reject_marks_changes_and_completes_task(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    await submit(db_session, env, {"client_phone": "(312) 555-0142"})
    result = await routes.reject_proposed_changes(
        env.matter.id, IntakeChangeDecision(all=True), db_session, env.user
    )
    assert result["pending_count"] == 0
    await db_session.refresh(env.contact)
    assert env.contact.phone is None
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_conflict_answers_create_conflict_check_record(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    rival = Contact(
        id=uuid.uuid4(),
        tenant_id=env.user.tenant_id,
        organization_name="Acme Logistics LLC",
        entity_type="organization",
        contact_type="opposing_party",
    )
    db_session.add(rival)
    await db_session.commit()
    await submit(
        db_session,
        env,
        {
            "conflict_spouse": "Alex Smith",
            "conflict_businesses": "Acme Logistics",
        },
    )
    record = await db_session.scalar(
        select(ConflictCheckRecord).where(
            ConflictCheckRecord.matter_id == env.matter.id
        )
    )
    assert record is not None
    assert record.label.startswith("Intake questionnaire")
    assert record.query_snapshot["names"] == ["Alex Smith"]
    assert record.query_snapshot["organization_names"] == ["Acme Logistics"]
    assert record.match_count >= 1
    assert any(
        match["display_name"] == "Acme Logistics LLC"
        for match in record.result_snapshot
    )
    assert (env.packet.proposed_changes or {})["conflict_check_id"] == str(record.id)
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    # No record fields were proposed, so no review task exists, but the
    # conflict evidence is still persisted and linked from the packet.
    assert task is None
    # The submit succeeded even though hits were found.
    assert env.packet.answers["conflict_spouse"] == "Alex Smith"


@pytest.mark.asyncio
async def test_no_new_information_creates_no_task(db_session, test_user, monkeypatch):
    env = await make_packet(db_session, test_user, monkeypatch, phone="(312) 555-0142")
    env.matter.court = "Cook County Court"
    await db_session.commit()
    result = await submit(
        db_session,
        env,
        {"client_phone": "(312) 555-0142", "matter_court": "Cook County Court"},
    )
    assert result["requirements"]["questionnaire"]["completed"]
    assert pending_changes(env) == []
    assert (env.packet.proposed_changes or {}).get("changes") == []
    assert await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback")) is None


@pytest.mark.asyncio
async def test_accept_refuses_when_record_changed_since_proposal(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    await submit(db_session, env, {"client_phone": "(312) 555-0142"})
    env.contact.phone = "(312) 555-0777"
    await db_session.commit()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await routes.accept_proposed_changes(
            env.matter.id,
            IntakeChangeDecision(change_id="contact.phone"),
            db_session,
            env.user,
        )
    assert exc.value.status_code == 409
    await db_session.rollback()
    await db_session.refresh(env.contact)
    assert env.contact.phone == "(312) 555-0777"


@pytest.mark.asyncio
async def test_an_answer_too_long_to_store_is_reported_not_dropped(
    db_session, test_user, monkeypatch
):
    """A skipped answer that reaches nobody is the same as an answer lost."""
    env = await make_packet(db_session, test_user, monkeypatch)
    long_court = "C" * 400  # matter.court holds 300
    await submit(
        db_session,
        env,
        {"matter_court": long_court, "matter_judge": "Judge Rivera"},
    )
    state = env.packet.proposed_changes or {}
    assert [item["id"] for item in state["oversized"]] == ["matter.court"]
    assert state["oversized"][0]["length"] == 400
    assert state["oversized"][0]["max_length"] == 300
    assert state["oversized"][0]["label"] == "Court or agency"
    # It is reported, never proposed: nothing here is acceptable.
    assert [change["id"] for change in pending_changes(env)] == ["matter.judge"]
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    assert task is not None
    assert "Too long to store on Court or agency" in task.description
    await db_session.refresh(env.matter)
    assert env.matter.court is None


@pytest.mark.asyncio
async def test_an_oversized_answer_alone_still_raises_the_review_task(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    await submit(db_session, env, {"matter_court": "C" * 400})
    assert pending_changes(env) == []
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    assert task is not None
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_deciding_the_other_changes_leaves_the_task_open_for_it(
    db_session, test_user, monkeypatch
):
    env = await make_packet(db_session, test_user, monkeypatch)
    await submit(
        db_session,
        env,
        {"matter_court": "C" * 400, "matter_judge": "Judge Rivera"},
    )
    result = await routes.accept_proposed_changes(
        env.matter.id, IntakeChangeDecision(all=True), db_session, env.user
    )
    assert result["pending_count"] == 0
    assert result["oversized_count"] == 1
    task = await db_session.get(Task, uuid.uuid5(env.packet.id, "writeback"))
    # The unresolvable answer still needs a person, so the task stays open.
    assert task.status != "completed"


def test_oversized_answers_reports_only_bound_over_long_answers():
    questions = [
        {"key": "matter_court", "label": "Court or agency"},
        {"key": "matter_judge", "label": "Judge"},
        {"key": "conflict_spouse", "label": "Spouse or partner"},
    ]
    reported = intake_writeback.oversized_answers(
        questions,
        {
            "matter_court": "C" * 301,
            "matter_judge": "Judge Rivera",
            "conflict_spouse": "S" * 900,
        },
    )
    # The judge fits; the conflict question binds to no record field at all.
    assert [item["question_key"] for item in reported] == ["matter_court"]


def test_question_bindings_built_from_intake_form():
    assert intake_writeback.QUESTION_BINDINGS["client_phone"] == "client.phone"
    assert (
        intake_writeback.QUESTION_BINDINGS["client_street"] == "client.address.street"
    )
    assert (
        intake_writeback.QUESTION_BINDINGS["matter_case_number"] == "matter.case_number"
    )
    assert intake_writeback.QUESTION_BINDINGS["other_parties"] == "matter.counterparty"
    # Section 6 conflict fields have no binding and never propose field writes.
    # (matter_counterparty / other_parties are the exception: they carry a
    # declared binding for the counterparty field AND feed the conflict check.)
    pure_conflict_keys = set(intake_writeback.CONFLICT_QUESTION_KEYS) - {
        "matter_counterparty",
        "other_parties",
    }
    for key in pure_conflict_keys:
        assert key not in intake_writeback.QUESTION_BINDINGS
