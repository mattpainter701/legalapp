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


class FirmReportBundle(BaseModel):
    matter_status: MatterStatusReport
    intake_funnel: IntakeFunnelReport
    overdue_tasks: OverdueTasksReport
    generated_at: str  # ISO datetime string
