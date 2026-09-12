"""Cards: the owner of a template field.

These are pure-function tests with no database.  The contract they hold is
that adding cards changed *addressing* and nothing else: every path a published
template may already carry still resolves, and still resolves through the same
Smart Fill alias it did before.
"""

import pytest

from app.services.template_bindings import (
    MANUAL_BINDING,
    alias_for_binding,
    catalogue as binding_catalogue,
    is_item_binding,
)
from app.services.template_cards import (
    ALL_INSTANCES,
    MAX_ROLE_INSTANCES,
    CardKind,
    alias_for,
    canonical_path,
    card,
    cards,
    indexed_alias,
    is_valid_card_path,
    resolve,
    role_cards,
)


class TestCatalogueShape:
    def test_card_keys_are_unique(self):
        keys = [entry.key for entry in cards()]
        assert len(keys) == len(set(keys))

    def test_field_keys_are_unique_within_a_card(self):
        for entry in cards():
            keys = [item.key for item in entry.fields]
            assert len(keys) == len(set(keys)), entry.key

    def test_record_backed_aliases_are_unique_across_cards(self):
        # Two card fields sharing an alias would resolve to the same record
        # while claiming to name different subjects.
        aliases = [
            item.alias
            for entry in cards()
            for item in entry.fields
            if item.alias
        ]
        assert len(aliases) == len(set(aliases))

    def test_item_card_fields_carry_no_alias(self):
        # They resolve per iteration of a repeating section, so there is no
        # single record — and no alias — behind them.
        item = card("item")
        assert item.kind is CardKind.ITEM
        assert all(not entry.alias for entry in item.fields)

    def test_only_role_cards_have_instances(self):
        for entry in cards():
            expected = MAX_ROLE_INSTANCES if entry.kind is CardKind.ROLE else 1
            assert entry.max_instances == expected

    def test_role_cards_name_a_party_role(self):
        assert {entry.key for entry in role_cards()} == {"plaintiff", "defendant"}
        assert all(entry.party_role for entry in role_cards())


class TestLegacyCompatibility:
    """The migration contract. A failure here is a customer-visible regression."""

    def test_every_legacy_binding_resolves_to_a_card_field(self):
        for entry in binding_catalogue():
            assert resolve(entry.path) is not None, entry.path

    def test_every_legacy_binding_keeps_its_alias(self):
        # Cards changed addressing, not resolution. A card field that resolved
        # through a different alias would silently re-source a clause.
        for entry in binding_catalogue():
            if is_item_binding(entry.path):
                continue
            ref = resolve(entry.path)
            assert alias_for(ref) == entry.alias, entry.path

    def test_canonical_path_translates_without_rewriting(self):
        assert canonical_path("party.defendant.name") == "defendant.full_name"
        assert canonical_path("party.defendant.names") == f"defendant.{ALL_INSTANCES}.full_name"
        assert canonical_path("client.address.city") == "client.city"

    def test_canonical_path_passes_through_unknown_paths(self):
        # A path this catalogue does not know stays itself, so the caller can
        # report it as unresolved rather than guessing a near match.
        assert canonical_path("something.invented") == "something.invented"

    def test_a_card_path_is_its_own_canonical_form(self):
        assert canonical_path("defendant.full_name") == "defendant.full_name"


class TestResolution:
    def test_resolves_a_singleton_card_field(self):
        ref = resolve("matter.case_number")
        assert ref.card.key == "matter"
        assert ref.field.key == "case_number"
        assert ref.instance is None
        assert ref.path == "matter.case_number"

    def test_resolves_a_role_instance(self):
        ref = resolve("defendant.2.full_name")
        assert ref.card.key == "defendant"
        assert ref.instance == 2
        assert ref.label == "Defendant 2 — Full name"

    def test_first_instance_is_addressable_both_ways(self):
        assert resolve("defendant.1.full_name").field == resolve("defendant.full_name").field
        # …and resolves through the same alias, so the two spellings cannot
        # disagree about which record they name.
        assert alias_for(resolve("defendant.1.full_name")) == alias_for(resolve("defendant.full_name"))

    def test_resolves_every_instance(self):
        ref = resolve(f"defendant.{ALL_INSTANCES}.full_name")
        assert ref.instance == ALL_INSTANCES
        assert alias_for(ref) == "defendant_names"

    @pytest.mark.parametrize(
        "path",
        [
            "",
            MANUAL_BINDING,
            "defendant",
            "defendant.full_name.extra",
            "Defendant.full_name",
            "unknown_card.full_name",
            "matter.unknown_field",
            # An instance on a singleton names a record that cannot exist.
            "matter.2.case_number",
            # Beyond the addressable ceiling.
            f"defendant.{MAX_ROLE_INSTANCES + 1}.full_name",
            # "Every instance" of a field with no plural form.
            f"defendant.{ALL_INSTANCES}.email",
        ],
    )
    def test_rejects(self, path):
        assert resolve(path) is None
        assert is_valid_card_path(path) is False

    def test_custom_field_paths_are_not_card_paths(self):
        # template_bindings.custom_binding owns those; resolving them here
        # would give one path two owners.
        path = "custom.matter.0f7c4b2e-1a6d-4c8e-9b31-2d5a7e6f1c04"
        assert resolve(path) is None

    def test_resolve_tolerates_non_string_input(self):
        assert resolve(None) is None
        assert canonical_path(None) == ""


class TestInstanceAliases:
    def test_indexed_alias_inserts_the_index_after_the_role(self):
        entry = card("defendant").field("full_name")
        assert indexed_alias("defendant", 2, entry) == "defendant_2_name"

    def test_indexed_alias_falls_back_to_the_field_key(self):
        entry = card("defendant").field("email")
        assert indexed_alias("defendant", 3, entry) == "defendant_3_email"

    def test_indexed_aliases_never_collide_with_singular_or_plural(self):
        reserved = {"defendant_name", "defendant_names", "defendants", "defendant_email", "defendant_phone"}
        for instance in range(2, MAX_ROLE_INSTANCES + 1):
            for entry in card("defendant").fields:
                assert indexed_alias("defendant", instance, entry) not in reserved

    def test_every_role_alias_is_resolvable_from_the_flat_catalogue(self):
        # Instance 1 must keep resolving through an alias the existing
        # candidate builder actually emits.
        for entry in role_cards():
            ref = resolve(f"{entry.key}.full_name")
            assert alias_for_binding(f"party.{entry.key}.name") == alias_for(ref)
