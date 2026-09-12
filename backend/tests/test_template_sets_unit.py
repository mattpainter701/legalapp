"""Sets: collapsing several documents into one interview.

Pure-function tests, no database.  The contract is that one answer reaches
every document that asked the same question — and that two documents which only
*look* like they asked the same question are never merged.
"""

import pytest

from app.services.template_sets import (
    MANUAL_KEY_PREFIX,
    MAX_SET_MEMBERS,
    TemplateMember,
    answers_for_documents,
    build_interview,
    unanswered_required,
)


def member(template_id, title, *fields):
    return TemplateMember(
        template_id=template_id,
        title=title,
        variable_schema={"fields": list(fields)},
    )


def field(name, **kwargs):
    return {"name": name, "label": kwargs.pop("label", name), **kwargs}


class TestMerging:
    def test_two_documents_binding_the_same_path_ask_once(self):
        questions = build_interview([
            member("t1", "Motion", field("def_name", binding="defendant.full_name")),
            member("t2", "Notice", field("DEFENDANT", binding="defendant.full_name")),
        ])
        assert len(questions) == 1
        assert questions[0].is_shared is True
        assert {ref.template_id for ref in questions[0].appears_in} == {"t1", "t2"}

    def test_a_legacy_path_and_a_card_path_are_the_same_question(self):
        # A template published before cards and one authored after must
        # recognise each other, or a set would ask the same fact twice.
        questions = build_interview([
            member("t1", "Motion", field("a", binding="party.defendant.name")),
            member("t2", "Notice", field("b", binding="defendant.full_name")),
        ])
        assert len(questions) == 1

    def test_unbound_fields_never_merge_across_documents(self):
        # Two hand-typed blanks sharing a label are not evidence that they are
        # the same fact; merging them would move one document's value into
        # another with nothing reporting it.
        questions = build_interview([
            member("t1", "Motion", field("amount", label="Amount")),
            member("t2", "Notice", field("amount", label="Amount")),
        ])
        assert len(questions) == 2
        assert all(q.key.startswith(MANUAL_KEY_PREFIX) for q in questions)
        assert all(q.is_shared is False for q in questions)

    def test_manual_binding_stays_per_document(self):
        questions = build_interview([
            member("t1", "A", field("note", binding="manual")),
            member("t2", "B", field("note", binding="manual")),
        ])
        assert len(questions) == 2

    def test_required_in_any_document_makes_the_question_required(self):
        questions = build_interview([
            member("t1", "A", field("a", binding="matter.case_number")),
            member("t2", "B", field("b", binding="matter.case_number", required=True)),
        ])
        assert questions[0].required is True

    def test_a_bound_question_is_labelled_by_its_card(self):
        # Not by whichever document happened to be listed first.
        questions = build_interview([
            member("t1", "A", field("def_name", label="DEF NAME", binding="defendant.full_name")),
        ])
        assert questions[0].label == "Defendant — Full name"
        assert questions[0].card == "defendant"

    def test_members_beyond_the_ceiling_are_not_interviewed(self):
        members = [
            member(f"t{index}", f"Doc {index}", field("a", binding="matter.court"))
            for index in range(MAX_SET_MEMBERS + 5)
        ]
        questions = build_interview(members)
        assert len(questions[0].appears_in) == MAX_SET_MEMBERS


class TestExclusions:
    @pytest.mark.parametrize(
        "extra",
        [
            {"included": False},              # author switched it off
            {"value_from": "other_field"},    # already linked inside its template
            {"binding": "item.party_name"},   # resolved per repeat iteration
        ],
    )
    def test_excluded_from_the_interview(self, extra):
        questions = build_interview([member("t1", "A", field("a", **extra))])
        assert questions == []

    def test_malformed_schemas_do_not_raise(self):
        # These run over schemas saved by every past version of the editor.
        assert build_interview([TemplateMember("t1", "A", {})]) == []
        assert build_interview([TemplateMember("t1", "A", {"fields": "nope"})]) == []
        assert build_interview([TemplateMember("t1", "A", {"fields": [None, {}]})]) == []


class TestOrdering:
    def test_shared_card_questions_come_before_per_document_ones(self):
        questions = build_interview([
            member(
                "t1",
                "A",
                field("local", label="Local detail"),
                field("case", binding="matter.case_number"),
            ),
        ])
        assert [q.card for q in questions] == ["matter", ""]

    def test_cards_are_grouped_in_catalogue_order(self):
        questions = build_interview([
            member(
                "t1",
                "A",
                field("d", binding="defendant.full_name"),
                field("f", binding="firm.name"),
                field("m", binding="matter.court"),
            ),
        ])
        # firm, matter, …, defendant — the catalogue's presentation order.
        assert [q.card for q in questions] == ["firm", "matter", "defendant"]


class TestFanOut:
    def test_one_answer_reaches_each_document_under_its_own_field_name(self):
        questions = build_interview([
            member("t1", "Motion", field("def_name", binding="defendant.full_name")),
            member("t2", "Notice", field("DEFENDANT", binding="defendant.full_name")),
        ])
        documents = answers_for_documents(questions, {questions[0].key: "Acme Corp"})
        assert documents == {
            "t1": {"def_name": "Acme Corp"},
            "t2": {"DEFENDANT": "Acme Corp"},
        }

    def test_a_missing_answer_stays_missing(self):
        # Sending "" would render a blank as though it had been supplied, and
        # the existing required-field check would stop reporting it.
        questions = build_interview([
            member("t1", "A", field("a", binding="matter.court")),
        ])
        assert answers_for_documents(questions, {}) == {}
        assert answers_for_documents(questions, {questions[0].key: ""}) == {}

    def test_unanswered_required_reports_in_interview_order(self):
        questions = build_interview([
            member(
                "t1",
                "A",
                field("c", binding="matter.court", required=True),
                field("f", binding="firm.name", required=True),
                field("o", binding="matter.judge"),
            ),
        ])
        missing = unanswered_required(questions, {})
        assert [q.card for q in missing] == ["firm", "matter"]

        answered = {q.key: "x" for q in questions if q.card == "firm"}
        assert [q.card for q in unanswered_required(questions, answered)] == ["matter"]

    def test_whitespace_is_not_an_answer(self):
        questions = build_interview([
            member("t1", "A", field("a", binding="matter.court", required=True)),
        ])
        assert unanswered_required(questions, {questions[0].key: "   "}) == questions
