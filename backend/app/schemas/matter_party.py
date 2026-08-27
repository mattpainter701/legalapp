"""Pydantic schemas for matter parties."""

import re
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


MATTER_PARTY_ROLE_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "value": "plaintiff",
        "label": "Plaintiff",
        "description": "A party asserting claims in a civil action.",
        "is_caption_role": True,
        "template_fields": ["plaintiff_name", "plaintiff_names"],
    },
    {
        "value": "defendant",
        "label": "Defendant",
        "description": "A party defending against claims in a civil action.",
        "is_caption_role": True,
        "template_fields": ["defendant_name", "defendant_names"],
    },
    {
        "value": "petitioner",
        "label": "Petitioner",
        "description": "A party requesting relief in a petition-based proceeding.",
        "is_caption_role": True,
        "template_fields": [],
    },
    {
        "value": "respondent",
        "label": "Respondent",
        "description": "A party responding in a petition-based proceeding.",
        "is_caption_role": True,
        "template_fields": [],
    },
    {
        "value": "client",
        "label": "Client",
        "description": (
            "The contact represented by the firm; this relationship does not by "
            "itself identify a plaintiff or defendant."
        ),
        "is_caption_role": False,
        "template_fields": [],
    },
    {
        "value": "opposing_party",
        "label": "Opposing Party",
        "description": (
            "A party adverse to the client when a more specific caption role is "
            "not known."
        ),
        "is_caption_role": False,
        "template_fields": [],
    },
    {
        "value": "counsel",
        "label": "Counsel",
        "description": "An attorney or law firm appearing for a party.",
        "is_caption_role": False,
        "template_fields": [],
    },
    {
        "value": "witness",
        "label": "Witness",
        "description": "A fact or lay witness associated with the matter.",
        "is_caption_role": False,
        "template_fields": [],
    },
    {
        "value": "expert",
        "label": "Expert",
        "description": "An expert witness or consultant associated with the matter.",
        "is_caption_role": False,
        "template_fields": [],
    },
    {
        "value": "other",
        "label": "Other",
        "description": "Another matter participant not covered by a canonical role.",
        "is_caption_role": False,
        "template_fields": [],
    },
)

_PARTY_ROLE_ALIASES = {
    "plaintiffs": "plaintiff",
    "defendants": "defendant",
    "petitioners": "petitioner",
    "respondents": "respondent",
    "opposing_parties": "opposing_party",
    "opponent": "opposing_party",
}


def normalize_matter_party_role(value: object) -> str:
    """Store party roles in a stable snake_case form used by Smart Fill."""

    role = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    role = _PARTY_ROLE_ALIASES.get(role, role)
    if not role:
        raise ValueError("Party role is required")
    if len(role) > 50:
        raise ValueError("Party role may not exceed 50 characters")
    return role


class MatterPartyRoleDefinition(BaseModel):
    value: str
    label: str
    description: str
    is_caption_role: bool = False
    template_fields: list[str] = Field(default_factory=list)


def matter_party_role_definitions() -> list[MatterPartyRoleDefinition]:
    return [
        MatterPartyRoleDefinition.model_validate(definition)
        for definition in MATTER_PARTY_ROLE_DEFINITIONS
    ]


class MatterPartyCreate(BaseModel):
    matter_id: uuid.UUID
    contact_id: uuid.UUID
    role: str = "other"
    is_primary: bool = False
    notes: Optional[str] = None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: object) -> str:
        return normalize_matter_party_role(value)


class MatterPartyUpdate(BaseModel):
    role: Optional[str] = None
    is_primary: Optional[bool] = None
    notes: Optional[str] = None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_matter_party_role(value)


class MatterPartyResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    matter_id: uuid.UUID
    contact_id: Optional[uuid.UUID] = None
    role: str
    is_primary: bool
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    contact_display_name: Optional[str] = None

    class Config:
        from_attributes = True


class MatterPartyListResponse(BaseModel):
    items: list[MatterPartyResponse]
    total: int
    role_definitions: list[MatterPartyRoleDefinition] = Field(
        default_factory=matter_party_role_definitions
    )
