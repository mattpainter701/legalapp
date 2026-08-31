"""Versioned API contract for authorized, multi-source Firm Memory search."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator, field_validator


CorrelationId = str
SourceScope = Literal["all", "on_prem", "cloud", "selected"]
CoverageState = Literal[
    "ready", "partial", "indexing", "stale", "offline", "unsupported", "unauthorized"
]


class FirmMemoryFilters(BaseModel):
    model_config = {"extra": "forbid"}

    file_extensions: list[str] | None = Field(None, max_length=50)
    mime_types: list[str] | None = Field(None, max_length=50)
    modified_from: datetime | None = None
    modified_to: datetime | None = None

    @field_validator("file_extensions")
    @classmethod
    def normalize_extensions(cls, value):
        if value is None:
            return None
        result: list[str] = []
        for item in value:
            item = item.strip().lower()
            if item and not item.startswith("."):
                item = "." + item
            if item and len(item) <= 20 and item not in result:
                result.append(item)
        return result or None

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            self.modified_from
            and self.modified_to
            and self.modified_from > self.modified_to
        ):
            raise ValueError("modified_from must not be after modified_to")
        return self


class FirmMemoryDocumentSearchRequest(BaseModel):
    """Normalized search input; a matter is an optional filter, never a gate."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    query: str = Field(..., min_length=1, max_length=1000)
    source_scope: SourceScope = "all"
    matter_ids: list[str] = Field(default_factory=list, max_length=50)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    collection_ids: list[str] = Field(default_factory=list, max_length=50)
    filters: FirmMemoryFilters = Field(default_factory=FirmMemoryFilters)
    limit: int = Field(20, ge=1, le=100)
    audit_correlation_id: CorrelationId | None = Field(
        None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )

    @field_validator("query")
    @classmethod
    def trim_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("matter_ids", "source_ids", "collection_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            item = item.strip()
            if item and item not in result:
                result.append(item)
        return result

    @model_validator(mode="after")
    def selected_scope_has_selector(self):
        if self.source_scope == "selected" and not (
            self.source_ids or self.collection_ids
        ):
            raise ValueError("selected scope requires source_ids or collection_ids")
        return self


class FirmMemoryResultProvenance(BaseModel):
    source_id: str
    source_name: str
    source_kind: str
    collection_ids: list[str] = Field(default_factory=list)
    index_kind: str
    indexed_at: datetime | None = None
    relative_location: str | None = None
    page_number: int | None = Field(None, ge=1)


class FirmMemoryResultAction(BaseModel):
    kind: Literal["provider_open", "lawhand_result", "open_on_device"]
    label: str = Field(..., min_length=1, max_length=80)
    available: bool = False
    href: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=120)

    @model_validator(mode="after")
    def validate_server_action(self):
        if not self.available:
            if self.href is not None:
                raise ValueError("unavailable actions cannot include an href")
            return self
        if not self.href:
            raise ValueError("available actions require an href")
        if self.kind == "provider_open":
            parsed = urlparse(self.href)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("provider actions require an HTTPS URL")
        elif not self.href.startswith("/") or self.href.startswith("//"):
            raise ValueError("LawHand and device actions require a same-origin path")
        return self


class FirmMemoryDocumentSearchHit(BaseModel):
    document_id: str = Field(..., min_length=20, max_length=128)
    title: str = Field(..., max_length=500)
    snippet: str | None = Field(None, max_length=10000)
    score: float | None = None
    mime_type: str | None = Field(None, max_length=200)
    file_extension: str | None = Field(None, max_length=20)
    size_bytes: int | None = Field(None, ge=0)
    modified_at: datetime | None = None
    matter_ids: list[str] = Field(default_factory=list)
    workspace_ids: list[str] = Field(default_factory=list)
    provenance: FirmMemoryResultProvenance
    actions: list[FirmMemoryResultAction] = Field(default_factory=list, max_length=6)


class FirmMemorySourceCoverage(BaseModel):
    source_id: str
    source_name: str | None = None
    source_kind: str | None = None
    state: CoverageState
    authorization: Literal["allowed", "denied", "unknown"]
    searched: bool = False
    partial: bool = False
    result_count: int = Field(0, ge=0)
    reason: str | None = Field(None, max_length=120)


class FirmMemoryDocumentSearchResponse(BaseModel):
    schema_version: Literal[1] = 1
    audit_correlation_id: CorrelationId
    results: list[FirmMemoryDocumentSearchHit] = Field(default_factory=list)
    coverage: list[FirmMemorySourceCoverage] = Field(default_factory=list)
    partial: bool = False
    complete: bool = False
    generalized_search_enabled: bool = False


class FirmMemorySourceInfo(BaseModel):
    id: str
    display_name: str
    source_kind: str
    provider_key: str | None = None
    share_id: str | None = None
    coverage_state: str
    collection_ids: list[str] = Field(default_factory=list)


class FirmMemoryCapabilitiesResponse(BaseModel):
    contract_versions: list[int] = Field(default_factory=lambda: [1])
    source_scopes: list[SourceScope] = Field(
        default_factory=lambda: ["all", "on_prem", "cloud", "selected"]
    )
    search_entitled: bool
    generalized_search_enabled: bool
    unified_research_available: bool
