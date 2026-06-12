"""Shared net-income helpers.

These produce *estimates* used only when an explicit deduction figure is not
supplied. They are intentionally simple and conservative; an attorney preparing
a filing should enter actual figures (pay stubs, tax returns). Every estimated
figure is flagged ``estimated=True`` on the worksheet.

Constants are tax-year specific and live here so they can be updated centrally.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.childsupport.models import money

# ── FICA (2024/2025) ──────────────────────────────────────────────────────────
SOCIAL_SECURITY_RATE = Decimal("0.062")
MEDICARE_RATE = Decimal("0.0145")
# Social Security wage base (annual). 2024: 168,600; 2025: 176,100. Use a current
# value; this is an estimate path only.
SS_WAGE_BASE_ANNUAL = Decimal("176100")


def estimate_fica(gross_monthly: Decimal, self_employed: bool = False) -> Decimal:
    """Estimate monthly FICA (employee share; doubled for self-employment)."""
    gross_monthly = money(gross_monthly)
    annual = gross_monthly * 12
    ss_base = min(annual, SS_WAGE_BASE_ANNUAL)
    ss = ss_base * SOCIAL_SECURITY_RATE
    medicare = annual * MEDICARE_RATE
    monthly = (ss + medicare) / 12
    if self_employed:
        monthly = monthly * 2
    return money(monthly)


# ── Federal income tax (very rough single-filer, standard deduction) ──────────
# 2024 single brackets on taxable income; standard deduction 14,600.
FED_STANDARD_DEDUCTION = Decimal("14600")
_FED_BRACKETS = [
    (Decimal("0"), Decimal("0.10")),
    (Decimal("11600"), Decimal("0.12")),
    (Decimal("47150"), Decimal("0.22")),
    (Decimal("100525"), Decimal("0.24")),
    (Decimal("191950"), Decimal("0.32")),
    (Decimal("243725"), Decimal("0.35")),
    (Decimal("609350"), Decimal("0.37")),
]


def _bracket_tax(taxable_annual: Decimal, brackets) -> Decimal:
    if taxable_annual <= 0:
        return Decimal("0")
    tax = Decimal("0")
    for i, (floor, rate) in enumerate(brackets):
        ceiling = brackets[i + 1][0] if i + 1 < len(brackets) else None
        if taxable_annual <= floor:
            break
        upper = taxable_annual if ceiling is None else min(taxable_annual, ceiling)
        tax += (upper - floor) * rate
        if ceiling is None or taxable_annual <= ceiling:
            break
    return tax


def estimate_federal_tax(gross_monthly: Decimal) -> Decimal:
    """Rough monthly federal income tax (single filer, standard deduction)."""
    annual = money(gross_monthly) * 12
    taxable = max(Decimal("0"), annual - FED_STANDARD_DEDUCTION)
    return money(_bracket_tax(taxable, _FED_BRACKETS) / 12)


# ── North Dakota state income tax (near-flat, 2023+) ──────────────────────────
# ND's 2023 reform: ~0% up to a threshold, then 1.95% and 2.50%. Single filer.
ND_STANDARD_DEDUCTION = Decimal("14600")  # ND conforms to federal std deduction
_ND_BRACKETS = [
    (Decimal("0"), Decimal("0.0")),
    (Decimal("44725"), Decimal("0.0195")),
    (Decimal("225975"), Decimal("0.025")),
]


def estimate_nd_state_tax(gross_monthly: Decimal) -> Decimal:
    """Rough monthly North Dakota income tax (single filer)."""
    annual = money(gross_monthly) * 12
    taxable = max(Decimal("0"), annual - ND_STANDARD_DEDUCTION)
    return money(_bracket_tax(taxable, _ND_BRACKETS) / 12)
