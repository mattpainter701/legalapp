"""Engine orchestrator: validate input, dispatch to the jurisdiction provider."""

from __future__ import annotations

from app.services.childsupport.models import ChildSupportInput, Worksheet
from app.services.childsupport.registry import get_provider


class UnsupportedJurisdictionError(ValueError):
    """Raised when no guideline provider is registered for a state."""


def calculate(inp: ChildSupportInput) -> Worksheet:
    """Run the guideline for ``inp.jurisdiction`` and return a worksheet."""
    provider = get_provider(inp.jurisdiction, inp.effective_date)
    if provider is None:
        raise UnsupportedJurisdictionError(
            f"No child support guideline is registered for '{inp.jurisdiction}'."
        )
    return provider.compute(inp)


def calculate_for_jurisdiction(
    state_code: str, inp: ChildSupportInput
) -> Worksheet:
    """Convenience wrapper that overrides the input's jurisdiction."""
    inp.jurisdiction = state_code
    return calculate(inp)
