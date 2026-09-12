"""Schemas for template sets and the one interview that fills them."""

import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.document_template_set import MAX_SET_MEMBERS


class DocumentTemplateSetItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: uuid.UUID
    #: Omit to follow the template's published version; set it to reproduce one
    #: exact immutable version, which is what a filed packet needs.
    pinned_version_no: Optional[int] = Field(default=None, ge=1)


class DocumentTemplateSetWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)
    module: Optional[str] = Field(default=None, max_length=50)
    jurisdiction: Optional[str] = Field(default=None, max_length=100)
    #: Order is the order documents are produced and reviewed in.
    items: list[DocumentTemplateSetItemInput] = Field(
        default_factory=list, max_length=MAX_SET_MEMBERS
    )

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a name for this set")
        return value.strip()

    @field_validator("items")
    @classmethod
    def unique_templates(
        cls, value: list[DocumentTemplateSetItemInput]
    ) -> list[DocumentTemplateSetItemInput]:
        # The same template twice would ask its manual questions twice and
        # produce two identical documents; if a firm wants two copies they
        # want two templates.
        seen = {item.template_id for item in value}
        if len(seen) != len(value):
            raise ValueError("A set lists each template once")
        return value


class DocumentTemplateSetItemResponse(BaseModel):
    template_id: uuid.UUID
    title: str
    position: int
    pinned_version_no: Optional[int] = None
    #: Which version this member would draft from right now, and why not when
    #: there is none. A member that cannot be drafted is never hidden: the
    #: whole point of a set is knowing the packet is complete.
    resolved_version_no: Optional[int] = None
    unavailable_reason: Optional[str] = None


class DocumentTemplateSetResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str] = None
    module: Optional[str] = None
    jurisdiction: Optional[str] = None
    items: list[DocumentTemplateSetItemResponse]
    created_at: str
    updated_at: str


class DocumentTemplateSetListResponse(BaseModel):
    items: list[DocumentTemplateSetResponse]
    total: int


class InterviewQuestionPlacement(BaseModel):
    """One document this answer fills, and the field it fills there."""

    template_id: uuid.UUID
    template_title: str
    field_name: str
    label: str


class InterviewQuestionResponse(BaseModel):
    key: str
    label: str
    value_kind: str
    required: bool
    #: Card key for a shared question, "" for one belonging to one document.
    card: str
    binding: str
    shared: bool
    appears_in: list[InterviewQuestionPlacement]
    #: Present only when a matter was named. A question with no suggestion is
    #: not an error — it is a question for a person.
    suggested_value: Optional[str] = None
    provenance: Optional[dict[str, Any]] = None
    review_required: bool = False


class DocumentTemplateSetInterviewResponse(BaseModel):
    """Every member's fields, collapsed into the questions asked once."""

    set_id: uuid.UUID
    title: str
    matter_id: Optional[uuid.UUID] = None
    questions: list[InterviewQuestionResponse]
    #: Members that cannot be drafted right now, with the reason. Reported
    #: rather than dropped: a packet silently missing a document is worse than
    #: one that says which document is missing.
    unavailable: list[DocumentTemplateSetItemResponse]
