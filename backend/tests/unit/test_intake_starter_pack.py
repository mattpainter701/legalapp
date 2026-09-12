"""Executable guarantees for the paperwork every new client receives."""

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import intake_starter_pack as router
from app.routers.document_templates import CATEGORIES, extract_template_variables
from app.schemas.document_template import DocumentTemplateCreate
from app.schemas.matter_intake import (
    IntakeQuestion,
    IntakeStart,
    IntakeUploadRequirement,
)
from app.services import intake_starter_pack as pack
from app.services.template_bindings import is_valid_binding

MARKER = re.compile(r"\{\{\s*\#(?:if|unless|each)\s+([A-Za-z][A-Za-z0-9_.-]*)\s*\}\}")


@pytest.mark.parametrize(
    "matter_type", ["", None, "   ", "Something we have never handled"]
)
def test_an_unrecognised_type_still_gets_a_questionnaire(matter_type):
    """A client always receives questions; nothing falls through to an empty pack."""

    assert pack.questionnaire(matter_type)


@pytest.mark.parametrize(
    "matter_type,practice_area,expected",
    [
        ("general", "Family Law", "family"),
        ("general", "Mediation", "mediation"),
        ("", "Real Estate", "real_estate"),
        ("DUI", "Family Law", "criminal"),
        ("general", "", "general"),
    ],
)
def test_the_practice_area_answers_when_the_type_says_nothing(
    matter_type, practice_area, expected
):
    """Real files carry the signal in whichever label the firm filled in."""

    assert pack.resolve_practice(matter_type, practice_area).slug == expected
    assert pack.pack(matter_type, practice_area)["practice"] == expected


def test_a_specific_alias_beats_a_generic_one_from_another_practice():
    """ "Contract" belongs to business; "breach of contract" is still a dispute."""

    assert pack.resolve_practice("Contract review").slug == "business"
    assert pack.resolve_practice("Breach of contract").slug == "litigation"


def test_every_alias_is_claimed_by_exactly_one_practice():
    seen: dict[str, str] = {}
    for practice in pack.practices():
        for alias in practice.aliases:
            assert (
                alias not in seen
            ), f"{alias} claimed by {seen.get(alias)} and {practice.slug}"
            seen[alias] = practice.slug

def test_the_pack_resolves_the_practice_from_either_label():
    assert pack.pack("general", "Family Law")["practice"] == "family"
    assert pack.pack("DUI", "Family Law")["practice"] == "criminal"


@pytest.mark.parametrize("practice", pack.practices(), ids=lambda p: p.slug)
def test_a_resolved_pack_is_accepted_by_the_intake_schema(practice):
    """The pack is submitted to intake unchanged, so it must validate there."""

    questions = pack.questionnaire(practice.slug)
    uploads = pack.upload_requirements(practice.slug)
    start = IntakeStart(
        email="client@example.com",
        channels=["email"],
        questions=[IntakeQuestion(**question) for question in questions],
        upload_requirements=[IntakeUploadRequirement(**upload) for upload in uploads],
        agreement_document_id=uuid.uuid4(),
        confirm_send=True,
    )
    assert len(start.questions) == len(questions)
    assert len({question.key for question in start.questions}) == len(questions)
    assert len({upload.key for upload in start.upload_requirements}) == len(uploads)


def test_shared_questions_come_before_practice_questions():
    questions = pack.questionnaire("divorce")
    assert [q["key"] for q in questions[: len(pack.CORE_QUESTIONS)]] == [
        q.key for q in pack.CORE_QUESTIONS
    ]
    assert questions[len(pack.CORE_QUESTIONS)]["key"] == "household"


@pytest.mark.parametrize("document", pack.documents(), ids=lambda d: d.key)
def test_a_document_declares_exactly_the_placeholders_it_uses(document):
    """An undeclared placeholder never gets filled; a declared one never used is noise."""

    used = set(extract_template_variables(document.body)) | set(
        MARKER.findall(document.body)
    )
    declared = {field.name for field in document.fields}
    assert used == declared


@pytest.mark.parametrize("document", pack.documents(), ids=lambda d: d.key)
def test_a_document_is_installable_through_the_normal_template_rules(document):
    assert document.category in CATEGORIES
    DocumentTemplateCreate(
        title=document.title,
        body=document.body,
        category=document.category,
        description=document.description,
        variable_schema=document.variable_schema(),
    )


