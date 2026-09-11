"""Strict argument contracts for Workspace MCP-only lifecycle tools."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.chat_action import ChatActionModel

#: A pushed DOCX is bounded well below the transport cap so an oversized file
#: is refused with a named argument error rather than a bare HTTP 413.
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BASE64_CHARS = ((MAX_DOCUMENT_BYTES + 2) // 3) * 4
MAX_TEMPLATE_BODY_CHARS = 200_000


class SearchClientsArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["prospect", "active", "inactive", "former"] | None = None
    active_only: bool = True
    limit: int = Field(default=25, ge=1, le=50)


class GetClientArgs(ChatActionModel):
    client_id: UUID
    matter_limit: int = Field(default=25, ge=1, le=50)
    related_contact_limit: int = Field(default=25, ge=1, le=50)


class SearchIntakesArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, min_length=1, max_length=50)
    practice_area: str | None = Field(default=None, min_length=1, max_length=100)
    assigned_to_user_id: UUID | None = None
    limit: int = Field(default=25, ge=1, le=50)


class GetIntakeArgs(ChatActionModel):
    intake_id: UUID


class SearchMattersArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, min_length=1, max_length=100)
    practice_area: str | None = Field(default=None, min_length=1, max_length=200)
    include_closed: bool = False
    limit: int = Field(default=25, ge=1, le=50)


class SearchFirmMemoryArgs(ChatActionModel):
    """Search matter-bound files through the customer's local agent."""

    matter_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    file_extensions: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=20, ge=1, le=50)
    correlation_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Query must not be blank")
        return value

    @field_validator("file_extensions")
    @classmethod
    def normalize_extensions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for raw_extension in value:
            extension = str(raw_extension).strip().lower()
            if not extension:
                continue
            if not extension.startswith("."):
                extension = f".{extension}"
            if len(extension) > 20:
                raise ValueError("File extensions must be at most 20 characters")
            if extension not in normalized:
                normalized.append(extension)
        return normalized or None


