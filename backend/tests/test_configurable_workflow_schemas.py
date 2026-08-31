"""Focused validation tests for the bounded COMP-09 API contracts."""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.configurable_workflow import (
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionUpdate,
    CustomFieldValuesUpdate,
    WorkflowApplyRequest,
    WorkflowDefinitionInput,
    WorkflowRollbackRequest,
    WorkflowTemplateCreate,
    normalized_field_value,
)


def test_field_keys_are_canonical_and_field_options_are_bounded():
    field = CustomFieldDefinitionCreate(
        entity_type="matter",
        field_key="  Opponent_Name ",
        label="  Opponent   name ",
        field_type="single_select",
        options=["Individual", "Organization"],
    )
    assert field.field_key == "opponent_name"
    assert field.label == "Opponent name"

    with pytest.raises(ValidationError):
        CustomFieldDefinitionCreate(
            entity_type="matter", field_key="Not-valid", label="x", field_type="text"
        )
    with pytest.raises(ValidationError, match="select fields"):
        CustomFieldDefinitionCreate(
            entity_type="matter",
            field_key="status",
            label="Status",
            field_type="single_select",
        )
    with pytest.raises(ValidationError, match="only valid"):
        CustomFieldDefinitionCreate(
            entity_type="matter",
            field_key="notes",
            label="Notes",
            field_type="text",
            options=["unexpected"],
        )
    with pytest.raises(ValidationError, match="unique"):
        CustomFieldDefinitionCreate(
            entity_type="matter",
            field_key="status",
            label="Status",
            field_type="single_select",
            options=["Open", " open "],
        )


@pytest.mark.parametrize(
    ("field_type", "value", "expected"),
    [
        ("text", "  hello  ", "hello"),
        ("number", "001.2300", "1.23"),
        ("date", "2026-08-30", "2026-08-30"),
        ("boolean", True, True),
        ("single_select", "Open", "Open"),
        ("multi_select", ["Closed", "Open"], ["Open", "Closed"]),
        ("contact", str(uuid.UUID(int=1)), str(uuid.UUID(int=1))),
    ],
)
def test_custom_values_are_canonical(field_type, value, expected):
    options = ["Open", "Closed"] if "select" in field_type else []
    assert normalized_field_value(field_type, options, value) == expected


@pytest.mark.parametrize(
    ("field_type", "value"),
    [
        ("number", True),
        ("number", "NaN"),
        ("date", "08/30/2026"),
        ("boolean", "true"),
        ("single_select", "Unknown"),
        ("multi_select", ["Open", "Open"]),
        ("contact", "not-a-uuid"),
    ],
)
def test_custom_values_reject_wrong_types_or_unknown_options(field_type, value):
    options = ["Open", "Closed"] if "select" in field_type else []
    with pytest.raises(ValueError):
        normalized_field_value(field_type, options, value)


def _definition(**overrides):
    value = {
        "initial_stage_key": "intake",
        "stages": [{"stage_key": "intake", "label": "Intake"}],
        "checklist": [
            {
                "item_key": "review",
                "stage_key": "intake",
                "title": "Review intake",
                "due_offset_days": 30,
                "assignee_role": "matter_owner",
            }
        ],
    }
    value.update(overrides)
    return value


def test_workflow_definition_rejects_duplicate_and_dangling_references():
    with pytest.raises(ValidationError, match="stage keys"):
        WorkflowDefinitionInput(
            **_definition(
                stages=[
                    {"stage_key": "intake", "label": "A"},
                    {"stage_key": "intake", "label": "B"},
                ]
            )
        )
    with pytest.raises(ValidationError, match="checklist item keys"):
        WorkflowDefinitionInput(
            **_definition(
                checklist=[
                    {
                        "item_key": "review",
                        "stage_key": "intake",
                        "title": "A",
                        "due_offset_days": 0,
                    },
                    {
                        "item_key": "review",
                        "stage_key": "intake",
                        "title": "B",
                        "due_offset_days": 1,
                    },
                ]
            )
        )
    with pytest.raises(ValidationError, match="reference a stage"):
        WorkflowDefinitionInput(**_definition(initial_stage_key="missing"))
    with pytest.raises(ValidationError, match="every checklist"):
        WorkflowDefinitionInput(
            **_definition(
                checklist=[
                    {
                        "item_key": "review",
                        "stage_key": "missing",
                        "title": "A",
                        "due_offset_days": 0,
                    }
                ]
            )
        )
    with pytest.raises(ValidationError):
        WorkflowDefinitionInput(
            **_definition(
                checklist=[
                    {
                        "item_key": "review",
                        "stage_key": "intake",
                        "title": "A",
                        "due_offset_days": 3651,
                    }
                ]
            )
        )


def test_workflow_names_labels_and_titles_are_nonblank_and_canonical():
    template = WorkflowTemplateCreate(name="  New   matter  ", **_definition())
    assert template.name == "New matter"
    assert template.stages[0].label == "Intake"

    with pytest.raises(ValidationError, match="template name"):
        WorkflowTemplateCreate(name="   ", **_definition())
    with pytest.raises(ValidationError, match="stage label"):
        WorkflowTemplateCreate(
            name="Opening",
            **_definition(stages=[{"stage_key": "intake", "label": "   "}]),
        )
    with pytest.raises(ValidationError, match="checklist title"):
        WorkflowTemplateCreate(
            name="Opening",
            **_definition(
                checklist=[
                    {
                        "item_key": "review",
                        "stage_key": "intake",
                        "title": "   ",
                        "due_offset_days": 0,
                    }
                ]
            ),
        )


def test_workflow_requests_require_explicit_apply_and_reasoned_rollback():
    preview_sha = "a" * 64
    assert (
        WorkflowApplyRequest(
            preview_sha256=preview_sha, confirm_apply=True
        ).confirm_apply
        is True
    )
    with pytest.raises(ValidationError):
        WorkflowApplyRequest(preview_sha256=preview_sha, confirm_apply=False)
    assert (
        WorkflowRollbackRequest(reason="  no longer needed  ").reason
        == "no longer needed"
    )
    with pytest.raises(ValidationError):
        WorkflowRollbackRequest(reason="   ")


def test_custom_value_update_rejects_duplicate_field_ids():
    field_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="once"):
        CustomFieldValuesUpdate(
            values=[
                {"field_definition_id": field_id, "value": "a"},
                {"field_definition_id": field_id, "value": "b"},
            ]
        )


@pytest.mark.parametrize(
    "field_name", ["label", "options", "required", "sensitive", "active"]
)
def test_field_update_rejects_explicit_null_for_non_nullable_contracts(field_name):
    with pytest.raises(ValidationError, match="may not be null|null"):
        CustomFieldDefinitionUpdate(
            expected_schema_version=1,
            **{field_name: None},
        )