@pytest.mark.parametrize("document", pack.documents(), ids=lambda d: d.key)
def test_every_declared_binding_is_in_the_server_catalogue(document):
    for field in document.fields:
        assert is_valid_binding(field.binding), f"{document.key}.{field.name}"


def test_a_jurisdiction_drafted_template_records_which_one():
    """A firm must be able to see what law the wording was written against."""

    assert pack.HOURLY_FEE_AGREEMENT_ND.jurisdiction == "North Dakota"
    assert pack.FEE_AGREEMENT.jurisdiction == ""


@pytest.mark.asyncio
async def test_install_stores_the_jurisdiction_on_the_template():
    db = FakeDB()
    await pack.install(db, uuid.uuid4())

    stored = {row.title: row.jurisdiction for row in db.added}

    assert stored[pack.HOURLY_FEE_AGREEMENT_ND.title] == "North Dakota"
    assert stored[pack.FEE_AGREEMENT.title] is None


def test_only_settled_terms_carry_a_default():
    """A convention can be suggested; a fee, rate or amount cannot."""

    defaults = {
        field.name: field.default
        for field in pack.HOURLY_FEE_AGREEMENT_ND.fields
        if field.default
    }

    assert defaults["billing_increment"] == "0.1 hour (six minutes)"
    assert "North Dakota" in defaults["confidentiality_rule"]
    assert not {
        "retainer_amount",
        "hourly_rate",
        "staff_rate_range",
        "attorney_rate_range",
        "trial_fee_amount",
        "venue",
    } & set(defaults)


def test_fee_terms_the_firm_decides_are_never_bound_to_a_record():
    """A fee or deposit the firm must set is decided by a person, never inferred."""

    decided_by_the_firm = {
        field.binding
        for field in pack.FEE_AGREEMENT.fields
        if field.name
        in {
            "flat_fee_amount",
            "advance_deposit_amount",
            "scope_of_representation",
            "excluded_matters",
        }
    }
    assert decided_by_the_firm == {"manual"}


def test_fee_terms_the_matter_record_carries_bind_to_it():
    """Contingency, retainer, and venue already live on the matter's records,
    so Smart Fill fills them from there instead of asking again."""

    by_name = {field.name: field.binding for field in pack.FEE_AGREEMENT.fields}
    assert by_name["contingency_percentage"] == "matter.contingency_percentage"

    nd_by_name = {
        field.name: field.binding for field in pack.HOURLY_FEE_AGREEMENT_ND.fields
    }
    assert nd_by_name["retainer_amount"] == "matter.retainer_amount"
    assert nd_by_name["retainer_minimum_balance"] == "matter.retainer_minimum_balance"
    assert nd_by_name["venue"] == "matter.venue"
    # Rate ranges are the firm's own schedule, not a record the matter carries.
    assert nd_by_name["staff_rate_range"] == "manual"
    assert nd_by_name["attorney_rate_range"] == "manual"


def test_the_pack_names_the_documents_that_travel_with_it():
    payload = pack.pack("Divorce")
    assert payload["practice"] == "family"
    assert [document["key"] for document in payload["documents"]] == [
        document.key for document in pack.documents()
    ]
    assert payload["questions"] and payload["upload_requirements"]


class FakeDB:
    """Records what install() would write, without a database."""

    def __init__(self, existing=()):
        self.existing = list(existing)
        self.added = []
        self.commits = 0

    async def scalar(self, query):
        title = query.compile().params.get("lower_1")
        for template in self.existing:
            if template.title.lower() == title:
                return template
        return None

    def add(self, row):
        self.added.append(row)
        self.existing.append(row)

    async def flush(self):
        for row in self.added:
            if row.id is None:
                row.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_install_adds_both_documents_as_unapproved_drafts():
    db = FakeDB()
    tenant = uuid.uuid4()

    installed = await pack.install(db, tenant)

    assert [row["created"] for row in installed] == [True] * len(pack.documents())
    assert len(db.added) == len(pack.documents())
    for row in db.added:
        assert row.tenant_id == tenant
        assert row.status == "draft"
        assert row.approved_at is None
        assert row.format == "markdown"
        assert row.variable_schema["fields"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_install_leaves_a_firms_own_template_alone():
    """A firm that edited or approved its fee agreement keeps it."""

    db = FakeDB()
    tenant = uuid.uuid4()
    await pack.install(db, tenant)
    theirs = db.added[0]
    theirs.body = "The firm's own reviewed terms"
    theirs.status = "approved"

    again = await pack.install(db, tenant)

    assert [row["created"] for row in again] == [False] * len(pack.documents())
    assert len(db.added) == len(pack.documents())
    assert theirs.body == "The firm's own reviewed terms"
    assert again[0]["template_id"] == str(theirs.id)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="attorney")


