"""Pydantic schemas for QuickBooks Online integration."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class QBOIntegrationStatus(BaseModel):
    connected: bool
    qbo_realm_id: Optional[str] = None
    sandbox_mode: bool
    scopes: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    is_active: bool
    sync_frequency_minutes: int
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None
    qbo_ar_account_id: Optional[str] = None
    qbo_ar_account_name: Optional[str] = None


class QBOIntegrationResponse(BaseModel):
    id: str
    tenant_id: str
    qbo_realm_id: Optional[str] = None
    is_active: bool
    sandbox_mode: bool
    scopes: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    sync_frequency_minutes: int
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None
    qbo_ar_account_id: Optional[str] = None
    qbo_ar_account_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QBOIntegrationUpdate(BaseModel):
    sync_frequency_minutes: Optional[int] = None
    qbo_ar_account_id: Optional[str] = None
    qbo_ar_account_name: Optional[str] = None


class QBOSyncRequest(BaseModel):
    """Trigger a manual sync for specified entity types."""

    sync_customers: bool = False
    sync_time_activities: bool = False
    sync_invoices: bool = False
    sync_payments: bool = False


class QBOSyncStatus(BaseModel):
    status: str  # running, success, partial, failed
    customers_synced: int = 0
    time_activities_synced: int = 0
    invoices_synced: int = 0
    payments_synced: int = 0
    errors: list[str] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class QBOItemOption(BaseModel):
    """A single QBO Item (service type) available for mapping."""

    id: str
    name: str


class QBOAccountOption(BaseModel):
    """A QBO accounts-receivable account available for invoice posting."""

    id: str
    name: str


class QBOItemMappingResponse(BaseModel):
    id: str
    source_type: str
    expense_category: Optional[str] = None
    qbo_item_id: str
    qbo_item_name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QBOItemMappingUpsert(BaseModel):
    """Upsert a single billing-type → QBO Item mapping."""

    source_type: str
    expense_category: Optional[str] = None
    qbo_item_id: str
    qbo_item_name: str
