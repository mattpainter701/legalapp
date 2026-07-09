"""Texas child support guideline provider — Tex. Fam. Code ch. 154.

Texas uses a *percentage of obligor net resources* model. Unlike North Dakota's
schedule, the Texas guideline percentages are set directly by statute
(§154.125), so this provider is accurate from the statute itself — it is a good
second model that exercises the engine's pluggability with a different
``model_type``.

Algorithm:
  * Net resources (§154.062): gross income less Social Security/FICA, federal
    income tax (single taxpayer, one personal exemption, standard deduction),
    union dues, and the child's health/dental insurance premium.
  * Net resources are capped at a statutory ceiling (§154.125(a-1)); the
    percentage applies only to the first cap dollars (a court may order more
    based on proven needs).
  * Standard percentages (§154.125): 1=20%, 2=25%, 3=30%, 4=35%, 5=40%, 6+ ≥40%.
  * Multiple-family adjustment (§154.129): when the obligor supports children in
    more than one household, the reduced percentage table applies.

The cap is adjusted by the OAG for inflation every six years; the encoded value
and effective date must be confirmed for the order's date (a warning is emitted).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.childsupport import deductions
from app.services.childsupport.base import GuidelineProvider
from app.services.childsupport.models import (
    ChildSupportInput,
    CustodyType,
    ModelType,
    ParentFinancials,
    Worksheet,
    money,
)

# Statutory monthly net-resources cap (§154.125(a-1)). $9,200 effective 2019-09-01;
# adjusted every 6 years — verify for the order's effective date.
NET_RESOURCES_CAP = Decimal("9200")
CAP_EFFECTIVE = "2019-09-01"

# §154.125 standard percentages by number of children before the court.
_STANDARD_PCT = {
    1: Decimal("0.20"),
    2: Decimal("0.25"),
    3: Decimal("0.30"),
    4: Decimal("0.35"),
    5: Decimal("0.40"),
    6: Decimal("0.40"),
}

# §154.129 multiple-family table. Keyed by (children_before_court, other_children)
# -> percentage of net resources. Columns 1..7 before the court; rows 0..7 other.
_MULTIFAMILY_PCT = {
    0: [20.00, 25.00, 30.00, 35.00, 40.00, 40.00, 40.00],
    1: [17.50, 22.50, 27.38, 32.20, 37.33, 37.71, 38.00],
    2: [16.00, 20.63, 25.20, 30.33, 35.43, 36.00, 36.44],
    3: [14.75, 19.00, 24.00, 29.00, 34.00, 34.67, 35.20],
    4: [13.60, 18.33, 23.14, 28.00, 32.89, 33.60, 34.18],
    5: [13.33, 17.86, 22.50, 27.22, 32.00, 32.73, 33.33],
    6: [13.14, 17.50, 22.00, 26.60, 31.27, 32.00, 32.62],
    7: [13.00, 17.22, 21.60, 26.09, 30.67, 31.41, 32.04],
}

_CITATIONS = [
    "Tex. Fam. Code § 154.062 (net resources)",
    "Tex. Fam. Code § 154.125 (application of guidelines; cap)",
    "Tex. Fam. Code § 154.129 (multiple households)",
]


def _multifamily_pct(before_court: int, other: int) -> Decimal:
    col = max(1, min(7, before_court)) - 1
    row = max(0, min(7, other))
    return money(Decimal(str(_MULTIFAMILY_PCT[row][col])) / 100)


class TexasProvider(GuidelineProvider):
    state_code = "TX"
    state_name = "Texas"
    model_type = ModelType.PERCENTAGE_OF_INCOME
    schedule_version = "2019-cap"
    effective_date = date.fromisoformat(CAP_EFFECTIVE)
    schedule_unverified = False

    def available_deviations(self) -> list[str]:
        return [
            "Proven needs of the child exceeding the cap (§154.126)",
            "Age and needs of the child",
            "Childcare expenses for employment",
            "Extraordinary educational, healthcare, or special needs",
            "Periods of possession and access",
            "Other relevant factors (§154.123)",
        ]

    def _net_resources(
        self, ws: Worksheet, p: ParentFinancials, inp: ChildSupportInput, tag: str
    ) -> Decimal:
        gross = p.gross_monthly_income
        ws.add(
            f"{tag}.GROSS", "Gross monthly resources", gross, detail=p.name or p.role
        )

        fica = p.fica_tax
        fica_est = fica is None
        if fica is None:
            fica = deductions.estimate_fica(gross) if inp.allow_estimates else money(0)
        ws.add(f"{tag}.FICA", "Less: Social Security / FICA", -fica, estimated=fica_est)

        fed = p.federal_income_tax
        fed_est = fed is None
        if fed is None:
            fed = (
                deductions.estimate_federal_tax(gross)
                if inp.allow_estimates
                else money(0)
            )
        ws.add(
            f"{tag}.FED",
            "Less: federal income tax (single, std deduction)",
            -fed,
            estimated=fed_est,
        )

        if p.union_dues:
            ws.add(f"{tag}.UNION", "Less: union dues", -p.union_dues)
        if p.health_insurance_children:
            ws.add(
                f"{tag}.HEALTH",
                "Less: child health/dental insurance",
                -p.health_insurance_children,
            )

        net = money(
            max(
                Decimal("0"),
                gross - fica - fed - p.union_dues - p.health_insurance_children,
            )
        )
        ws.add(f"{tag}.NET", "Monthly net resources", net)

        capped = min(net, NET_RESOURCES_CAP)
        if net > NET_RESOURCES_CAP:
            ws.add(
                f"{tag}.CAP",
                "Net resources after statutory cap",
                capped,
                detail=f"Capped at ${NET_RESOURCES_CAP:,.0f}/mo (§154.125)",
            )
            ws.warnings.append(
                "Net resources exceed the statutory cap; the cap is adjusted "
                "periodically — verify the current amount for the order date."
            )
        return capped

    def compute(self, inp: ChildSupportInput) -> Worksheet:
        ws = Worksheet(
            jurisdiction=self.state_code,
            state_name=self.state_name,
            model_type=self.model_type,
            schedule_version=self.schedule_version,
            effective_date=inp.effective_date,
            num_children=inp.num_children,
            obligor_role=inp.obligor_role,
            presumptive_amount=money(0),
            final_amount=money(0),
            citations=list(_CITATIONS),
        )
        ws.warnings.extend(self.validate(inp))
        if inp.custody_type in (CustodyType.EQUAL.value, CustodyType.SPLIT.value):
            ws.warnings.append(
                "Texas has no statutory offset for equal/split possession; the "
                "guideline is applied to the obligor and the court exercises "
                "discretion. Confirm obligor designation."
            )

        obligor = self._select_obligor(inp)
        if obligor is None:
            ws.warnings.append("Could not determine the obligor parent.")
            ws.add("RESULT", "Presumptive monthly child support", money(0))
            return ws
        ws.obligor_role = obligor.role

        net = self._net_resources(ws, obligor, inp, "OBLIGOR")
        n = max(1, inp.num_children)
        other = obligor.other_children_in_home

        if other > 0:
            pct = _multifamily_pct(n, other)
            ws.add(
                "PCT",
                f"Multiple-family percentage ({n} before court, {other} other)",
                None,
                detail=f"{pct * 100:.2f}% (§154.129)",
            )
        else:
            pct = _STANDARD_PCT[min(6, n)]
            ws.add(
                "PCT",
                f"Guideline percentage ({n} child(ren))",
                None,
                detail=f"{pct * 100:.0f}% of net resources (§154.125)",
            )

        amount = money(net * pct)
        ws.presumptive_amount = amount

        if inp.deviation_amount is not None:
            ws.deviation_amount = money(inp.deviation_amount)
            ws.deviation_reason = inp.deviation_reason
            ws.final_amount = money(inp.deviation_amount)
            ws.add(
                "DEVIATION",
                "Deviation from guideline",
                ws.final_amount,
                detail=inp.deviation_reason or "No reason provided",
            )
        else:
            ws.final_amount = amount

        ws.add("RESULT", "Presumptive monthly child support", ws.presumptive_amount)
        return ws

    def _select_obligor(self, inp: ChildSupportInput) -> ParentFinancials | None:
        if not inp.parents:
            return None
        if inp.obligor_role:
            for p in inp.parents:
                if p.role == inp.obligor_role:
                    return p
        if len(inp.parents) == 1:
            return inp.parents[0]
        return sorted(
            inp.parents,
            key=lambda p: (p.annual_overnights, -p.gross_monthly_income),
        )[0]