@pytest.mark.asyncio
async def test_reading_the_pack_by_matter_type_needs_no_matter(monkeypatch):
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())

    payload = await router.read_pack(
        matter_type="DUI", practice_area="", matter_id=None, db=object(), user=_user()
    )

    assert payload["practice"] == "criminal"


@pytest.mark.asyncio
async def test_reading_the_pack_for_a_matter_reads_that_matters_type(monkeypatch):
    user = _user()
    matter = SimpleNamespace(matter_type="Probate of estate", practice_area=None)
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "can_access_matter", AsyncMock(return_value=True))
    db = SimpleNamespace(scalar=AsyncMock(return_value=matter))

    payload = await router.read_pack(matter_id=uuid.uuid4(), db=db, user=user)

    assert payload["practice"] == "estate"
    assert payload["matter_type"] == "Probate of estate"


@pytest.mark.asyncio
async def test_a_matter_typed_general_still_gets_its_practice_questions(monkeypatch):
    """The live library types many matters "general" and names the area instead."""

    matter = SimpleNamespace(matter_type="general", practice_area="Family Law")
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "can_access_matter", AsyncMock(return_value=True))
    db = SimpleNamespace(scalar=AsyncMock(return_value=matter))

    payload = await router.read_pack(matter_id=uuid.uuid4(), db=db, user=_user())

    assert payload["practice"] == "family"
    assert payload["practice_area"] == "Family Law"


@pytest.mark.asyncio
async def test_a_matter_the_caller_cannot_reach_is_not_found(monkeypatch):
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "can_access_matter", AsyncMock(return_value=False))

    with pytest.raises(router.HTTPException) as failure:
        await router.read_pack(matter_id=uuid.uuid4(), db=object(), user=_user())

    assert failure.value.status_code == 404


@pytest.mark.asyncio
async def test_listing_practices_reports_what_each_type_resolves_to():
    listed = await router.list_practices(user=_user())

    assert {row["practice"] for row in listed} == {p.slug for p in pack.practices()}
    family = next(row for row in listed if row["practice"] == "family")
    assert "divorce" in family["matter_types"]
    assert family["question_count"] > len(pack.CORE_QUESTIONS)


@pytest.mark.asyncio
async def test_installing_documents_is_tenant_scoped(monkeypatch):
    user = _user()
    tenant_context = AsyncMock()
    monkeypatch.setattr(router, "set_tenant_context", tenant_context)
    db = FakeDB()

    payload = await router.install_documents(db=db, user=user)

    assert len(payload["documents"]) == len(pack.documents())
    assert tenant_context.await_args.args[1] == str(user.tenant_id)
    assert all(row.tenant_id == user.tenant_id for row in db.added)


def _values(document, **overrides):
    filled = {field.name: f"[{field.label}]" for field in document.fields}
    filled.update(overrides)
    return filled


def test_the_fee_agreement_renders_only_the_fee_terms_that_apply():
    """A firm bills one way per matter; the other fee sections must disappear."""

    from app.routers.document_templates import render_template

    hourly = render_template(
        pack.FEE_AGREEMENT.body,
        _values(
            pack.FEE_AGREEMENT,
            hourly_rate="$325 per hour",
            flat_fee_amount="",
            flat_fee_payment_terms="",
            contingency_percentage="",
            contingency_terms="",
        ),
    )

    assert "$325 per hour" in hourly
    assert "Flat fee." not in hourly
    assert "Contingency fee." not in hourly
    assert "{{" not in hourly


@pytest.mark.parametrize("document", pack.documents(), ids=lambda d: d.key)
def test_a_fully_supplied_document_leaves_no_placeholder_behind(document):
    from app.routers.document_templates import render_template

    assert "{{" not in render_template(document.body, _values(document))
