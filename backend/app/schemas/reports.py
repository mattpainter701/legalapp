"""Pydantic schemas for firm reporting endpoints."""

from typing import Any

from pydantic import BaseModel


class MatterStatusReport(BaseModel):
    total_matters: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_risk_level: dict[str, int]


class IntakeFunnelReport(BaseModel):
    total_leads: int
    by_status: dict[str, int]
    conversion_rate: float  # matter_opened leads / total_leads


class OverdueTasksReport(BaseModel):
    total_overdue: int
    tasks: list[dict[str, Any]]  # [{id, title, due_date, matter_name}]


class MatterBudgetReport(BaseModel):
    matter_id: str
    matter_name: str
    budget_amount: float | None
    budget_currency: str | None = "USD"
    total_hours: float
    total_billed: float
    billable_time_amount: float | None = None
    billable_expense_amount: float | None = None
    remaining: float | None = None
    utilization_pct: float | None  # None when budget_amount is null/zero


class FirmReportBundle(BaseModel):
    matter_status: MatterStatusReport
    intake_funnel: IntakeFunnelReport
    overdue_tasks: OverdueTasksReport
    generated_at: str  # ISO datetime string


class RealizationReportRow(BaseModel):
    """Per-matter billable-vs-collected realization."""

    matter_id: str
    matter_name: str
    billable_hours: float
    billable_amount: float
    billable_time_amount: float = 0
    billable_expense_amount: float = 0
    collected_amount: float
    realization_pct: float  # collected_amount / billable_amount * 100, rounded to 1dp


class WipReportRow(BaseModel):
    """Per-matter uninvoiced billable time and client expenses."""

    matter_id: str
    matter_name: str
    wip_hours: float
    wip_value: float


class AgingReportRow(BaseModel):
    """Per-matter outstanding A/R balance bucketed by days overdue."""

    matter_id: str
    matter_name: str
    days_0_30: float
    days_31_60: float
    days_61_90: float
    days_90_plus: float
