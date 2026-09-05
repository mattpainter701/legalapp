"""Field data bindings and the semantic-metadata boundary."""

import pytest

from app.services.template_bindings import (
    ITEM_BINDING_PREFIX,
    MANUAL_BINDING,
    alias_for_binding,
    binding_label,
    catalogue,
    collections,
    declared_bindings,
    is_valid_binding,
    is_item_binding,
    is_valid_collection,
    item_key,
)
from app.services.template_logic import TemplateLogicError
from app.services.template_semantics import (
    SEMANTIC_FIELD_KEYS,
    TemplateSemanticsError,
    is_semantic_only_change,
    validate_semantic_metadata,
)


class TestCatalogue:
    def test_every_path_is_unique(self):
        paths = [entry.path for entry in catalogue()]
        assert len(paths) == len(set(paths))

    def test_record_backed_aliases_are_unique(self):
        # Two catalogue entries sharing an alias would resolve to the same
        # record while claiming to name different ones.
        aliases = [entry.alias for entry in catalogue() if entry.alias]
        assert len(aliases) == len(set(aliases))

    def test_item_bindings_carry_no_alias(self):
        # They resolve per iteration of a repeating section, so there is no
        # single record — and no alias — behind them.
        item_entries = [entry for entry in catalogue() if is_item_binding(entry.path)]
        assert item_entries
        assert all(entry.alias == "" for entry in item_entries)
        assert all(alias_for_binding(entry.path) == "" for entry in item_entries)

    def test_entries_carry_presentation_metadata(self):
        assert all(entry.label and entry.group for entry in catalogue())

    def test_known_paths_resolve_to_their_alias(self):
        assert alias_for_binding("matter.case_number") == "case_number"
        assert alias_for_binding("client.address.city") == "client_city"

    def test_manual_and_unknown_paths_resolve_to_nothing(self):
        # Resolving either to an alias would reintroduce the accidental
        # name-collision fill that bindings exist to remove.
        assert alias_for_binding(MANUAL_BINDING) is None
        assert alias_for_binding("matter.not_a_field") is None

    def test_validity_covers_the_manual_marker(self):
        assert is_valid_binding(MANUAL_BINDING)
        assert is_valid_binding("attorney.name")
        assert not is_valid_binding("matter.__dict__")
        assert not is_valid_binding("")

    def test_labels_are_available_for_provenance(self):
        assert binding_label("matter.court") == "Court"
        assert binding_label(MANUAL_BINDING) == "Entered by hand"
        assert binding_label("nope") is None

    def test_item_bindings_name_a_collection_item_field(self):
        assert is_item_binding("item.party_name")
        assert not is_item_binding("client.name")
        assert not is_item_binding(None)
        assert item_key("item.party_name") == "party_name"
        assert item_key("client.name") == ""
        assert ITEM_BINDING_PREFIX == "item."

    def test_every_item_binding_names_a_field_a_collection_supplies(self):
        # An item binding naming a key no collection emits would resolve to
        # nothing on every iteration.
        supplied = {
            field for entry in collections() for field in entry.item_fields
        }
        for entry in catalogue():
            if is_item_binding(entry.path):
                assert item_key(entry.path) in supplied

    def test_collections_declare_their_item_fields(self):
        assert is_valid_collection("parties")
        assert not is_valid_collection("everything")
        assert all(entry.item_fields for entry in collections())


class TestDeclaredBindings:
    def test_valid_bindings_are_returned_by_field_name(self):
        schema = {
            "fields": [
                {"name": "case", "binding": "matter.case_number"},
                {"name": "typed", "binding": MANUAL_BINDING},
            ]
        }
        assert declared_bindings(schema) == {
            "case": "matter.case_number",
            "typed": MANUAL_BINDING,
        }

    @pytest.mark.parametrize(
        "schema",
        [
            None,
            "not-a-dict",
            {"fields": "not-a-list"},
            {"fields": [None, 7]},
            {"fields": [{"name": "", "binding": "matter.court"}]},
            {"fields": [{"name": "x", "binding": 42}]},
            {"fields": [{"name": "x", "binding": "   "}]},
        ],
    )
    def test_malformed_schemas_yield_nothing_rather_than_raising(self, schema):
        # This runs on the read path for templates saved before bindings
        # existed, so it must never block generation.
        assert declared_bindings(schema) == {}

    def test_a_path_the_catalogue_no_longer_knows_still_counts_as_declared(self):
        # Save-time validation rejects unknown paths, so this can only mean the
        # catalogue changed under an existing template. Dropping the binding
        # here would silently hand the field back to name matching.
        assert declared_bindings(
            {"fields": [{"name": "x", "binding": "matter.retired_path"}]}
        ) == {"x": "matter.retired_path"}


class TestSemanticMetadata:
    def test_authored_keys_do_not_count_as_structural_change(self):
        current = {"fields": [{"name": "x", "docx_anchor": {"start": 1}, "label": "X"}]}
        proposed = {
            "fields": [
                {
                    "name": "x",
                    "docx_anchor": {"start": 1},
                    "label": "Client",
                    "binding": "client.name",
                    "logic": {"field": "x", "operator": "present"},
                }
            ]
        }
        assert is_semantic_only_change(current, proposed)

    @pytest.mark.parametrize(
        "proposed",
        [
            {"fields": [{"name": "x", "docx_anchor": {"start": 2}}]},
            {"fields": [{"name": "renamed", "docx_anchor": {"start": 1}}]},
            {"fields": []},
            {"fields": [{"name": "x", "docx_anchor": {"start": 1}}, {"name": "y"}]},
            {"fields": [{"name": "x", "docx_anchor": {"start": 1}}], "version": 2},
        ],
    )
    def test_structural_changes_are_detected(self, proposed):
        current = {"fields": [{"name": "x", "docx_anchor": {"start": 1}}]}
        assert not is_semantic_only_change(current, proposed)

    def test_field_order_is_structural(self):
        # Intake assigns meaning to field order, so a reorder is not cosmetic.
        current = {"fields": [{"name": "a"}, {"name": "b"}]}
        assert not is_semantic_only_change(
            current, {"fields": [{"name": "b"}, {"name": "a"}]}
        )

    def test_semantic_keys_are_the_documented_set(self):
        assert SEMANTIC_FIELD_KEYS == {"binding", "label", "description", "logic"}

    def test_unknown_binding_is_rejected(self):
        with pytest.raises(TemplateSemanticsError):
            validate_semantic_metadata(
                {"fields": [{"name": "x", "binding": "matter.secret"}]}
            )

    def test_malformed_logic_is_rejected(self):
        with pytest.raises(TemplateLogicError):
            validate_semantic_metadata(
                {"fields": [{"name": "x", "logic": {"operator": "regex"}}]}
            )

    def test_logic_must_reference_a_field_the_template_defines(self):
        with pytest.raises(TemplateLogicError):
            validate_semantic_metadata(
                {"fields": [{"name": "x", "logic": {"field": "ghost", "operator": "present"}}]}
            )

    @pytest.mark.parametrize("schema", [None, "text", {}, {"fields": "no"}])
    def test_schemas_without_fields_are_accepted(self, schema):
        validate_semantic_metadata(schema)
