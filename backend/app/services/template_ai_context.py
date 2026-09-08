"""Bounded editor context for source-backed AI field suggestions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.template_bindings import catalogue
from app.services.template_ai_assist import normalize_ai_field_name


class CurrentTemplateField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=100)
    label: str = Field(default="", max_length=160)
    source_text: str = Field(default="", max_length=2000)
    field_type: str = Field(default="text", max_length=40)
    binding: str = Field(default="", max_length=200)
    included: bool = True
    required: bool = False
    page: int | None = Field(default=None, ge=1, le=1000)
    paragraph_ordinal: int | None = Field(default=None, ge=0, le=100000)


class TemplateAiContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["suggest_fields"] = "suggest_fields"
    title: str = Field(default="", max_length=200)
    category: str = Field(default="other", max_length=100)
    requirements: str = Field(default="", max_length=2000)
    draft_body: str = Field(default="", max_length=100000)
    fields: list[CurrentTemplateField] = Field(default_factory=list, max_length=200)

    def evidence(self, redact, *, source: str) -> dict:
        """Send definitions and editor state, never values from client records."""
        value = self.model_dump()
        value["source_mode"] = source
        value["draft_body_truncated"] = len(self.draft_body) > 12000

        # Redact every user-authored string, including field labels and requirements.
        def clean(item):
            if isinstance(item, str):
                return redact(item)
            if isinstance(item, list):
                return [clean(entry) for entry in item]
            if isinstance(item, dict):
                return {key: clean(entry) for key, entry in item.items()}
            return item

        value = clean(value)
        value["available_bindings"] = [
            {"path": entry.path, "label": entry.label, "group": entry.group}
            for entry in catalogue()
        ]
        value["review_findings"] = [
            {
                "name": clean(field.name),
                "issue": "No explicit binding; review the data source",
            }
            for field in self.fields
            if field.included and not field.binding
        ]
        return value

    def allows_addition(self, proposal) -> bool:
        """Existing edits and exclusions are locked for this additive action."""
        if proposal.existing_name:
            return False
        source = proposal.source_text.strip()
        return not any(
            normalize_ai_field_name(proposal.name) == field.name
            or (
                source
                and field.source_text
                and (source in field.source_text or field.source_text in source)
            )
            for field in self.fields
        )
