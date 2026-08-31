"""Validation contracts for tenant fields and bounded matter workflows."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


EntityType = Literal["matter", "contact"]
FieldType = Literal[
    "text",
    "long_text",
    "number",
    "date",
    "boolean",
    "single_select",
    "multi_select",
    "contact",
]
TaskType = Literal[
    "deadline",
    "hearing",
    "filing",
    "deposition",
    "call",
    "follow_up",
    "intake",
    "review",
    "general",
]
Priority = Literal["low", "medium", "high", "urgent"]
AssigneeRole = Literal[
    "matter_owner", "attorney_of_record", "template_applier", "unassigned"
]

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _clean_key(value: str) -> str:
    clean = value.strip().lower()
    if not KEY_PATTERN.fullmatch(clean):
        raise ValueError(
            "keys must start with a letter and use lowercase letters, numbers, or underscores"
        )
    return clean


class CustomFieldDefinitionCreate(BaseModel):
    entity_type: EntityType
    field_key: str
    label: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    field_type: FieldType
    options: list[str] = Field(default_factory=list, max_length=100)
    required: bool = False
    sensitive: bool = False

    _validate_key = field_validator("field_key")(_clean_key)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("label must not be blank")
        return clean

    @field_validator("options")
    @classmethod
    def clean_options(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = " ".join(value.split())
            if not clean or len(clean) > 160:
                raise ValueError("options must be non-blank and at most 160 characters")
            folded = clean.casefold()
            if folded in seen:
                raise ValueError("options must be unique")
            seen.add(folded)
            result.append(clean)
        return result

    @model_validator(mode="after")
    def options_match_type(self):
        select_type = self.field_type in {"single_select", "multi_select"}
        if select_type and not self.options:
            raise ValueError("select fields require at least one option")
        if not select_type and self.options:
            raise ValueError("options are only valid for select fields")
        return self


class CustomFieldDefinitionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    options: list[str] | None = Field(default=None, max_length=100)
    required: bool | None = None
    sensitive: bool | None = None
    active: bool | None = None
    expected_schema_version: int = Field(ge=1)

    @field_validator("label")
    @classmethod
    def clean_optional_label(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("label may not be null")
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("label must not be blank")
        return clean

    @field_validator("options")
    @classmethod
    def reject_null_options(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            raise ValueError("options may not be null")
        return value

    @field_validator("required", "sensitive", "active")
    @classmethod
    def reject_null_flags(cls, value: bool | None) -> bool | None:
        if value is None:
            raise ValueError("field flags may not be null")
        return value


class CustomFieldValueInput(BaseModel):
    field_definition_id: uuid.UUID
    value: object


class CustomFieldValuesUpdate(BaseModel):
    values: list[CustomFieldValueInput] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_fields(self):
        ids = [item.field_definition_id for item in self.values]
        if len(ids) != len(set(ids)):
            raise ValueError("each field may appear only once")
        return self


def normalized_field_value(
    field_type: str, options: list[str], value: object
) -> object:
    """Return one canonical JSON-compatible value or raise ``ValueError``."""

    if value is None:
        return None
    if field_type in {"text", "long_text"}:
        if not isinstance(value, str):
            raise ValueError("value must be text")
        limit = 500 if field_type == "text" else 20_000
        if len(value) > limit:
            raise ValueError(f"value must be at most {limit} characters")
        return value.strip()
    if field_type == "number":
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("value must be a number") from None
        if not number.is_finite() or abs(number) > Decimal("1000000000000000"):
            raise ValueError("number is outside the supported range")
        return format(number.normalize(), "f")
    if field_type == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ValueError("value must be an ISO date") from None
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("value must be true or false")
        return value
    if field_type == "single_select":
        if not isinstance(value, str) or value not in options:
            raise ValueError("value must be one configured option")
        return value
    if field_type == "multi_select":
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError("value must be a list of configured options")
        if len(value) != len(set(value)) or any(item not in options for item in value):
            raise ValueError("value contains duplicate or unknown options")
        return [option for option in options if option in value]
    if field_type == "contact":
        try:
            return str(uuid.UUID(str(value)))
        except ValueError:
            raise ValueError("value must be a contact id") from None
    raise ValueError("unsupported field type")


class WorkflowStageInput(BaseModel):
    stage_key: str
    label: str = Field(min_length=1, max_length=160)

    _validate_key = field_validator("stage_key")(_clean_key)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("stage label must not be blank")
        return clean


class WorkflowChecklistInput(BaseModel):
    item_key: str
    stage_key: str
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5_000)
    task_type: TaskType = "general"
    priority: Priority = "medium"
    due_offset_days: int = Field(ge=0, le=3650)
    assignee_role: AssigneeRole = "unassigned"

    _validate_item_key = field_validator("item_key")(_clean_key)
    _validate_stage_key = field_validator("stage_key")(_clean_key)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("checklist title must not be blank")
        return clean


class WorkflowDefinitionInput(BaseModel):
    initial_stage_key: str
    stages: list[WorkflowStageInput] = Field(min_length=1, max_length=50)
    checklist: list[WorkflowChecklistInput] = Field(min_length=1, max_length=200)
    required_field_definition_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=100
    )

    _validate_initial_stage_key = field_validator("initial_stage_key")(_clean_key)

    @model_validator(mode="after")
    def references_are_consistent(self):
        stage_keys = [stage.stage_key for stage in self.stages]
        item_keys = [item.item_key for item in self.checklist]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("stage keys must be unique")
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("checklist item keys must be unique")
        if self.initial_stage_key not in stage_keys:
            raise ValueError("initial_stage_key must reference a stage")
        if any(item.stage_key not in stage_keys for item in self.checklist):
            raise ValueError("every checklist item must reference a stage")
        if len(self.required_field_definition_ids) != len(
            set(self.required_field_definition_ids)
        ):
            raise ValueError("required field ids must be unique")
        return self


class WorkflowTemplateCreate(WorkflowDefinitionInput):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("template name must not be blank")
        return clean


class WorkflowTemplateVersionCreate(WorkflowDefinitionInput):
    expected_latest_version: int = Field(ge=1)


class WorkflowApplyRequest(BaseModel):
    preview_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_apply: Literal[True]


class WorkflowRollbackRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("rollback reason is required")
        return clean


class WorkflowPreviewResponse(BaseModel):
    run_id: uuid.UUID
    matter_id: uuid.UUID
    template_id: uuid.UUID
    template_version_id: uuid.UUID
    template_name: str
    template_version: int
    preview_sha256: str
    planned_at: datetime
    can_apply: bool
    initial_stage: dict
    tasks: list[dict]
    missing_required_fields: list[dict]
    missing_assignees: list[dict]
