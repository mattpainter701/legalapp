"""Pydantic schemas for DocumentTemplate."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

CATEGORIES = ["engagement_letter", "retainer", "NDA", "motion", "other"]


class DocumentTemplateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str
    category: str = "other"
    description: Optional[str] = None
    visibility: Optional[str] = "tenant"
    layer: Optional[str] = None
    status: Optional[str] = "draft"
    format: Optional[str] = "markdown"
    module: Optional[str] = None
    stage: Optional[str] = None
    jurisdiction: Optional[str] = None
    kind: Optional[str] = None
    variable_schema: Optional[dict[str, Any]] = None
    signer_roles: Optional[list[dict[str, Any]]] = None
    branding_profile: Optional[dict[str, Any]] = None


class DocumentTemplateUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    body: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None
    layer: Optional[str] = None
    status: Optional[str] = None
    format: Optional[str] = None
    module: Optional[str] = None
    stage: Optional[str] = None
    jurisdiction: Optional[str] = None
    kind: Optional[str] = None
    variable_schema: Optional[dict[str, Any]] = None
    signer_roles: Optional[list[dict[str, Any]]] = None
    branding_profile: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class DocumentTemplateResponse(BaseModel):
    id: str
    title: str
    body: str
    category: str
    description: Optional[str] = None
    visibility: Optional[str] = "tenant"
    layer: Optional[str] = None
    status: Optional[str] = "draft"
    format: Optional[str] = "markdown"
    module: Optional[str] = None
    stage: Optional[str] = None
    jurisdiction: Optional[str] = None
    kind: Optional[str] = None
    variable_schema: Optional[dict[str, Any]] = None
    signer_roles: Optional[list[dict[str, Any]]] = None
    branding_profile: Optional[dict[str, Any]] = None
    source_filename: Optional[str] = None
    source_content_type: Optional[str] = None
    source_sha256: Optional[str] = None
    source_file_size: Optional[int] = None
    last_test_rendered_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_user_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("id", "approved_by_user_id", mode="before")
    @classmethod
    def stringify_uuid_fields(cls, value: Any) -> Any:
        return str(value) if value is not None else None

    class Config:
        from_attributes = True


class DocumentTemplateListResponse(BaseModel):
    items: list[DocumentTemplateResponse]
    total: int


class DocumentTemplateRenderRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict, max_length=200)
    matter_id: Optional[str] = None
    include_suggestions: bool = False
    # Matter-ready output is non-editable by default. The renderer fails rather
    # than clipping or substituting unsupported glyphs.
    flatten_pdf: bool = True

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, str]) -> dict[str, str]:
        total = 0
        for key, item in value.items():
            if len(key) > 100:
                raise ValueError("Variable names may not exceed 100 characters")
            if len(item) > 10_000:
                raise ValueError(f"Variable {key!r} exceeds 10,000 characters")
            total += len(item)
        if total > 250_000:
            raise ValueError("Combined variable values exceed 250,000 characters")
        return value


class DocumentTemplateVariableSuggestion(BaseModel):
    variable: str
    suggested_value: Optional[str] = None
    source_type: Optional[str] = None
    source_field: Optional[str] = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    review_required: bool = True


class DocumentTemplateRenderResponse(BaseModel):
    rendered: str
    matter_document_id: Optional[str] = None
    variable_suggestions: Optional[list[DocumentTemplateVariableSuggestion]] = None
    output_format: str = "markdown"
    output_filename: Optional[str] = None
    download_url: Optional[str] = None
    storage_backend: Optional[str] = None
    storage_provider: Optional[str] = None
    storage_warning: Optional[str] = None


class DocumentTemplateSmartFillRequest(BaseModel):
    matter_id: Optional[str] = None
    variables: Optional[list[str]] = None


class DocumentTemplateSmartFillResponse(BaseModel):
    template_id: str
    matter_id: Optional[str] = None
    variables: list[DocumentTemplateVariableSuggestion]


class DocumentTemplateUploadAnalysisResponse(BaseModel):
    title: str
    format: str
    body: str
    body_preview: str
    extracted_text: str
    suggested_variable_schema: dict[str, Any] = Field(default_factory=dict)
    detected_branding_profile: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
