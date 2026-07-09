"""North Dakota child support guideline provider — N.D. Admin. Code ch. 75-02-04.1.

ND is an *obligor-net-income* model. The presumptive obligation is read from the
75-02-04.1-10 schedule using the obligor's monthly net income and the number of
children. This provider produces an auditable worksheet covering:

  * gross -> net income (75-02-04.1-01, -04): tax/FICA/required deductions, an
    "other children in the home" deduction, and existing support paid;
  * imputed income flagging (75-02-04.1-07);
  * the schedule lookup (75-02-04.1-10);
  * equal residential responsibility offset (75-02-04.1-08.2);
  * split custody netting (75-02-04.1-03);
  * a structured deviation layer (75-02-04.1-09).

Schedule amounts come from ``nd_schedule_2024`` and are PROVISIONAL until that
module is verified (the engine warns accordingly).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.childsupport import deductions
from app.services.childsupport.base import GuidelineProvider
from app.services.childsupport.jurisdictions import nd_schedule_2024 as schedule
from app.services.childsupport.models import (
    ChildSupportInput,
    CustodyType,
    ModelType,
    ParentFinancials,
    Worksheet,
    money,
)

_CITATIONS = [
    "N.D. Admin. Code 75-02-04.1-01 (definitions / net income)",
    "N.D. Admin. Code 75-02-04.1-04 (determination of net income)",
    "N.D. Admin. Code 75-02-04.1-07 (imputed income)",
    "N.D. Admin. Code 75-02-04.1-08.2 (equal residential responsibility)",
    "N.D. Admin. Code 75-02-04.1-09 (criteria for rebuttal/deviation)",
    "N.D. Admin. Code 75-02-04.1-10 (child support guideline schedule)",
]


class NorthDakotaProvider(GuidelineProvider):
    state_code = "ND"
    state_name = "North Dakota"
    model_type = ModelType.OBLIGOR_NET_INCOME
    schedule_version = schedule.SCHEDULE_VERSION
    effective_date = date.fromisoformat(schedule.EFFECTIVE_DATE)
    schedule_unverified = not schedule.VERIFIED

    def available_deviations(self) -> list[str]:
        return [
            "Increased need due to a child's medical, dental, educational, or "
            "special needs (75-02-04.1-09)",
            "The increased ability of an obligor to provide support",
            "Travel expenses to exercise parenting time",
            "Reduced ability of the obligor to pay due to extraordinary expenses",
            "A property settlement or assets not reflected in net income",
            "The needs of a new spouse or other dependents in limited circumstances",
            "Any other relevant factor making guideline application inequitable",
        ]

    # ── net income (75-02-04.1-01, -04) ──────────────────────────────────────
    def _net_income(
        self, ws: Worksheet, parent: ParentFinancials, inp: ChildSupportInput, tag: str
    ) -> Decimal:
        gross = parent.gross_monthly_income
        ws.add(
            f"{tag}.GROSS",
            "Gross monthly income",
            gross,
            detail=parent.name or parent.role,
        )

        fed = parent.federal_income_tax
        fed_est = fed is None
        if fed is None:
            fed = (
                deductions.estimate_federal_tax(gross)
                if inp.allow_estimates
                else money(0)
            )
        ws.add(f"{tag}.FED", "Less: federal income tax", -fed, estimated=fed_est)

        st = parent.state_income_tax
        st_est = st is None
        if st is None:
            st = (
                deductions.estimate_nd_state_tax(gross)
                if inp.allow_estimates
                else money(0)
            )
        ws.add(f"{tag}.STATE", "Less: ND state income tax", -st, estimated=st_est)

        fica = parent.fica_tax
        fica_est = fica is None
        if fica is None:
            fica = deductions.estimate_fica(gross) if inp.allow_estimates else money(0)
        ws.add(
            f"{tag}.FICA",
            "Less: FICA (Social Security + Medicare)",
            -fica,
            estimated=fica_est,
        )

        if parent.required_retirement:
            ws.add(
                f"{tag}.RET",
                "Less: required retirement contributions",
                -parent.required_retirement,
            )
        if parent.union_dues:
            ws.add(f"{tag}.UNION", "Less: union dues", -parent.union_dues)
        if parent.existing_support_paid:
            ws.add(
                f"{tag}.PRIOR",
                "Less: existing court-ordered support paid",
                -parent.existing_support_paid,
            )

        subtotal = (
            gross
            - fed
            - st
            - fica
            - parent.required_retirement
            - parent.union_dues
            - parent.existing_support_paid
        )

        # Other children living in the obligor's home (75-02-04.1-10): deduct a
        # hypothetical support amount for those children at this net income.
        if parent.other_children_in_home > 0 and subtotal > 0:
            other = schedule.lookup(subtotal, parent.other_children_in_home)
            ws.add(
                f"{tag}.OTHERKIDS",
                f"Less: support for {parent.other_children_in_home} other "
                "child(ren) in the home",
                -other,
                detail="Hypothetical schedule amount per 75-02-04.1-10",
                estimated=self.schedule_unverified,
            )
            subtotal = subtotal - other

        net = money(max(Decimal("0"), subtotal))
        ws.add(f"{tag}.NET", "Monthly net income", net)
        return net

    def _schedule_amount(
        self, ws: Worksheet, net: Decimal, n: int, tag: str
    ) -> Decimal:
        amt = schedule.lookup(net, n)
        ws.add(
            f"{tag}.SCHED",
            f"Guideline schedule amount ({n} child(ren))",
            amt,
            detail=f"75-02-04.1-10 @ net ${net:,.2f}"
            + (
                f" (capped at ${schedule.INCOME_CEILING:,.0f} net)"
                if net >= schedule.INCOME_CEILING
                else ""
            ),
            estimated=self.schedule_unverified,
        )
        return amt

    # ── main entry ────────────────────────────────────────────────────────────
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

        custody = inp.custody_type
        if custody == CustodyType.SPLIT.value:
            presumptive = self._compute_split(ws, inp)
        elif custody == CustodyType.EQUAL.value:
            presumptive = self._compute_equal(ws, inp)
        else:
            presumptive = self._compute_primary(ws, inp)

        ws.presumptive_amount = money(presumptive)

        # Deviation layer (75-02-04.1-09)
        if inp.deviation_amount is not None:
            ws.deviation_amount = money(inp.deviation_amount)
            ws.deviation_reason = inp.deviation_reason
            ws.final_amount = money(inp.deviation_amount)
            ws.add(
                "DEVIATION",
                "Deviation from guideline (rebuttal)",
                ws.final_amount,
                detail=inp.deviation_reason or "No reason provided",
            )
        else:
            ws.final_amount = ws.presumptive_amount

        ws.add("RESULT", "Presumptive monthly child support", ws.presumptive_amount)
        return ws

    # ── primary residential responsibility ───────────────────────────────────
    def _compute_primary(self, ws: Worksheet, inp: ChildSupportInput) -> Decimal:
        obligor = self._select_obligor(inp)
        if obligor is None:
            ws.warnings.append("Could not determine the obligor parent.")
            return money(0)
        ws.obligor_role = obligor.role
        net = self._net_income(ws, obligor, inp, "OBLIGOR")
        return self._schedule_amount(ws, net, inp.num_children, "OBLIGOR")

    # ── equal residential responsibility offset (75-02-04.1-08.2) ────────────
    def _compute_equal(self, ws: Worksheet, inp: ChildSupportInput) -> Decimal:
        if len(inp.parents) < 2:
            ws.warnings.append(
                "Equal residential responsibility requires both parents' incomes."
            )
            return self._compute_primary(ws, inp)
        a, b = inp.parents[0], inp.parents[1]
        net_a = self._net_income(ws, a, inp, "PA")
        net_b = self._net_income(ws, b, inp, "PB")
        amt_a = self._schedule_amount(ws, net_a, inp.num_children, "PA")
        amt_b = self._schedule_amount(ws, net_b, inp.num_children, "PB")
        # The parent with the greater amount pays the difference (offset).
        if amt_a >= amt_b:
            obligor, diff = a, amt_a - amt_b
        else:
            obligor, diff = b, amt_b - amt_a
        ws.obligor_role = obligor.role
        ws.add(
            "OFFSET",
            "Equal residential responsibility offset (higher pays difference)",
            money(diff),
            detail="75-02-04.1-08.2",
        )
        return money(diff)

    # ── split custody (75-02-04.1-03) ────────────────────────────────────────
    def _compute_split(self, ws: Worksheet, inp: ChildSupportInput) -> Decimal:
        if len(inp.parents) < 2:
            ws.warnings.append("Split custody requires both parents' incomes.")
            return self._compute_primary(ws, inp)
        a, b = inp.parents[0], inp.parents[1]
        kids_a = max(0, min(inp.num_children, inp.children_with_parent_a))
        kids_b = inp.num_children - kids_a
        if kids_a == 0 or kids_b == 0:
            ws.warnings.append(
                "Split custody entered but all children reside with one parent; "
                "treated as primary residential responsibility."
            )
        net_a = self._net_income(ws, a, inp, "PA")
        net_b = self._net_income(ws, b, inp, "PB")
        # Each parent owes for the children living with the OTHER parent.
        owes_a = self._schedule_amount(ws, net_a, kids_b, "PA") if kids_b else money(0)
        owes_b = self._schedule_amount(ws, net_b, kids_a, "PB") if kids_a else money(0)
        if owes_a >= owes_b:
            obligor, diff = a, owes_a - owes_b
        else:
            obligor, diff = b, owes_b - owes_a
        ws.obligor_role = obligor.role
        ws.add(
            "SPLIT.NET",
            "Split custody net obligation (higher pays difference)",
            money(diff),
            detail=f"Parent A keeps {kids_a}, Parent B keeps {kids_b} (75-02-04.1-03)",
        )
        return money(diff)

    # ── obligor selection ─────────────────────────────────────────────────────
    def _select_obligor(self, inp: ChildSupportInput) -> ParentFinancials | None:
        if not inp.parents:
            return None
        if inp.obligor_role:
            for p in inp.parents:
                if p.role == inp.obligor_role:
                    return p
        if len(inp.parents) == 1:
            return inp.parents[0]
        # Infer: the parent with fewer overnights is the obligor; tie-break on
        # higher gross income.
        return sorted(
            inp.parents,
            key=lambda p: (p.annual_overnights, -p.gross_monthly_income),
        )[0]