class SearchTasksArgs(ChatActionModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    matter_id: UUID | None = None
    contact_id: UUID | None = None
    assigned_to_user_id: UUID | None = None
    status: str | None = Field(default=None, min_length=1, max_length=50)
    priority: str | None = Field(default=None, min_length=1, max_length=20)
    task_type: str | None = Field(default=None, min_length=1, max_length=50)
    due_before: date | None = None
    due_after: date | None = None
    limit: int = Field(default=25, ge=1, le=50)


class GetTaskArgs(ChatActionModel):
    task_id: UUID
    event_limit: int = Field(default=25, ge=1, le=100)


class GetDocumentTemplateTextArgs(ChatActionModel):
    matter_id: UUID
    template_id: UUID
    max_characters: int = Field(default=20_000, ge=100, le=50_000)


class ProposeDocumentFromTemplateArgs(ChatActionModel):
    """Render an approved template into cloud work awaiting staged review."""

    matter_id: UUID
    template_id: UUID
    variables: dict[str, str] = Field(default_factory=dict, max_length=200)
    client_request_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_date: date | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    staff_reviewer_user_id: UUID | None = None
    attorney_reviewer_user_id: UUID | None = None

    @field_validator("variables")
    @classmethod
    def bound_variables(cls, value: dict[str, str]) -> dict[str, str]:
        total = 0
        normalized: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name or len(name) > 120:
                raise ValueError("Template variable names must be 1-120 characters")
            variable_value = str(raw_value)
            if len(variable_value) > 10_000:
                raise ValueError(
                    f"Template variable {name!r} exceeds 10,000 characters"
                )
            total += len(variable_value)
            normalized[name] = variable_value
        if total > 20_000:
            raise ValueError("Combined template variables exceed 20,000 characters")
        return normalized


class ProposeMatterDocumentFileArgs(ChatActionModel):
    """Adopt an externally authored DOCX as reviewable cloud matter work.

    ``content_base64`` carries the exact file an outside agent produced.  The
    review preview is always extracted from those bytes server-side, so a
    caller cannot describe the document as something other than what the firm
    will actually approve.
    """

    matter_id: UUID
    content_base64: str = Field(min_length=1, max_length=MAX_DOCUMENT_BASE64_CHARS)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    client_request_id: UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    document_kind: str = Field(default="other", min_length=1, max_length=80)
    due_date: date | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    staff_reviewer_user_id: UUID | None = None
    attorney_reviewer_user_id: UUID | None = None

    @field_validator("content_base64")
    @classmethod
    def strip_base64(cls, value: str) -> str:
        # Wire formats wrap long base64. Whitespace is not part of the payload.
        compact = "".join(str(value).split())
        if not compact:
            raise ValueError("content_base64 must not be blank")
        return compact

    @field_validator("content_sha256")
    @classmethod
    def normalize_digest(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = str(value).strip()
        if not name or any(character in name for character in ("/", "\\", "\x00")):
            raise ValueError("filename must not contain a path")
        if not name.casefold().endswith(".docx"):
            raise ValueError("Only .docx files can be adopted")
        return name


class ProposeDocumentTemplateArgs(ChatActionModel):
    """Push an authored firm template into LawHand as a draft.

    Three source formats are supported. A ``pdf`` carrying real AcroForm
    fields is the preferred one: its field map is discovered from the form
    itself, so the template arrives complete and needs no hand placement. A
    ``docx`` is analysed the same way, anchoring each variable to the exact
    source text it replaces. ``markdown`` takes a ``{{variable}}`` body inline.

    A pushed template is never active.  ``template_id`` revises a template that
    is still a draft; ``supersedes_template_id`` proposes a replacement draft
    for a live template without touching the one the firm is using today.
    """

    title: str = Field(min_length=1, max_length=300)
    format: Literal["markdown", "docx", "pdf"] = "markdown"
    #: Required for markdown. For docx/pdf the body is derived from the file, and
    #: an explicit body may only refine the reviewer-facing text.
    body: str | None = Field(default=None, max_length=MAX_TEMPLATE_BODY_CHARS)
    #: The template file itself, for the docx and pdf formats.
    content_base64: str | None = Field(
        default=None, max_length=MAX_DOCUMENT_BASE64_CHARS
    )
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    category: str = Field(default="other", min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=2_000)
    module: str | None = Field(default=None, min_length=1, max_length=100)
    stage: str | None = Field(default=None, min_length=1, max_length=200)
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=300)
    kind: str | None = Field(default=None, min_length=1, max_length=100)
    variable_schema: dict[str, Any] | None = None
    template_id: UUID | None = None
    supersedes_template_id: UUID | None = None
    client_request_id: UUID | None = None
    change_summary: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        title = " ".join(str(value).split())
        if not title:
            raise ValueError("title must not be blank")
        return title

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        body = str(value).strip()
        if not body:
            raise ValueError("body must not be blank")
        return body

    @field_validator("content_base64")
    @classmethod
    def strip_base64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        compact = "".join(str(value).split())
        if not compact:
            raise ValueError("content_base64 must not be blank")
        return compact

    @field_validator("content_sha256")
    @classmethod
    def normalize_digest(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = str(value).strip()
        if not name or any(character in name for character in ("/", "\\", "\x00")):
            raise ValueError("filename must not contain a path")
        return name

    @model_validator(mode="after")
    def one_revision_target(self) -> "ProposeDocumentTemplateArgs":
        if self.template_id is not None and self.supersedes_template_id is not None:
            raise ValueError(
                "Pass template_id to revise a draft or supersedes_template_id to "
                "replace a live template, not both"
            )
        if self.format == "markdown":
            if self.content_base64 is not None:
                raise ValueError(
                    "content_base64 belongs to the docx and pdf formats; a "
                    "markdown template carries its body inline"
                )
            if not self.body:
                raise ValueError("A markdown template requires a body")
        elif self.content_base64 is None:
            raise ValueError(f"A {self.format} template requires content_base64")
        return self


class ProposeMatterFileArgs(ChatActionModel):
    """Attach an artifact the assistant produced or forwarded to a matter.

    This is the path for evidence and correspondence — a screenshot, a scanned
    exhibit, a saved email, an export — rather than for Word work product a
    reviewer edits, which goes through ``propose_matter_document_file``.
    """

    matter_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=MAX_DOCUMENT_BASE64_CHARS)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    description: str | None = Field(default=None, max_length=400)
    document_category: str | None = Field(default=None, min_length=1, max_length=100)
    client_request_id: UUID | None = None

    @field_validator("content_base64")
    @classmethod
    def strip_base64(cls, value: str) -> str:
        compact = "".join(str(value).split())
        if not compact:
            raise ValueError("content_base64 must not be blank")
        return compact

    @field_validator("content_sha256")
    @classmethod
    def normalize_digest(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        name = str(value).strip()
        if not name or any(character in name for character in ("/", "\\", "\x00")):
            raise ValueError("filename must not contain a path")
        if name.startswith(".") or "." not in name:
            raise ValueError("filename must have a file extension")
        return name
