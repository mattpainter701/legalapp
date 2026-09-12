"""What an AI proposal may say about where a field's value comes from.

The model is sent the card catalogue and told to choose from it. This covers
what happens when it does not — the case that decides whether a hallucinated
data source can reach a fill.
"""

import pytest

from app.services.template_ai_assist import AiFieldProposal, reviewed_binding
from app.services.template_bindings import MANUAL_BINDING


def proposal(binding=None):
    values = {"name": "defendant_name", "label": "Defendant full name"}
    if binding is not None:
        values["binding"] = binding
    return AiFieldProposal(**values)


class TestReviewedBinding:
    def test_a_catalogue_path_is_kept(self):
        assert reviewed_binding(proposal("defendant.full_name")) == "defendant.full_name"

    def test_a_pre_card_path_is_translated_to_its_card(self):
        # The model may know the older vocabulary; both spellings name the
        # same record, and storing one form keeps new templates consistent.
        assert reviewed_binding(proposal("party.defendant.name")) == "defendant.full_name"

    @pytest.mark.parametrize(
        "binding",
        [
            "defendant.middle_initial",     # field the catalogue has no idea about
            "opposing_expert.full_name",    # card that does not exist
            "matter.case_number.extra",     # not a path at all
            "../../etc/passwd",
            "{{ lookup('secrets') }}",
        ],
    )
    def test_an_invented_path_lands_as_manual(self, binding):
        # The located source text is still useful, so the field arrives — but
        # unbound, for a human to set. Keeping the invented path would let a
        # fill silently find nothing and nobody would know why.
        assert reviewed_binding(proposal(binding)) == MANUAL_BINDING

    @pytest.mark.parametrize("binding", [None, "", "   ", MANUAL_BINDING])
    def test_saying_nothing_means_manual(self, binding):
        assert reviewed_binding(proposal(binding)) == MANUAL_BINDING

    @pytest.mark.parametrize(
        "binding", ["defendant.2.full_name", "defendant.*.full_name"]
    )
    def test_a_role_instance_is_refused_even_though_the_path_is_valid(self, binding):
        # Which of a matter's defendants a blank means is a decision about that
        # matter; the document text cannot settle it. Collapsing it to the
        # first defendant would be the same guess made quietly, so the field
        # lands unbound for a human to decide.
        assert reviewed_binding(proposal(binding)) == MANUAL_BINDING

    def test_the_schema_still_forbids_unknown_keys(self):
        # The proposal contract stays closed: a model cannot smuggle in a new
        # field by naming it.
        with pytest.raises(ValueError):
            AiFieldProposal(name="x", label="X", overlay={"page": 1})
