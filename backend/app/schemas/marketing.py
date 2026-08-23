import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator


class DemoRequestCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    firm_name: str = Field(min_length=2, max_length=300)
    phone: str | None = Field(default=None, max_length=60)
    team_size: str | None = Field(default=None, max_length=50)
    message: str | None = Field(default=None, max_length=3000)
    source_path: str = Field(default="/demo", max_length=500)
    campaign: dict[str, str] | None = None
    website: str = Field(default="", max_length=200)

    @field_validator(
        "name", "firm_name", "phone", "team_size", "message", "source_path"
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("name", "firm_name", "phone", "team_size", "source_path")
    @classmethod
    def reject_line_breaks(cls, value: str | None) -> str | None:
        """Keep single-line fields on one line.

        ``firm_name`` reaches the lead notification's subject header. Python's
        email stack refuses to serialize a header containing an embedded one, so
        a newline here does not inject a header -- it raises, the broad handler
        in ``send_email`` turns that into a delivery failure, and the lead is
        stored while nobody is notified. Rejecting it at the edge keeps that
        silent-loss path closed.
        """
        if value and any(character in value for character in "\r\n"):
            raise ValueError("must be a single line")
        return value

    @field_validator("campaign")
    @classmethod
    def limit_campaign(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if not value:
            return None
        allowed = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "placement",
            "referrer",
        }
        return {
            key: str(raw)[:500] for key, raw in value.items() if key in allowed and raw
        } or None


class DemoRequestAccepted(BaseModel):
    id: uuid.UUID
    status: str = "received"


class MarketingEventCreate(BaseModel):
    name: str
    session_id: uuid.UUID
    page: str = Field(max_length=500)
    properties: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def allow_event_name(cls, value: str) -> str:
        if value not in {
            "demo_cta_clicked",
            "demo_form_started",
            "demo_form_submitted",
        }:
            raise ValueError("Unsupported marketing event")
        return value

    @field_validator("properties")
    @classmethod
    def limit_properties(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if not value:
            return None
        allowed = {"placement", "utm_source", "utm_medium", "utm_campaign"}
        return {
            key: str(raw)[:200] for key, raw in value.items() if key in allowed and raw
        } or None
