"""Rule matching normalizes practice labels through the shared alias table.

A rule and a matter often name the same practice differently — a rule keyed
to "family" must fire for a matter typed "Dissolution of Marriage", and a
rule typed "Dissolution of Marriage" must fire for a "Family Law" matter.
Labels the alias table does not recognize keep the historical exact
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


def test_a_rule_keyed_to_an_alias_matches_a_matters_slug():
    rule = _rule(match_practice_area="Dissolution of Marriage")
    matter = _matter(practice_area="Family Law")

    assert workflow_automations.rule_matches(
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
