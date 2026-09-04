"""Validation contracts for bounded matter workflow automation rules."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


TriggerEvent = Literal["matter_created", "matter_stage_changed"]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None


class WorkflowAutomationRuleInput(BaseModel):
    """The complete bounded definition of one automation rule."""

    name: str = Field(min_length=1, max_length=120)
    trigger_event: TriggerEvent
    trigger_stage: str | None = Field(default=None, max_length=200)
    match_matter_type: str | None = Field(default=None, max_length=100)
    match_practice_area: str | None = Field(default=None, max_length=200)
    template_id: uuid.UUID

    @field_validator("name")
    @classmethod
    def _name_present(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("name is required")
        return clean

    @field_validator("trigger_stage", "match_matter_type", "match_practice_area")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def _stage_matches_trigger(self) -> "WorkflowAutomationRuleInput":
        if self.trigger_event == "matter_stage_changed" and not self.trigger_stage:
            raise ValueError("trigger_stage is required for a stage-change trigger")
        if self.trigger_event == "matter_created" and self.trigger_stage:
            raise ValueError("trigger_stage applies only to a stage-change trigger")
        return self


class WorkflowAutomationActivateRequest(BaseModel):
    """Approval carries the exact definition the approver reviewed."""

    definition_sha256: str = Field(min_length=64, max_length=64)
    confirm_activate: bool

    @field_validator("definition_sha256")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        clean = value.strip().lower()
        if not all(character in "0123456789abcdef" for character in clean):
            raise ValueError("definition_sha256 must be a sha-256 hex digest")
        return clean

    @field_validator("confirm_activate")
    @classmethod
    def _explicit(cls, value: bool) -> bool:
        if not value:
            raise ValueError("confirm_activate must be true")
        return value
