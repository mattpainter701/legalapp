"""North Dakota child support guideline schedule — N.D. Admin. Code 75-02-04.1-10.

Source of truth (verify before production use):
  * N.D. Admin. Code ch. 75-02-04.1 (Child Support Guidelines)
    https://ndlegis.gov/information/acdata/pdf/75-02-04.1.pdf
  * ND HHS annotated guidelines & official calculator
    https://www.hhs.nd.gov/childsupport/partners/lawyers/child-support-guidelines

ND uses an OBLIGOR-NET-INCOME model: the presumptive amount is read directly
from a schedule keyed on the obligor's *monthly net income* and the number of
children the order covers. The schedule rises with income up to a ceiling of
$25,000 monthly net income; above the ceiling the guideline amount is capped (a
court may deviate upward). Verified anchor (top of schedule, monthly amounts):

    children:        1       2       3       4       5       6
    @ $25,000 net: $3,500  $4,250  $5,000  $5,500  $5,900  $6,200   (CAP)

⚠️  VERIFICATION REQUIRED ⚠️
The intermediate bracket amounts below the ceiling are encoded here as a
TRANSPARENT, PROVISIONAL approximation (a declining-effective-percentage curve
anchored to the verified cap row). They are NOT the official per-bracket table.
Before this module is relied on for a filing, replace ``_RAW_SCHEDULE`` with the
official table from the source above and set ``VERIFIED = True``. The engine
emits a prominent warning on every worksheet while ``VERIFIED`` is False.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.childsupport.models import money

SCHEDULE_VERSION = "2024-08"
EFFECTIVE_DATE = "2024-08-23"
VERIFIED = False  # flip to True only after replacing _RAW_SCHEDULE with official data

# Monthly net income ceiling. At/above this the capped amount applies (75-02-04.1-10).
INCOME_CEILING = Decimal("25000")

# Verified cap row (monthly obligation at the income ceiling), indexed by child count.
CAP_AMOUNTS: dict[int, Decimal] = {
    1: Decimal("3500"),
    2: Decimal("4250"),
    3: Decimal("5000"),
    4: Decimal("5500"),
    5: Decimal("5900"),
    6: Decimal("6200"),
}

# Provisional low-income effective-percentage anchors (share of net income at the
# bottom of the schedule). Used only to interpolate the PROVISIONAL curve between
# $0 and the verified cap. Replace with the official table to make authoritative.
_LOW_INCOME_PCT: dict[int, Decimal] = {
    1: Decimal("0.190"),
    2: Decimal("0.270"),
    3: Decimal("0.320"),
    4: Decimal("0.360"),
    5: Decimal("0.390"),
    6: Decimal("0.410"),
}

# Minimum monthly order floor (ND applies a minimum for very low / imputed income).
MINIMUM_ORDER = Decimal("75")


def _clamp_children(n: int) -> int:
    return max(1, min(6, n))


def lookup(net_monthly_income: Decimal, num_children: int) -> Decimal:
    """Return the presumptive monthly support amount from the schedule.

    While ``VERIFIED`` is False this uses a provisional declining-percentage
    interpolation anchored to the verified cap row; swap in ``_RAW_SCHEDULE`` to
    make it authoritative.
    """
    income = money(max(Decimal("0"), net_monthly_income))
    n = _clamp_children(num_children)

    cap_amount = CAP_AMOUNTS[n]
    if income >= INCOME_CEILING:
        return money(cap_amount)

    # Effective percentage declines linearly from the low-income anchor down to
    # the cap's implied percentage as income approaches the ceiling.
    low_pct = _LOW_INCOME_PCT[n]
    cap_pct = cap_amount / INCOME_CEILING
    frac = income / INCOME_CEILING  # 0..1
    eff_pct = low_pct - (low_pct - cap_pct) * frac
    amount = income * eff_pct

    if income > 0:
        amount = max(amount, MINIMUM_ORDER)
    return money(amount)
