"""Schemas for external import staging APIs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExternalSystemConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    external_key: str
    display_name: str
    status: str
    accounting_mode: str
    source_metadata: dict | None = None
    last_import_run_id: uuid.UUID | None = None
    last_import_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ExternalImportRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    source_system: str | None = None
    export_id: str | None = None
    status: str
    row_counts: dict | None = None
    checksum_summary: dict | None = None
    warnings: list | None = None
    errors: list | None = None
    approved_at: datetime | None = None
    promoted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ExternalImportTableSummary(BaseModel):
    source_table: str
    row_count: int
    checksum: str | None = None
    manifest_row_count: int | None = None
    status: str = "staged"


class ExternalRawRowPreview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_table: str
    source_row_key: str
    row_checksum: str
    row_data: dict
    created_at: datetime


class ExternalImportTablesResponse(BaseModel):
    import_run_id: uuid.UUID
    tables: list[ExternalImportTableSummary]


class ExternalImportRowsResponse(BaseModel):
    import_run_id: uuid.UUID
    source_table: str
    rows: list[ExternalRawRowPreview]
    total: int


class ExternalImportReconcileResponse(BaseModel):
    import_run_id: uuid.UUID
    status: str
    provider: str
    export_id: str | None = None
    table_count: int
    total_rows: int
    tables: list[ExternalImportTableSummary]
    warnings: list
    errors: list


class ExternalImportApproveRequest(BaseModel):
    confirmation: str
    report_hash: str


class ExternalImportPromoteResponse(BaseModel):
    import_run_id: uuid.UUID
    status: str
    created: dict[str, int]
    linked: int
    skipped: int
    errors: list[str]
    report_hash: str
