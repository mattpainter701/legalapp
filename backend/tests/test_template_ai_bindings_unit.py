"""What an AI proposal may say about where a field's value comes from.

The model is sent the card catalogue and told to choose from it. This covers
what happens when it does not — the case that decides whether a hallucinated
data source can reach a fill.
"""

from io import BytesIO

import pytest

from app.services.template_ai_assist import (
    AiFieldProposal,
    proposed_binding_entry,
    reconcile_ai_template_fields,
    reviewed_binding,
)
from app.services.template_bindings import MANUAL_BINDING
from app.services.template_intake import TemplateAnalysis


def proposal(binding=None, **overrides):
    values = {"name": "defendant_name", "label": "Defendant full name"}
    if binding is not None:
        values["binding"] = binding
    values.update(overrides)
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


def _text_analysis(body: str) -> TemplateAnalysis:
    return TemplateAnalysis(
        title="Notice",
        format="markdown",
        body=body,
        body_preview=body,
        extracted_text=body,
        source_text=body,
        variable_schema={"fields": []},
        branding_profile={},
    )


def _docx_analysis(paragraph: str) -> tuple[TemplateAnalysis, bytes]:
    from docx import Document

    document = Document()
    document.add_paragraph(paragraph)
    source = BytesIO()
    document.save(source)
    analysis = TemplateAnalysis(
        title="Notice",
        format="docx",
        body=paragraph,
        body_preview=paragraph,
        extracted_text=paragraph,
        source_text=paragraph,
        variable_schema={"fields": []},
        branding_profile={},
    )
    return analysis, source.getvalue()


def _added(mapped: list[dict], name: str) -> dict:
    return next(field for field in mapped if field["name"] == name)


class TestWhatReachesTheSchema:
    """The binding key a proposed field is stored with, not what the helper says.

    Storing ``manual`` is a decision: it means "always typed by hand" and turns
    Smart Fill's field-name matching off for that field. Before bindings were
    part of a proposal, an AI-added field carried no ``binding`` key and Smart
    Filled by name; a proposal the model left blank must still land that way.
    """

    def test_a_blank_proposal_stores_no_binding_so_name_matching_still_runs(self):
        assert proposed_binding_entry(proposal(None)) == {}
        assert proposed_binding_entry(proposal("")) == {}

    def test_an_explicit_manual_is_stored_as_manual(self):
        # The model may say "this is typed by hand"; that is a decision it is
        # allowed to make, and it is kept.
        assert proposed_binding_entry(proposal(MANUAL_BINDING)) == {
            "binding": MANUAL_BINDING
        }

    def test_a_catalogue_path_is_stored_canonically(self):
        assert proposed_binding_entry(proposal("party.defendant.name")) == {
            "binding": "defendant.full_name"
        }

    @pytest.mark.parametrize(
        "binding",
        [
            "opposing_expert.full_name",  # invented card
            "defendant.middle_initial",  # invented field
            "defendant.2.full_name",  # a role instance nobody chose
            "defendant.*.full_name",
        ],
    )
    def test_a_refused_path_stores_no_binding_rather_than_manual(self, binding):
        # The invention is dropped, but the field is not condemned to hand
        # entry for it: with no binding stored it Smart Fills by name, which
        # is what would have happened had the model said nothing.
        assert proposed_binding_entry(proposal(binding)) == {}

    def test_text_proposals_carry_the_decision_into_the_field_map(self):
        analysis = _text_analysis(
            "Defendant: John Doe. Docket: 12-CV-99. Notes: see file. Second: Jane Roe."
        )
        mapped, unmapped = reconcile_ai_template_fields(
            analysis=analysis,
            file_bytes=b"",
            proposals=[
                proposal(
                    "defendant.full_name", name="defendant", source_text="John Doe"
                ),
                proposal(None, name="docket", source_text="12-CV-99"),
                proposal(MANUAL_BINDING, name="notes", source_text="see file"),
                proposal(
                    "defendant.2.full_name", name="second", source_text="Jane Roe"
                ),
            ],
        )
        assert unmapped == []
        assert _added(mapped, "defendant")["binding"] == "defendant.full_name"
        assert "binding" not in _added(mapped, "docket")
        assert _added(mapped, "notes")["binding"] == MANUAL_BINDING
        assert "binding" not in _added(mapped, "second")

    def test_docx_proposals_carry_the_decision_into_the_field_map(self):
        analysis, file_bytes = _docx_analysis(
            "Defendant John Doe, docket 12-CV-99, notes see file."
        )
        mapped, unmapped = reconcile_ai_template_fields(
            analysis=analysis,
            file_bytes=file_bytes,
            proposals=[
                proposal(
                    "defendant.full_name", name="defendant", source_text="John Doe"
                ),
                proposal(None, name="docket", source_text="12-CV-99"),
                proposal(MANUAL_BINDING, name="notes", source_text="see file"),
            ],
        )
        assert unmapped == []
        assert _added(mapped, "defendant")["binding"] == "defendant.full_name"
        assert "binding" not in _added(mapped, "docket")
        assert _added(mapped, "notes")["binding"] == MANUAL_BINDING
