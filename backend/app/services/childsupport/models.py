"""Calculation-only data structures for the child support engine.

These are plain dataclasses (NOT SQLAlchemy ORM models or Pydantic schemas) used
internally by the engine. The router maps Pydantic request/response schemas to
and from these. Keeping the engine free of FastAPI/DB concerns makes it unit
testable in isolation and reusable from a CLI, a scheduled job, or the chat
agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


CENTS = Decimal("0.01")


def money(value: Decimal | int | float | str | None) -> Decimal:
    """Coerce a value to a 2-decimal Decimal. ``None`` becomes 0.00."""
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


class ModelType(str, Enum):
    """The structural family a state's guideline belongs to."""

    OBLIGOR_NET_INCOME = "obligor_net_income"  # e.g. North Dakota
    INCOME_SHARES = "income_shares"  # ~40 states
    PERCENTAGE_OF_INCOME = "percentage_of_income"  # e.g. Texas
    MELSON = "melson"  # DE, HI, MT


class CustodyType(str, Enum):
    PRIMARY = "primary"  # one parent has primary residential responsibility
    EQUAL = "equal"  # equal/shared residential responsibility offset
    SPLIT = "split"  # each parent has primary care of >=1 child


class ParentRole(str, Enum):
    PETITIONER = "petitioner"
    RESPONDENT = "respondent"
    PARENT_A = "parent_a"
    PARENT_B = "parent_b"


@dataclass
class ParentFinancials:
    """One parent's income picture.

    Explicitly-provided deduction amounts are authoritative. Any deduction left
    as ``None`` may be *estimated* by the engine (clearly labeled as such on the
    worksheet); attorneys should supply real figures for a filing.
    """

    role: str = ParentRole.PARENT_A.value
    name: str | None = None
    gross_monthly_income: Decimal = field(default_factory=lambda: Decimal("0"))

    # Net-income deductions (None => engine may estimate; provided => authoritative)
    federal_income_tax: Decimal | None = None
    state_income_tax: Decimal | None = None
    fica_tax: Decimal | None = None
    required_retirement: Decimal = field(default_factory=lambda: Decimal("0"))
    union_dues: Decimal = field(default_factory=lambda: Decimal("0"))

    # Other obligations / add-ons
    health_insurance_children: Decimal = field(default_factory=lambda: Decimal("0"))
    existing_support_paid: Decimal = field(default_factory=lambda: Decimal("0"))
    other_children_in_home: int = 0

    # Imputation
    is_imputed: bool = False
    imputed_basis: str | None = None  # "earning_capacity" | "minimum_wage" | etc.

    # Custody facts
    annual_overnights: int = 0  # overnights this parent has the child(ren)

    def __post_init__(self) -> None:
        self.gross_monthly_income = money(self.gross_monthly_income)
        for f in (
            "federal_income_tax",
            "state_income_tax",
            "fica_tax",
        ):
            v = getattr(self, f)
            if v is not None:
                setattr(self, f, money(v))
        for f in (
            "required_retirement",
            "union_dues",
            "health_insurance_children",
            "existing_support_paid",
        ):
            setattr(self, f, money(getattr(self, f)))


@dataclass
class ChildSupportInput:
    """Normalized, jurisdiction-agnostic input to the engine."""

    jurisdiction: str  # state code, e.g. "ND"
    num_children: int
    parents: list[ParentFinancials]
    effective_date: date = field(default_factory=date.today)

    custody_type: str = CustodyType.PRIMARY.value
    # For PRIMARY: the obligor is the non-custodial parent. If the caller knows
    # the obligor explicitly, set obligor_role; otherwise the engine infers it.
    obligor_role: str | None = None
    # For SPLIT custody: how many of the children live primarily with parent A
    # (index 0). The remainder live with parent B.
    children_with_parent_a: int = 0

    # Guideline deviation (non-standard cases). When deviation_amount is set the
    # final obligation departs from the presumptive amount; a reason is required.
    deviation_amount: Decimal | None = None
    deviation_reason: str | None = None

    # Estimation toggle: when False the engine refuses to estimate missing
    # deductions and instead emits a warning (treats them as 0).
    allow_estimates: bool = True

    def __post_init__(self) -> None:
        if self.deviation_amount is not None:
            self.deviation_amount = money(self.deviation_amount)


@dataclass
class WorksheetLine:
    """One auditable line of the worksheet."""

    code: str  # short stable identifier, e.g. "ND.NET", "ND.10"
    label: str
    amount: Decimal | None = None
    detail: str | None = None
    estimated: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "amount": (str(self.amount) if self.amount is not None else None),
            "detail": self.detail,
            "estimated": self.estimated,
        }


@dataclass
class Worksheet:
    """The full, persistable result of a calculation run."""

    jurisdiction: str
    state_name: str
    model_type: ModelType
    schedule_version: str
    effective_date: date
    num_children: int
    obligor_role: str | None
    presumptive_amount: Decimal
    final_amount: Decimal
    lines: list[WorksheetLine] = field(default_factory=list)
    deviation_amount: Decimal | None = None
    deviation_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def add(
        self,
        code: str,
        label: str,
        amount: Decimal | None = None,
        detail: str | None = None,
        estimated: bool = False,
    ) -> None:
        self.lines.append(
            WorksheetLine(
                code=code,
                label=label,
                amount=money(amount) if amount is not None else None,
                detail=detail,
                estimated=estimated,
            )
        )

    def to_dict(self) -> dict:
        return {
            "jurisdiction": self.jurisdiction,
            "state_name": self.state_name,
            "model_type": self.model_type.value,
            "schedule_version": self.schedule_version,
            "effective_date": self.effective_date.isoformat(),
            "num_children": self.num_children,
            "obligor_role": self.obligor_role,
            "presumptive_amount": str(self.presumptive_amount),
            "final_amount": str(self.final_amount),
            "deviation_amount": (
                str(self.deviation_amount) if self.deviation_amount is not None else None
            ),
            "deviation_reason": self.deviation_reason,
            "lines": [ln.to_dict() for ln in self.lines],
            "warnings": list(self.warnings),
            "citations": list(self.citations),
        }
