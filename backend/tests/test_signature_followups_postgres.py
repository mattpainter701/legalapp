"""Chasing a signature, and filing a document by the category it carries.

A signature request sent mid-case used to have no deadline and nothing
watching it. A dated request now raises one assigned follow-up keyed to the
request, and closes it the moment the request can no longer be acted on.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.models.plugin import Matter
from app.services.esign.followups import (
    CLOSING_STATUSES,
    close_signature_followup,
    ensure_signature_followup,
    matter_timezone,
)
from app.services.matter_document_organization import (
    SYSTEM_FOLDER_CORRESPONDENCE,
    autofile_folder_id,
    ensure_system_folder,
)

DUE = datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def matter(db_session, test_tenant, test_user):
    row = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"signature-{uuid.uuid4().hex[:8]}",
        matter_name="Delgado refinance",
        status="open",
    )
    db_session.add(row)
    await db_session.commit()
    return row


def request_for(matter, user, **overrides):
    values = dict(
        id=uuid.uuid4(),
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        due_at=DUE,
        status="sent",
        created_by_user_id=user.id,
        source_document_filename="Fee agreement.pdf",
        signers=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_a_dated_request_raises_one_chasing_task(
    db_session, matter, test_user
):
    req = request_for(matter, test_user)

    task = await ensure_signature_followup(db_session, req)
    await db_session.commit()

    assert task is not None
    assert task.title == "Signature due from client: Fee agreement.pdf"
    assert task.source == "signature"
    assert task.external_ref == f"signature:{req.id}"
    assert task.assigned_to_user_id == test_user.id
    assert "Fee agreement.pdf" in task.description


@pytest.mark.asyncio
async def test_a_request_with_no_deadline_raises_nothing(
    db_session, matter, test_user
):
    req = request_for(matter, test_user, due_at=None)

    assert await ensure_signature_followup(db_session, req) is None
    # And closing one is equally a no-op, so a request that never had a
    # deadline cannot cancel somebody else's task.
    assert await close_signature_followup(db_session, req, "signed") is None


@pytest.mark.parametrize("status", sorted(CLOSING_STATUSES))
@pytest.mark.asyncio
async def test_a_request_already_finished_raises_nothing(
    db_session, matter, test_user, status
):
    req = request_for(matter, test_user, status=status)

    assert await ensure_signature_followup(db_session, req) is None


@pytest.mark.asyncio
async def test_repeated_sends_do_not_duplicate_the_task(
    db_session, matter, test_user
):
    req = request_for(matter, test_user)

    first = await ensure_signature_followup(db_session, req)
    await db_session.commit()
    second = await ensure_signature_followup(db_session, req)
    await db_session.commit()

    assert first.id == second.id


@pytest.mark.asyncio
async def test_signing_closes_the_task(db_session, matter, test_user):
    req = request_for(matter, test_user)
    task = await ensure_signature_followup(db_session, req)
    await db_session.commit()

    closed = await close_signature_followup(db_session, req, "The client signed.")
    await db_session.commit()

    assert closed.id == task.id and closed.status == "cancelled"


@pytest.mark.asyncio
async def test_an_unnamed_document_still_reads_sensibly(
    db_session, matter, test_user
):
    req = request_for(matter, test_user, source_document_filename=None)

    task = await ensure_signature_followup(db_session, req)
    await db_session.commit()

    assert task.title == "Signature due from client: Document"


@pytest.mark.asyncio
async def test_the_first_signer_with_a_contact_is_carried_onto_the_task(
    db_session, matter, test_user, test_tenant
):
    from app.models.contact import Contact

    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Luis",
        last_name="Delgado",
        email="luis@example.com",
    )
    db_session.add(contact)
    await db_session.commit()

    req = request_for(
        matter,
        test_user,
        signers=[
            SimpleNamespace(contact_id=None),
            SimpleNamespace(contact_id=contact.id),
        ],
    )

    task = await ensure_signature_followup(db_session, req)
    await db_session.commit()

    assert task.contact_id == contact.id


class _ConfigDb:
    """Just enough session for the one scalar ``matter_timezone`` runs."""

    def __init__(self, config):
        self._config = config

    async def scalar(self, _statement):
        return self._config


@pytest.mark.asyncio
async def test_the_timezone_comes_from_what_intake_recorded():
    assert (
        await matter_timezone(
            _ConfigDb({"timezone": "America/Chicago"}), uuid.uuid4(), uuid.uuid4()
        )
        == "America/Chicago"
    )
    # A packet that never recorded one, and a config that is missing entirely,
    # both land on UTC rather than on None.
    assert await matter_timezone(_ConfigDb({}), uuid.uuid4(), uuid.uuid4()) == "UTC"
    assert await matter_timezone(_ConfigDb(None), uuid.uuid4(), uuid.uuid4()) == "UTC"
    assert (
        await matter_timezone(
            _ConfigDb({"timezone": None}), uuid.uuid4(), uuid.uuid4()
        )
        == "UTC"
    )


@pytest.mark.asyncio
async def test_the_deadline_lands_at_5pm_in_the_clients_timezone(
    db_session, matter, test_user, monkeypatch
):
    """"Due Friday" has to mean the same thing in month four as it did at intake."""
    from app.services.esign import followups

    async def chicago(*_args, **_kwargs):
        return "America/Chicago"

    monkeypatch.setattr(followups, "matter_timezone", chicago)

    task = await ensure_signature_followup(db_session, request_for(matter, test_user))
    await db_session.commit()

    assert task.due_date.isoformat() == "2026-09-25"
    assert task.due_time.hour == 17


@pytest.mark.asyncio
async def test_a_matter_that_never_ran_intake_falls_back_to_utc(
    db_session, matter
):
    assert await matter_timezone(db_session, matter.tenant_id, matter.id) == "UTC"


@pytest.mark.asyncio
async def test_a_document_files_itself_by_the_category_it_carries(
    db_session, matter
):
    folder_id = await autofile_folder_id(
        db_session,
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        document_category="correspondence",
    )
    await db_session.commit()

    expected = await ensure_system_folder(
        db_session,
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        system_key=SYSTEM_FOLDER_CORRESPONDENCE,
    )
    assert folder_id == expected.id


@pytest.mark.asyncio
async def test_the_category_match_ignores_case_and_padding(db_session, matter):
    padded = await autofile_folder_id(
        db_session,
        tenant_id=matter.tenant_id,
        matter_id=matter.id,
        document_category="  Correspondence  ",
    )
    await db_session.commit()

    assert padded is not None


@pytest.mark.asyncio
async def test_a_category_the_product_does_not_own_stays_unfiled(
    db_session, matter
):
    for category in ("something_else", "", None):
        assert (
            await autofile_folder_id(
                db_session,
                tenant_id=matter.tenant_id,
                matter_id=matter.id,
                document_category=category,
            )
            is None
        )


@pytest.mark.asyncio
async def test_a_folder_failure_never_costs_the_document(
    db_session, matter, monkeypatch
):
    """Losing the upload to a folder error would be the worse outcome."""
    from app.services import matter_document_organization

    async def explode(*_args, **_kwargs):
        raise RuntimeError("the folder table is unavailable")

    monkeypatch.setattr(
        matter_document_organization, "ensure_system_folder", explode
    )

    assert (
        await autofile_folder_id(
            db_session,
            tenant_id=matter.tenant_id,
            matter_id=matter.id,
            document_category="correspondence",
        )
        is None
    )
