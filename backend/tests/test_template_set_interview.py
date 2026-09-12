"""Smart Fill across a merged interview, and members that cannot be drafted.

The merge itself is unit-tested in test_template_sets_unit. This covers what
the endpoint adds: that one resolved value is reported once however many
documents it fills, and that a member which cannot be drafted is named rather
than dropped.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.routers import document_templates, template_sets as router
from app.services import template_sets

pytestmark = pytest.mark.asyncio


def _matter():
    return SimpleNamespace(
        id=uuid.uuid4(),
        matter_name="Lovelace v. Analytical Engines",
        matter_type="civil",
        description=None,
        status="open",
        stage="pleadings",
        jurisdiction="North Dakota",
        case_number="CV-2026-42",
        court="Cass County District Court",
        judge="Hon. A. Turing",
        billing_method=None,
        billing_cycle=None,
        hourly_rate=None,
        budget_amount=None,
        role=None,
        counterparty=None,
        client=SimpleNamespace(
            id=uuid.uuid4(),
            display_name="Ada Lovelace",
            email="ada@example.com",
            phone=None,
            address={"city": "Fargo"},
        ),
        attorney_of_record=None,
    )


def member(template_id, title, *fields):
    return template_sets.TemplateMember(
        template_id=template_id, title=title, variable_schema={"fields": list(fields)}
    )


class TestMergedSmartFill:
    async def _fill(self, monkeypatch, members):
        matter = _matter()

        async def load_matter(**_):
            return matter

        async def load_parties(**_):
            return []

        monkeypatch.setattr(document_templates, "_load_matter_context", load_matter)
        monkeypatch.setattr(document_templates, "_load_matter_parties", load_parties)
        questions = template_sets.build_interview(members)
        resolved = await router._interview_suggestions(
            db=SimpleNamespace(),
            tenant_id=uuid.uuid4(),
            current_user=SimpleNamespace(
                id=uuid.uuid4(), full_name="Test Attorney", email="t@example.com"
            ),
            matter_id=str(matter.id),
            questions=questions,
        )
        return questions, resolved

    async def test_one_value_answers_every_document_that_asked(self, monkeypatch):
        questions, resolved = await self._fill(
            monkeypatch,
            [
                member("t1", "Motion", {"name": "cause_no", "binding": "matter.case_number"}),
                member("t2", "Notice", {"name": "CASE", "binding": "matter.case_number"}),
            ],
        )
        assert len(questions) == 1
        assert resolved[questions[0].key].suggested_value == "CV-2026-42"
        # …and the caller still knows both documents it lands in.
        assert {ref.template_id for ref in questions[0].appears_in} == {"t1", "t2"}

    async def test_a_card_path_and_its_pre_card_spelling_resolve_together(
        self, monkeypatch
    ):
        questions, resolved = await self._fill(
            monkeypatch,
            [
                member("t1", "A", {"name": "a", "binding": "client.name"}),
                member("t2", "B", {"name": "b", "binding": "client.full_name"}),
            ],
        )
        assert len(questions) == 1
        assert resolved[questions[0].key].suggested_value == "Ada Lovelace"

    async def test_a_manual_question_is_asked_per_document_and_never_auto_filled(
        self, monkeypatch
    ):
        questions, resolved = await self._fill(
            monkeypatch,
            [
                member("t1", "A", {"name": "note", "binding": "manual"}),
                member("t2", "B", {"name": "note", "binding": "manual"}),
            ],
        )
        assert len(questions) == 2
        assert all(resolved[q.key].suggested_value is None for q in questions)

    async def test_an_unresolvable_binding_says_so_rather_than_going_blank(
        self, monkeypatch
    ):
        questions, resolved = await self._fill(
            monkeypatch,
            [member("t1", "A", {"name": "d", "binding": "defendant.full_name"})],
        )
        item = resolved[questions[0].key]
        assert item.suggested_value is None
        assert item.provenance["status"] == "binding_unresolved"

    async def test_no_questions_means_no_resolver_call(self, monkeypatch):
        # An empty set must not reach the matter loader at all.
        called = False

        async def load_matter(**_):
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(document_templates, "_load_matter_context", load_matter)
        assert template_sets.build_interview([]) == []
        assert called is False


class TestUnavailableMembers:
    """A packet silently missing a document is worse than one that says so."""

    def _record(self, items):
        return SimpleNamespace(id=uuid.uuid4(), items=items)

    def _item(self, template_id, position=0, pinned=None):
        return SimpleNamespace(
            template_id=template_id,
            position=position,
            pinned_version_no=pinned,
        )

    async def test_an_unpublished_member_is_reported_with_the_reason(
        self, monkeypatch
    ):
        template_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        async def titles(*_args, **_kwargs):
            return {template_id: "Draft motion"}

        async def published(_db, _template):
            raise ValueError("Publish a tested version before generating documents.")

        monkeypatch.setattr(router, "_titles", titles)
        monkeypatch.setattr(router, "published_template_view", published)
        db = SimpleNamespace(scalar=_returns(SimpleNamespace(id=template_id)))

        members, unavailable, _ = await router._member_snapshots(
            db, tenant_id, self._record([self._item(template_id)])
        )
        assert members == []
        assert unavailable[0].title == "Draft motion"
        assert "Publish a tested version" in unavailable[0].unavailable_reason

    async def test_a_missing_pinned_version_names_the_version(self, monkeypatch):
        template_id = uuid.uuid4()

        async def titles(*_args, **_kwargs):
            return {template_id: "Filed notice"}

        async def version(*_args, **_kwargs):
            return None

        monkeypatch.setattr(router, "_titles", titles)
        monkeypatch.setattr(router, "get_version", version)
        db = SimpleNamespace(scalar=_returns(SimpleNamespace(id=template_id)))

        members, unavailable, _ = await router._member_snapshots(
            db, uuid.uuid4(), self._record([self._item(template_id, pinned=3)])
        )
        assert members == []
        assert "Version 3 is no longer available." == unavailable[0].unavailable_reason

    async def test_a_deleted_template_does_not_break_the_rest_of_the_set(
        self, monkeypatch
    ):
        gone, kept = uuid.uuid4(), uuid.uuid4()

        async def titles(*_args, **_kwargs):
            return {kept: "Cover sheet"}

        async def published(_db, _template):
            return SimpleNamespace(
                variable_schema={"fields": [{"name": "a", "binding": "matter.court"}]},
                current_version_no=2,
            )

        monkeypatch.setattr(router, "_titles", titles)
        monkeypatch.setattr(router, "published_template_view", published)
        db = SimpleNamespace(scalar=_returns(SimpleNamespace(id=kept)))

        members, unavailable, resolved = await router._member_snapshots(
            db,
            uuid.uuid4(),
            self._record([self._item(gone, 0), self._item(kept, 1)]),
        )
        assert [entry.title for entry in members] == ["Cover sheet"]
        assert "no longer in your library" in unavailable[0].unavailable_reason
        assert resolved[str(kept)] == 2


def _returns(value):
    async def _scalar(*_args, **_kwargs):
        return value

    return _scalar
