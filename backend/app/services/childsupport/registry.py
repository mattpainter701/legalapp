"""Jurisdiction registry: maps a state code to its GuidelineProvider(s).

Adding a state is a one-line registration here plus a provider module. The
engine never needs to change. When a state has multiple schedule versions over
time, register each and resolve by ``effective_date`` so historical orders stay
reproducible.
"""

from __future__ import annotations

from datetime import date

from app.services.childsupport.base import GuidelineProvider
from app.services.childsupport.jurisdictions.north_dakota import NorthDakotaProvider
from app.services.childsupport.jurisdictions.texas import TexasProvider

# state_code -> list of providers (newest schedule first). Extend as states land.
_PROVIDERS: dict[str, list[GuidelineProvider]] = {
    "ND": [NorthDakotaProvider()],
    "TX": [TexasProvider()],
}


def get_provider(
    state_code: str, effective_date: date | None = None
) -> GuidelineProvider | None:
    """Return the guideline provider in effect for a state on a given date."""
    versions = _PROVIDERS.get((state_code or "").upper())
    if not versions:
        return None
    if effective_date is None:
        return versions[0]
    eligible = [p for p in versions if p.effective_date <= effective_date]
    if eligible:
        return max(eligible, key=lambda p: p.effective_date)
    # Requested date precedes the earliest known schedule; use the oldest.
    return min(versions, key=lambda p: p.effective_date)


def list_jurisdictions() -> list[dict]:
    """Catalog of supported jurisdictions for the frontend selector."""
    out: list[dict] = []
    for code, versions in sorted(_PROVIDERS.items()):
        p = versions[0]
        out.append(
            {
                "state_code": p.state_code,
                "state_name": p.state_name,
                "model_type": p.model_type.value,
                "schedule_version": p.schedule_version,
                "effective_date": p.effective_date.isoformat(),
                "verified": not p.schedule_unverified,
            }
        )
    return out


def supported_state_codes() -> set[str]:
    return set(_PROVIDERS.keys())
