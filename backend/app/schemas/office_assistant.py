"""Strict wire contracts for the Microsoft 365 Office add-in."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class OfficeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=_to_camel,
    )


OfficeSurface = Literal["word", "excel", "outlook"]
OfficeActionType = Literal[
    "replace_selection",
    "set_selected_values",
    "set_selected_formulas",
    "set_subject",
]
Fingerprint = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class HostCapabilities(OfficeModel):
    surface: OfficeSurface
    requirement_sets: dict[str, bool] = Field(max_length=30)
    readable_scopes: list[Literal["selection", "current-item"]] = Field(
        min_length=1, max_length=3
    )
    supported_actions: list[OfficeActionType] = Field(max_length=10)
    write_enabled: bool


class WordSelection(OfficeModel):
    kind: Literal["text"]
    text: str = Field(max_length=50_000)
    char_count: int = Field(ge=0, le=50_000)
    selection_hash: Fingerprint


class ExcelSelection(OfficeModel):
    kind: Literal["range"]
    address: str = Field(min_length=1, max_length=300)
    row_count: int = Field(gt=0, le=2_500)
    column_count: int = Field(gt=0, le=2_500)
    values: list[list[Any]] = Field(min_length=1, max_length=2_500)
    formulas: list[list[Any]] = Field(min_length=1, max_length=2_500)
    number_formats: list[list[Any]] = Field(min_length=1, max_length=2_500)
    selection_hash: Fingerprint


class OutlookSelection(OfficeModel):
    kind: Literal["mail"]
    mode: Literal["read", "compose"]
    subject: str = Field(max_length=998)
    body_text: str = Field(max_length=50_000)
    body_format: Literal["text", "html"]
    selection_hash: Fingerprint


class WordContext(OfficeModel):
    surface: Literal["word"]
    scope: Literal["selection"]
    captured_at: datetime
    document_fingerprint: Fingerprint
    host_capabilities: HostCapabilities
    selection: WordSelection


class ExcelContext(OfficeModel):
    surface: Literal["excel"]
    scope: Literal["selection"]
    captured_at: datetime
    document_fingerprint: Fingerprint
    host_capabilities: HostCapabilities
    selection: ExcelSelection


class OutlookContext(OfficeModel):
    surface: Literal["outlook"]
    scope: Literal["current-item"]
    captured_at: datetime
    document_fingerprint: Fingerprint
    host_capabilities: HostCapabilities
    selection: OutlookSelection


OfficeContext = Annotated[
    Union[WordContext, ExcelContext, OutlookContext], Field(discriminator="surface")
]


class OfficePlanRequest(OfficeModel):
    context: OfficeContext
    instruction: str = Field(min_length=1, max_length=4_000)


class TextContent(OfficeModel):
    text: str = Field(min_length=1, max_length=100_000)
    format: Literal["text"]


class ValuesContent(OfficeModel):
    values: list[list[Any]] = Field(min_length=1, max_length=2_500)


class FormulasContent(OfficeModel):
    formulas: list[list[str]] = Field(min_length=1, max_length=2_500)


class SubjectContent(OfficeModel):
    subject: str = Field(min_length=1, max_length=998)


class GeneratedReplaceSelection(OfficeModel):
    type: Literal["replace_selection"]
    content: TextContent


class GeneratedSetSelectedValues(OfficeModel):
    type: Literal["set_selected_values"]
    content: ValuesContent


class GeneratedSetSelectedFormulas(OfficeModel):
    type: Literal["set_selected_formulas"]
    content: FormulasContent


class GeneratedSetSubject(OfficeModel):
    type: Literal["set_subject"]
    content: SubjectContent


GeneratedAction = Annotated[
    Union[
        GeneratedReplaceSelection,
        GeneratedSetSelectedValues,
        GeneratedSetSelectedFormulas,
        GeneratedSetSubject,
    ],
    Field(discriminator="type"),
]


class GeneratedPlan(OfficeModel):
    summary: str = Field(min_length=1, max_length=1_000)
    warnings: list[str] = Field(max_length=10)
    actions: list[GeneratedAction] = Field(min_length=1, max_length=1)


class ActionAnchor(OfficeModel):
    selection_hash: Fingerprint
    address: str | None = Field(default=None, max_length=300)


class ReplaceSelectionAction(GeneratedReplaceSelection):
    anchor: ActionAnchor


class SetSelectedValuesAction(GeneratedSetSelectedValues):
    anchor: ActionAnchor


class SetSelectedFormulasAction(GeneratedSetSelectedFormulas):
    anchor: ActionAnchor


class SetSubjectAction(GeneratedSetSubject):
    anchor: ActionAnchor


OfficeAction = Annotated[
    Union[
        ReplaceSelectionAction,
        SetSelectedValuesAction,
        SetSelectedFormulasAction,
        SetSubjectAction,
    ],
    Field(discriminator="type"),
]


class OfficeActionPlan(OfficeModel):
    plan_id: str = Field(min_length=1, max_length=100)
    surface: OfficeSurface
    expires_at: datetime
    base_fingerprint: Fingerprint
    summary: str = Field(min_length=1, max_length=1_000)
    warnings: list[str] = Field(max_length=10)
    actions: list[OfficeAction] = Field(min_length=1, max_length=1)


class OfficeActionResult(OfficeModel):
    plan_id: str = Field(min_length=1, max_length=100)
    status: Literal["applied", "rejected", "stale", "failed"]
    action_count: int = Field(ge=0, le=1)
    result_fingerprint: Fingerprint | None = None
    error_code: str | None = Field(
        default=None, max_length=80, pattern=r"^[a-z0-9_\-]+$"
    )


class OfficeResultAcknowledgement(OfficeModel):
    plan_id: str
    status: Literal["applied", "rejected", "stale", "failed"]


class OfficePolicyResponse(OfficeModel):
    enabled: bool
    plan_ttl_seconds: int
    max_word_characters: int
    max_excel_cells: int
    max_outlook_characters: int
    allowed_actions: dict[OfficeSurface, list[OfficeActionType]]
    raw_content_audit_enabled: Literal[False] = False
