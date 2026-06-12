"""The GuidelineProvider contract every jurisdiction implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.services.childsupport.models import (
    ChildSupportInput,
    ModelType,
    Worksheet,
)


class GuidelineProvider(ABC):
    """A single state's child support guideline, for one schedule version.

    Implementations are pure functions of their input: ``compute`` must not read
    global mutable state so a run is fully reproducible from its inputs plus the
    provider's ``schedule_version``.
    """

    state_code: str = ""  # e.g. "ND"
    state_name: str = ""  # e.g. "North Dakota"
    model_type: ModelType = ModelType.INCOME_SHARES
    schedule_version: str = ""  # e.g. "2024-08"
    effective_date: date = date(1970, 1, 1)
    # Set True when the encoded schedule data is provisional / not yet verified
    # against the official source. Surfaces a warning on every worksheet.
    schedule_unverified: bool = False

    @abstractmethod
    def compute(self, inp: ChildSupportInput) -> Worksheet:
        """Run the guideline and return a line-by-line worksheet."""

    def available_deviations(self) -> list[str]:
        """Statutory/established grounds for departing from the guideline."""
        return [
            "Increased needs of a child (medical, dental, educational, special needs)",
            "Increased ability of a parent to provide support",
            "Travel expenses for visitation",
            "Reduced ability of the obligor to pay (extraordinary circumstances)",
            "Assets or income not adequately reflected in net income",
            "Other factors making guideline application inequitable",
        ]

    def validate(self, inp: ChildSupportInput) -> list[str]:
        """Cheap input sanity checks; returns warning strings (non-fatal)."""
        warnings: list[str] = []
        if inp.num_children < 1:
            warnings.append("Number of children is less than 1.")
        if inp.num_children > 6:
            warnings.append(
                "More than 6 children: guideline schedules typically cap columns "
                "at 6; the 6-child figure is applied."
            )
        if not inp.parents:
            warnings.append("No parent financial records were provided.")
        if inp.deviation_amount is not None and not (inp.deviation_reason or "").strip():
            warnings.append(
                "A deviation amount was entered without a written reason; a "
                "documented reason is required to depart from the guideline."
            )
        if self.schedule_unverified:
            warnings.append(
                f"{self.state_name} guideline schedule ({self.schedule_version}) is "
                "PROVISIONAL and must be verified against the official source before "
                "use in a filing."
            )
        return warnings
