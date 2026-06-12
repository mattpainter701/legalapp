"""Child support calculation engine.

A jurisdiction-pluggable engine for computing presumptive child support
obligations. Every state's guideline is implemented as a ``GuidelineProvider``
(see ``base.py``) registered in ``registry.py``. The engine normalizes a single
``ChildSupportInput`` and produces an auditable, line-by-line ``Worksheet`` that
mirrors the official state worksheet so an attorney can verify every figure and
reproduce the result against the schedule version in effect on a given date.

North Dakota (``jurisdictions/north_dakota.py``) is the v1 reference
implementation: an *obligor-net-income* model under N.D. Admin. Code ch.
75-02-04.1. The architecture deliberately supports the other major models
(income shares, percentage of income, Melson) without engine changes.

IMPORTANT: This engine is a drafting aid, not legal advice. Guideline schedule
amounts must be verified against the official source for the effective date, and
final orders are subject to judicial discretion and statutory deviations.
"""

from app.services.childsupport.engine import calculate, calculate_for_jurisdiction
from app.services.childsupport.models import (
    ChildSupportInput,
    CustodyType,
    ModelType,
    ParentFinancials,
    Worksheet,
    WorksheetLine,
)
from app.services.childsupport.registry import (
    get_provider,
    list_jurisdictions,
)

__all__ = [
    "calculate",
    "calculate_for_jurisdiction",
    "ChildSupportInput",
    "ParentFinancials",
    "CustodyType",
    "ModelType",
    "Worksheet",
    "WorksheetLine",
    "get_provider",
    "list_jurisdictions",
]
