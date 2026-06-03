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
    budget_currency: str
    total_hours: float
    total_billed: float
    utilization_pct: float | None  # None when budget_amount is null/zero


class FirmReportBundle(BaseModel):
    matter_status: MatterStatusReport
    intake_funnel: IntakeFunnelReport
    overdue_tasks: OverdueTasksReport
    generated_at: str  # ISO datetime string
