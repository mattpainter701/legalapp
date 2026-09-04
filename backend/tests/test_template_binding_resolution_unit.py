"""Resolving a field's value through its declared binding."""

import uuid

import pytest

from app.routers.document_templates import (
    _bound_suggestion,
    _repeat_collections,
    _schema_for_values,
)
from app.schemas.document_template import DocumentTemplateVariableSuggestion
from app.services.template_bindings import MANUAL_BINDING


def _candidate(value, **provenance):
    return DocumentTemplateVariableSuggestion(
        variable="ignored",
        suggested_value=value,
        source_type="matter",
        source_field="case_number",
        provenance={"source_type": "matter", **provenance},
        confidence=1.0,
        review_required=False,
    )


class TestBoundSuggestion:
    def test_a_resolved_binding_carries_the_value_and_its_path(self):
        candidates = {"case_number": _candidate("24-CV-9")}
        result = _bound_suggestion("docket", "matter.case_number", candidates)
        assert result.variable == "docket"
        assert result.suggested_value == "24-CV-9"
        assert result.provenance["binding"] == "matter.case_number"
        assert result.provenance["binding_label"] == "Case number"
        assert result.provenance["source_type"] == "matter"

    def test_a_binding_never_falls_back_to_name_matching(self):
        # The field is named the same as an unrelated alias that *is*
        # resolvable. Honouring that would fill from a record the customer
        # did not bind to, which is the surprise bindings exist to remove.
        candidates = {"court": _candidate("Superior Court")}
        result = _bound_suggestion("court", "matter.judge", candidates)
        assert result.suggested_value is None
        assert result.provenance["status"] == "binding_unresolved"

    def test_an_unresolved_binding_names_the_path_that_failed(self):
        result = _bound_suggestion("judge", "matter.judge", {})
        assert result.provenance == {
            "status": "binding_unresolved",
            "binding": "matter.judge",
            "binding_label": "Judge",
        }
        assert result.review_required is True

    def test_a_manual_binding_reports_manual_entry(self):
        result = _bound_suggestion("initials", MANUAL_BINDING, {"initials": _candidate("X")})
        assert result.suggested_value is None
        assert result.provenance["status"] == "manual_entry"
        assert result.review_required is True


class _Contact:
    def __init__(self, name, email=None, phone=None):
        self.display_name = name
        self.email = email
        self.phone = phone


class _Party:
    def __init__(self, name, role, is_primary=False):
        self.id = uuid.uuid4()
        self.role = role
        self.is_primary = is_primary
        self.created_at = 0
        self.contact = _Contact(name, f"{role}@example.test", "555")


class TestRepeatCollections:
    def test_parties_are_grouped_by_caption_role(self):
        parties = [
            _Party("Acme LLC", "plaintiff", is_primary=True),
            _Party("Bo Li", "defendant"),
            _Party("Cy Ng", "plaintiff"),
        ]
        result = _repeat_collections(parties)
        assert [item["party_name"] for item in result["parties"]] == [
            "Acme LLC",
            "Bo Li",
            "Cy Ng",
        ]
        assert [item["party_name"] for item in result["plaintiffs"]] == [
            "Acme LLC",
            "Cy Ng",
        ]
        assert [item["party_name"] for item in result["defendants"]] == ["Bo Li"]

    def test_items_expose_the_declared_fields(self):
        [item] = _repeat_collections([_Party("Acme LLC", "plaintiff")])["plaintiffs"]
        assert set(item) == {
            "party_name",
            "party_role",
            "party_email",
            "party_phone",
        }

    def test_nameless_parties_are_excluded(self):
        # A nameless row would render an empty bullet or signature block.
        nameless = _Party("", "plaintiff")
        nameless.contact.display_name = None
        assert _repeat_collections([nameless]) == {
            "parties": [],
            "plaintiffs": [],
            "defendants": [],
        }

    def test_no_parties_yields_empty_collections(self):
        assert _repeat_collections([]) == {
            "parties": [],
            "plaintiffs": [],
            "defendants": [],
        }


class TestSchemaForValues:
    SCHEMA = {
        "fields": [
            {
                "name": "spouse_name",
                "required": True,
                "docx_anchor": {"start": 1},
                "logic": {"field": "married", "operator": "truthy"},
            },
            {"name": "married", "required": True},
        ]
    }

    def test_a_switched_off_field_stops_being_required(self):
        adjusted = _schema_for_values(self.SCHEMA, {"married": "no"})
        by_name = {field["name"]: field for field in adjusted["fields"]}
        assert by_name["spouse_name"]["required"] is False
        assert by_name["married"]["required"] is True

    def test_authoritative_keys_survive_the_adjustment(self):
        adjusted = _schema_for_values(self.SCHEMA, {"married": "no"})
        assert adjusted["fields"][0]["docx_anchor"] == {"start": 1}

    def test_a_satisfied_condition_leaves_the_schema_untouched(self):
        assert _schema_for_values(self.SCHEMA, {"married": "yes"}) is self.SCHEMA

    @pytest.mark.parametrize("schema", [None, "text", {"fields": "no"}, {"fields": []}])
    def test_schemas_without_conditions_pass_through(self, schema):
        assert _schema_for_values(schema, {"a": "1"}) is schema
