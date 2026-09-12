"""Executable guarantees for the shared practice resolver.

Every subsystem that reads a matter's free-text ``matter_type`` or
``practice_area`` resolves through this module, so the alias table is the
single place a label maps to a practice.
"""

import pytest

from app.services.practice_resolution import (
    DEFAULT_PRACTICE,
    practice_key,
    practices,
    resolve_practice,
)


@pytest.mark.parametrize(
    "matter_type,expected",
    [
        ("Divorce", "family"),
        ("family_law", "family"),
        ("Custody modification — Ruiz", "family"),
        ("DUI", "criminal"),
        ("Criminal Defense", "criminal"),
        ("Car accident claim", "injury"),
        ("Estate Planning", "estate"),
        ("Wrongful termination", "employment"),
        ("SaaS vendor contract", "business"),
        ("Landlord/tenant eviction", "real_estate"),
        ("Naturalization", "immigration"),
        ("Chapter 7", "bankruptcy"),
        ("Breach of contract lawsuit", "litigation"),
        ("Mediation", "mediation"),
    ],
)
def test_free_text_matter_types_resolve_to_a_practice(matter_type, expected):
    assert resolve_practice(matter_type).slug == expected


@pytest.mark.parametrize(
    "matter_type", ["", None, "   ", "Something we have never handled"]
)
def test_an_unrecognised_type_falls_back_to_the_default_practice(matter_type):
    assert resolve_practice(matter_type) is DEFAULT_PRACTICE


@pytest.mark.parametrize(
    "matter_type,practice_area,expected",
    [
        ("general", "Family Law", "family"),
        ("general", "Mediation", "mediation"),
        ("", "Real Estate", "real_estate"),
        ("DUI", "Family Law", "criminal"),
        ("general", "", "general"),
    ],
)
def test_the_practice_area_answers_when_the_type_says_nothing(
    matter_type, practice_area, expected
):
    """Real files carry the signal in whichever label the firm filled in."""

    assert resolve_practice(matter_type, practice_area).slug == expected


def test_the_resolved_result_exposes_a_slug_and_a_label():
    resolved = resolve_practice("general", "Family Law")

    assert resolved.slug == "family"
    assert resolved.label == "Family and domestic relations"


def test_a_specific_alias_beats_a_generic_one_from_another_practice():
    """ "Contract" belongs to business; "breach of contract" is still a dispute."""

    assert resolve_practice("Contract review").slug == "business"
    assert resolve_practice("Breach of contract").slug == "litigation"


def test_every_alias_is_claimed_by_exactly_one_practice():
    seen: dict[str, str] = {}
    for practice in practices():
        for alias in practice.aliases:
            assert (
                alias not in seen
            ), f"{alias} claimed by {seen.get(alias)} and {practice.slug}"
            seen[alias] = practice.slug


@pytest.mark.parametrize(
    "value,expected_slug",
    [
        ("Family Law", "family"),
        ("FAMILY LAW", "family"),
        ("Dissolution of Marriage", "family"),
        ("family_law", "family"),
        ("general", "general"),
    ],
)
def test_practice_key_canonicalises_a_recognised_label(value, expected_slug):
    assert practice_key(value) == expected_slug


@pytest.mark.parametrize("value", [None, "", "   ", "Something unrecognised"])
def test_practice_key_returns_nothing_for_an_unrecognised_label(value):
    assert practice_key(value) is None
