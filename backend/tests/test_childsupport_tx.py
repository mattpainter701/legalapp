"""Tests for the Texas (percentage-of-income) child support provider.

Texas guideline percentages are statutory (Tex. Fam. Code §154.125), so unlike
the provisional ND schedule these assert exact dollar outcomes.
"""

from datetime import date
from decimal import Decimal

from app.services.childsupport import ChildSupportInput, ParentFinancials, calculate
from app.services.childsupport.jurisdictions.texas import NET_RESOURCES_CAP
from app.services.childsupport.models import ModelType
from app.services.childsupport.registry import get_provider, list_jurisdictions


def _input(num_children=1, gross=5000, parents=None, **kw):
    return ChildSupportInput(
        jurisdiction="TX",
        num_children=num_children,
        parents=parents
        or [
            ParentFinancials(
                role="respondent",
                gross_monthly_income=Decimal(str(gross)),
                # Provide explicit deductions so the percentage math is exact.
                federal_income_tax=Decimal("0"),
                fica_tax=Decimal("0"),
            )
        ],
        effective_date=date(2025, 1, 1),
        **kw,
    )


def test_standard_percentages_exact():
    # With zero deductions, net resources == gross, so amount == pct * gross.
    expected = {1: "1000.00", 2: "1250.00", 3: "1500.00", 4: "1750.00", 5: "2000.00"}
    for n, amt in expected.items():
        ws = calculate(_input(num_children=n, gross=5000))
        assert ws.final_amount == Decimal(amt), f"{n} children"


def test_six_plus_children_at_least_forty_percent():
    ws = calculate(_input(num_children=7, gross=5000))
    assert ws.final_amount >= Decimal("2000.00")  # >= 40%


def test_net_resources_cap_applied():
    ws = calculate(_input(num_children=1, gross=20000))
    # 20% of the capped net resources, not of full gross.
    assert ws.final_amount == (NET_RESOURCES_CAP * Decimal("0.20")).quantize(
        Decimal("0.01")
    )
    assert any("cap" in w.lower() for w in ws.warnings)


def test_multiple_family_reduces_percentage():
    base = calculate(_input(num_children=2, gross=5000))
    multi = calculate(
        _input(
            num_children=2,
            parents=[
                ParentFinancials(
                    role="respondent",
                    gross_monthly_income=Decimal("5000"),
                    federal_income_tax=Decimal("0"),
                    fica_tax=Decimal("0"),
                    other_children_in_home=2,
                )
            ],
        )
    )
    assert multi.final_amount < base.final_amount


def test_deductions_reduce_net_resources():
    ws = calculate(
        _input(
            parents=[
                ParentFinancials(
                    role="respondent",
                    gross_monthly_income=Decimal("5000"),
                    fica_tax=Decimal("382.50"),
                    federal_income_tax=Decimal("400"),
                    health_insurance_children=Decimal("200"),
                )
            ]
        )
    )
    # Net = 5000 - 382.50 - 400 - 200 = 4017.50; 20% = 803.50
    assert ws.final_amount == Decimal("803.50")


def test_model_metadata_and_registry():
    ws = calculate(_input())
    assert ws.model_type == ModelType.PERCENTAGE_OF_INCOME
    assert any("154.125" in c for c in ws.citations)
    codes = {j["state_code"] for j in list_jurisdictions()}
    assert {"ND", "TX"} <= codes
    assert get_provider("TX").state_name == "Texas"


def test_equal_custody_warns_no_offset():
    ws = calculate(_input(num_children=2, custody_type="equal"))
    assert any("offset" in w.lower() for w in ws.warnings)
