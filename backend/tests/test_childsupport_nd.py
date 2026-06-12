"""Tests for the child support engine and the North Dakota provider.

These are pure-Python unit tests (no DB / no async). They assert the *mechanics*
of the engine and ND algorithm — net-income deductions, schedule monotonicity,
the $25,000 cap, the equal-residence offset, split custody, deviations, and
registry/versioning — rather than exact official dollar amounts, because the
encoded ND schedule is provisional until verified against the official table.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.childsupport import (
    ChildSupportInput,
    ParentFinancials,
    calculate,
)
from app.services.childsupport.engine import UnsupportedJurisdictionError
from app.services.childsupport.jurisdictions import nd_schedule_2024 as schedule
from app.services.childsupport.models import CustodyType, ModelType
from app.services.childsupport.registry import get_provider, list_jurisdictions


def _parent(role, gross, **kw):
    return ParentFinancials(role=role, gross_monthly_income=Decimal(str(gross)), **kw)


def _input(num_children=1, parents=None, **kw):
    return ChildSupportInput(
        jurisdiction="ND",
        num_children=num_children,
        parents=parents or [_parent("respondent", 5000)],
        effective_date=date(2025, 1, 1),
        **kw,
    )


# ── schedule ──────────────────────────────────────────────────────────────────


def test_schedule_is_monotonic_in_income():
    prev = Decimal("-1")
    for income in range(0, 26000, 1000):
        amt = schedule.lookup(Decimal(income), 1)
        assert amt >= prev, f"schedule decreased at income {income}"
        prev = amt


def test_schedule_increases_with_children():
    income = Decimal("4000")
    amounts = [schedule.lookup(income, n) for n in range(1, 7)]
    assert amounts == sorted(amounts)
    assert amounts[0] < amounts[-1]


def test_schedule_cap_at_ceiling():
    assert schedule.lookup(Decimal("25000"), 1) == Decimal("3500.00")
    assert schedule.lookup(Decimal("25000"), 2) == Decimal("4250.00")
    assert schedule.lookup(Decimal("25000"), 3) == Decimal("5000.00")
    # Above the ceiling stays capped.
    assert schedule.lookup(Decimal("80000"), 1) == Decimal("3500.00")


def test_schedule_children_clamped_to_six():
    assert schedule.lookup(Decimal("5000"), 9) == schedule.lookup(Decimal("5000"), 6)


def test_minimum_order_floor():
    amt = schedule.lookup(Decimal("100"), 1)
    assert amt >= schedule.MINIMUM_ORDER


# ── net income ──────────────────────────────────────────────────────────────


def test_net_income_uses_explicit_deductions():
    p = _parent(
        "respondent",
        5000,
        federal_income_tax=Decimal("500"),
        state_income_tax=Decimal("100"),
        fica_tax=Decimal("382.50"),
    )
    ws = calculate(_input(parents=[p]))
    net_line = next(ln for ln in ws.lines if ln.code == "OBLIGOR.NET")
    assert net_line.amount == Decimal("4017.50")
    # Explicit deductions are not flagged as estimates.
    fed = next(ln for ln in ws.lines if ln.code == "OBLIGOR.FED")
    assert fed.estimated is False


def test_net_income_estimates_when_missing():
    ws = calculate(_input(parents=[_parent("respondent", 6000)]))
    fed = next(ln for ln in ws.lines if ln.code == "OBLIGOR.FED")
    assert fed.estimated is True
    assert fed.amount < 0


def test_estimates_suppressed_when_disallowed():
    ws = calculate(_input(parents=[_parent("respondent", 6000)], allow_estimates=False))
    fed = next(ln for ln in ws.lines if ln.code == "OBLIGOR.FED")
    assert fed.amount == Decimal("0.00")


def test_other_children_in_home_reduces_net():
    base = calculate(_input(parents=[_parent("respondent", 6000)]))
    with_kids = calculate(
        _input(parents=[_parent("respondent", 6000, other_children_in_home=2)])
    )
    base_net = next(ln for ln in base.lines if ln.code == "OBLIGOR.NET").amount
    kids_net = next(ln for ln in with_kids.lines if ln.code == "OBLIGOR.NET").amount
    assert kids_net < base_net


# ── custody scenarios ──────────────────────────────────────────────────────


def test_primary_infers_obligor_from_overnights():
    parents = [
        _parent("petitioner", 8000, annual_overnights=300),  # custodial
        _parent("respondent", 4000, annual_overnights=65),  # obligor
    ]
    ws = calculate(_input(num_children=2, parents=parents))
    assert ws.obligor_role == "respondent"
    assert ws.presumptive_amount > 0


def test_equal_residence_offset_higher_earner_pays():
    parents = [
        _parent("petitioner", 9000, annual_overnights=182),
        _parent("respondent", 4000, annual_overnights=183),
    ]
    ws = calculate(
        _input(num_children=2, parents=parents, custody_type=CustodyType.EQUAL.value)
    )
    assert ws.obligor_role == "petitioner"  # higher income -> higher amount -> pays
    offset = next(ln for ln in ws.lines if ln.code == "OFFSET")
    assert offset.amount > 0
    # Offset is less than the higher earner's standalone schedule amount.
    pa_sched = next(ln for ln in ws.lines if ln.code == "PA.SCHED").amount
    assert ws.presumptive_amount < pa_sched


def test_equal_residence_equal_incomes_nets_to_zero():
    parents = [
        _parent("petitioner", 5000, federal_income_tax=Decimal("400"),
                state_income_tax=Decimal("80"), fica_tax=Decimal("382.50")),
        _parent("respondent", 5000, federal_income_tax=Decimal("400"),
                state_income_tax=Decimal("80"), fica_tax=Decimal("382.50")),
    ]
    ws = calculate(
        _input(num_children=1, parents=parents, custody_type=CustodyType.EQUAL.value)
    )
    assert ws.presumptive_amount == Decimal("0.00")


def test_split_custody_nets_obligations():
    parents = [
        _parent("petitioner", 7000),
        _parent("respondent", 3000),
    ]
    ws = calculate(
        _input(
            num_children=2,
            parents=parents,
            custody_type=CustodyType.SPLIT.value,
            children_with_parent_a=1,
        )
    )
    assert ws.presumptive_amount >= 0
    assert any(ln.code == "SPLIT.NET" for ln in ws.lines)
    # Higher earner owes more for the child with the other parent.
    assert ws.obligor_role == "petitioner"


# ── deviation ─────────────────────────────────────────────────────────────


def test_deviation_overrides_presumptive_and_records_reason():
    ws = calculate(
        _input(
            parents=[_parent("respondent", 6000)],
            deviation_amount=Decimal("250"),
            deviation_reason="Child's extraordinary medical needs",
        )
    )
    assert ws.final_amount == Decimal("250.00")
    assert ws.presumptive_amount != ws.final_amount
    assert ws.deviation_reason == "Child's extraordinary medical needs"


def test_deviation_without_reason_warns():
    ws = calculate(
        _input(parents=[_parent("respondent", 6000)], deviation_amount=Decimal("250"))
    )
    assert any("reason" in w.lower() for w in ws.warnings)


# ── provider metadata / registry ─────────────────────────────────────────────


def test_provisional_schedule_emits_warning():
    ws = calculate(_input())
    assert any("PROVISIONAL" in w for w in ws.warnings)


def test_worksheet_carries_citations_and_metadata():
    ws = calculate(_input())
    assert ws.model_type == ModelType.OBLIGOR_NET_INCOME
    assert any("75-02-04.1-10" in c for c in ws.citations)
    assert ws.schedule_version == schedule.SCHEDULE_VERSION


def test_registry_lists_north_dakota():
    juris = list_jurisdictions()
    nd = next(j for j in juris if j["state_code"] == "ND")
    assert nd["model_type"] == "obligor_net_income"


def test_registry_version_resolution_by_date():
    p = get_provider("ND", date(2025, 6, 1))
    assert p is not None and p.state_code == "ND"


def test_unsupported_jurisdiction_raises():
    inp = _input()
    inp.jurisdiction = "ZZ"
    with pytest.raises(UnsupportedJurisdictionError):
        calculate(inp)


def test_worksheet_serializes_to_dict():
    ws = calculate(_input())
    d = ws.to_dict()
    assert d["jurisdiction"] == "ND"
    assert isinstance(d["lines"], list) and d["lines"]
    assert d["final_amount"] == str(ws.final_amount)
