"""Plugin suggestions resolve the matter's practice through the shared alias table."""

from app.services.plugins.manifest import suggest_plugin_for_matter


def test_a_breach_of_contract_matter_keeps_its_existing_plugin():
    """The haystack match is kept, so tenants that relied on it do not regress."""

    assert (
        suggest_plugin_for_matter(practice_area="Breach of Contract")
        == "commercial-legal"
    )


def test_a_family_law_matter_suggests_the_family_plugin():
    assert suggest_plugin_for_matter(practice_area="Family Law") == "family-law"


def test_a_resolved_practice_matches_a_plugin_term_missing_from_the_haystack():
    """ "Dissolution of Marriage" never mentions "family", yet it is one."""

    assert (
        suggest_plugin_for_matter(
            matter_type="general", practice_area="Dissolution of Marriage"
        )
        == "family-law"
    )


def test_unrecognised_text_still_suggests_nothing():
    assert (
        suggest_plugin_for_matter(
            matter_type="general", practice_area="Intergalactic treaty"
        )
        is None
    )


def test_empty_labels_suggest_nothing():
    assert suggest_plugin_for_matter() is None
    assert suggest_plugin_for_matter(practice_area="  ") is None
