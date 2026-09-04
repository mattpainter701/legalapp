"""Authored metadata as it passes through the reviewed-schema validator.

``_reviewed_variable_schema`` is the boundary where a client's proposed field
map meets the server's own analysis of the source. Bindings and conditions ride
through it as customer-authored metadata, so they are validated here rather
than trusted.
"""

import json

import pytest
from fastapi import HTTPException

from app.routers.document_templates import (
    _reviewed_variable_schema,
    extract_template_variables,
    render_template,
)


def _reviewed(fields, discovered=None):
    return _reviewed_variable_schema(
        json.dumps({"fields": fields}), discovered or {"fields": []}
    )


class TestBindingReview:
    def test_a_catalogue_path_survives_review(self):
        schema = _reviewed([{"name": "client_name", "binding": "client.name"}])
        assert schema["fields"][0]["binding"] == "client.name"

    def test_the_manual_marker_survives_review(self):
        schema = _reviewed([{"name": "initials", "binding": "manual"}])
        assert schema["fields"][0]["binding"] == "manual"

    def test_an_unknown_binding_is_rejected(self):
        with pytest.raises(HTTPException) as caught:
            _reviewed([{"name": "client_name", "binding": "matter.secrets"}])
        assert caught.value.status_code == 422
        assert "matter.secrets" in caught.value.detail

    def test_an_empty_binding_is_dropped_rather_than_stored(self):
        # A cleared picker sends "", which means "no binding", not a binding
        # named the empty string.
        schema = _reviewed([{"name": "client_name", "binding": "   "}])
        assert "binding" not in schema["fields"][0]

    def test_a_field_without_a_binding_is_untouched(self):
        schema = _reviewed([{"name": "client_name"}])
        assert "binding" not in schema["fields"][0]


class TestLogicReview:
    def test_a_condition_on_a_declared_field_survives_review(self):
        schema = _reviewed(
            [
                {"name": "is_entity"},
                {
                    "name": "authority_clause",
                    "logic": {"field": "is_entity", "operator": "truthy"},
                },
            ]
        )
        assert schema["fields"][1]["logic"]["operator"] == "truthy"

    def test_a_condition_on_an_undeclared_field_is_rejected(self):
        # It would always evaluate the same way, which reads as a clause the
        # template silently dropped.
        with pytest.raises(HTTPException) as caught:
            _reviewed(
                [{"name": "x", "logic": {"field": "ghost", "operator": "present"}}]
            )
        assert caught.value.status_code == 422
        assert "ghost" in caught.value.detail

    def test_a_malformed_operator_is_rejected(self):
        with pytest.raises(HTTPException) as caught:
            _reviewed([{"name": "x", "logic": {"field": "x", "operator": "regex"}}])
        assert caught.value.status_code == 422


class TestLogicMarkersAreNotVariables:
    def test_markers_are_excluded_from_extracted_variables(self):
        # Reporting them as variables would put "#if entity" on the generation
        # form and fail the PDF body/field-map cross-check.
        body = (
            "{{#if entity}}{{client_name}}{{/if}}"
            "{{#each parties}}{{party_name}}{{/each}}"
        )
        assert extract_template_variables(body) == ["client_name", "party_name"]

    def test_a_body_of_only_markers_reports_no_variables(self):
        assert extract_template_variables("{{#if a}}text{{/if}}") == []

    def test_substitution_still_leaves_unknown_placeholders_alone(self):
        assert render_template("{{a}} {{b}}", {"a": "1"}) == "1 {{b}}"
