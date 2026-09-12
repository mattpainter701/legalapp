"""Whether a field label can identify that field to a human.

These are the exact shapes the 2026-09-08 live audit found shipping, plus the
good labels the gate must not reject — a publish-time block that fires on a
merely unusual label would be worse than the problem it solves.
"""

import pytest

from app.services.template_labels import (
    label_needs_rename,
    label_problem,
    unusable_labels,
)


class TestRejectsWhatTheAuditFound:
    @pytest.mark.parametrize("label", ["And", "Shall Pay To", "By 2", "of the", "the"])
    def test_rejected(self, label):
        assert label_needs_rename(label) is True

    def test_the_reason_says_what_is_wrong_and_quotes_the_label(self):
        # A reviewer fixing ten of these needs to know which is which.
        assert "'And'" in label_problem("And")
        assert "does not name anything" in label_problem("And")
        assert "cut off mid-phrase" in label_problem("Shall Pay To")

    @pytest.mark.parametrize("label", ["---", "42"])
    def test_a_wordless_label_is_rejected(self, label):
        assert label_needs_rename(label) is True

    @pytest.mark.parametrize("label", ["", "   ", None])
    def test_a_field_with_neither_label_nor_name_is_rejected(self, label):
        assert label_needs_rename(label, "") is True


class TestAcceptsRealLabels:
    @pytest.mark.parametrize(
        "label",
        [
            "Defendant full name",
            "Case number",
            # A trailing index is how a real label distinguishes repeats.
            "Witness 2",
            "Signature 3",
            "Date of birth",
            "Amount payable",
            "Plaintiff's address",
            # Single content words are fine; brevity is not the problem.
            "Court",
            "Judge",
        ],
    )
    def test_accepted(self, label):
        assert label_needs_rename(label) is False, label

    def test_a_trailing_index_does_not_rescue_a_fragment(self):
        # "Witness 2" is a good label; "By 2" is the same fragment with a
        # number after it.
        assert label_needs_rename("Witness 2") is False
        assert label_needs_rename("By 2") is True


class TestUnlabelledFieldsFallBackToTheName:
    """The common shape: an author who never typed a separate label.

    Every editor surface renders ``label || name``, so the name is what a
    reader sees — and refusing a field for having no label would block
    templates that have been published for years.
    """

    @pytest.mark.parametrize(
        "name", ["client_name", "case_number", "defendant-full-name", "Court"]
    )
    def test_a_usable_name_publishes_without_a_label(self, name):
        assert label_needs_rename(None, name) is False
        assert label_needs_rename("", name) is False

    @pytest.mark.parametrize("name", ["and", "shall_pay_to", "by_2"])
    def test_an_unusable_name_is_still_refused(self, name):
        assert label_needs_rename(None, name) is True

    def test_the_reason_says_the_field_is_named_not_labelled(self):
        # A reviewer needs to know which text to change.
        assert "named" in label_problem(None, "and")
        assert "labelled" in label_problem("And", "and")

    def test_a_label_is_judged_even_when_the_name_would_pass(self):
        # The label is what a reader sees when one exists.
        assert label_needs_rename("And", "defendant_full_name") is True

    def test_a_name_is_read_the_way_it_is_displayed(self):
        # client_name reads as two words, not one unknown token.
        assert label_problem(None, "client_name") == ""


class TestSchemaSweep:
    def test_reports_every_included_field_that_needs_a_rename(self):
        problems = unusable_labels({
            "fields": [
                {"name": "a", "label": "And"},
                {"name": "b", "label": "Case number"},
                {"name": "c", "label": "Shall Pay To"},
            ]
        })
        assert [name for name, _ in problems] == ["a", "c"]

    def test_the_ordinary_unlabelled_field_publishes(self):
        # The shape almost every existing template uses.
        assert unusable_labels({"fields": [{"name": "client_name"}]}) == []

    def test_skips_fields_the_author_switched_off(self):
        # They are not part of the document, so their labels cannot mislead.
        assert unusable_labels({
            "fields": [{"name": "a", "label": "And", "included": False}]
        }) == []

    def test_skips_signing_fields(self):
        # Their signer role, not their label, identifies them at signature.
        assert unusable_labels({
            "fields": [{"name": "a", "label": "By", "signer_role": "client"}]
        }) == []

    @pytest.mark.parametrize(
        "schema",
        [None, {}, {"fields": "nope"}, {"fields": [None, {}, {"label": "And"}]}],
    )
    def test_a_malformed_schema_never_blocks_a_publish(self, schema):
        # This runs over templates saved by every past version of the editor;
        # it must not be the thing that stops one being published.
        assert unusable_labels(schema) == []
