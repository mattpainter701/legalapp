"""Rule matching reads practice labels through the shared alias table.

A rule and a matter often name the same practice differently: a rule keyed to
"family" must fire for a matter typed "Dissolution of Marriage". That widening
is deliberately one-directional. A rule scoped to one kind of work inside a
practice — "Adoption", "Chapter 7", "DUI" — matches only labels meaning that
same work, because these rules create tasks and draft documents and the
practice packs put adoptions and divorces, or Chapter 7 and Chapter 13, in one
pack. Labels the alias table does not recognize keep the historical exact
normalized comparison.
"""

import uuid

from app.models.plugin import Matter
from app.models.workflow_automation import MatterWorkflowAutomationRule
from app.services import workflow_automations


def _rule(**overrides) -> MatterWorkflowAutomationRule:
    values = {
        "tenant_id": uuid.uuid4(),
        "name": "Matcher",
        "trigger_event": "matter_created",
        "template_id": uuid.uuid4(),
        "status": "draft",
        "definition_sha256": "c" * 64,
    }
    values.update(overrides)
    return MatterWorkflowAutomationRule(**values)


def _matter(**overrides) -> Matter:
    values = {
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "slug": f"match-{uuid.uuid4().hex}",
        "matter_name": "Matching matter",
    }
    values.update(overrides)
    return Matter(**values)


def test_a_rule_keyed_to_a_slug_matches_a_matters_alias():
    rule = _rule(match_practice_area="family")
    matter = _matter(practice_area="Dissolution of Marriage")

    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_a_rule_keyed_to_a_practice_label_matches_a_curated_sibling():
    rule = _rule(match_practice_area="Family Law")
    matter = _matter(practice_area="Divorce")

    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_a_rule_keyed_to_one_kind_of_work_does_not_widen_to_its_practice():
    """The direction that would put an adoption automation on every divorce."""
    rule = _rule(match_practice_area="Dissolution of Marriage")
    matter = _matter(practice_area="Family Law")

    assert not workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_siblings_inside_one_practice_never_match_each_other():
    for rule_value, matter_value in (
        ("Adoption", "Divorce"),
        ("Chapter 7", "Chapter 13"),
        ("DUI", "Felony possession"),
        ("NDA", "M&A"),
    ):
        rule = _rule(match_matter_type=rule_value)
        matter = _matter(matter_type=matter_value)

        assert not workflow_automations.rule_matches(
            rule, matter, trigger_event="matter_created"
        ), f"{rule_value!r} must not match {matter_value!r}"


def test_a_specific_rule_still_matches_its_own_label():
    rule = _rule(match_matter_type="Adoption")
    matter = _matter(matter_type=" adoption ")

    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_the_general_pack_is_never_a_scope():
    """Everything unrecognised falls to general; scoping to it scopes to noise."""
    rule = _rule(match_matter_type="general")
    matter = _matter(matter_type="Other")

    assert not workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_matching_stays_case_insensitive():
    rule = _rule(match_matter_type="FAMILY LAW")
    matter = _matter(matter_type="family law")

    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_a_different_practice_still_does_not_match():
    rule = _rule(match_practice_area="family")
    matter = _matter(practice_area="Estate Planning")

    assert not workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )


def test_unrecognised_labels_keep_the_exact_normalized_comparison():
    rule = _rule(match_matter_type="Portfolio review")
    matching = _matter(matter_type=" portfolio review ")
    different = _matter(matter_type="Portfolio")

    assert workflow_automations.rule_matches(
        rule, matching, trigger_event="matter_created"
    )
    assert not workflow_automations.rule_matches(
        rule, different, trigger_event="matter_created"
    )


def test_an_absent_rule_condition_still_matches_anything():
    rule = _rule()
    matter = _matter(matter_type="Anything at all")

    assert workflow_automations.rule_matches(
        rule, matter, trigger_event="matter_created"
    )
