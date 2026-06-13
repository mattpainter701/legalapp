from decimal import Decimal

import pytest


pytestmark = pytest.mark.asyncio

BASE = "/api/plugins/domestic"


async def _create_case(client, name: str) -> dict:
    resp = await client.post(
        f"{BASE}/cases",
        json={
            "case_name": name,
            "case_type": "support",
            "jurisdiction": "ND",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_party(
    client, case_id: str, name: str, role: str = "parent_a"
) -> dict:
    resp = await client.post(
        f"{BASE}/cases/{case_id}/parties",
        json={"name": name, "role": role},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _save_calculation(client, case_id: str) -> dict:
    resp = await client.post(
        f"{BASE}/cases/{case_id}/calculations",
        json={
            "label": "Guideline worksheet",
            "request": {
                "jurisdiction": "ND",
                "num_children": 1,
                "parents": [
                    {
                        "role": "respondent",
                        "gross_monthly_income": "5000.00",
                        "federal_income_tax": "0.00",
                        "state_income_tax": "0.00",
                        "fica_tax": "0.00",
                    }
                ],
                "effective_date": "2026-01-01",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_order(client, case_id: str, payload: dict) -> dict:
    resp = await client.post(f"{BASE}/cases/{case_id}/orders", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _money(value) -> Decimal:
    return Decimal(str(value))


async def test_delete_payment_restores_applied_arrears(client):
    case = await _create_case(client, "Arrears restore")
    order = await _create_order(
        client,
        case["id"],
        {
            "monthly_amount": "500.00",
            "arrears_balance": "1000.00",
            "status": "active",
        },
    )

    payment_resp = await client.post(
        f"{BASE}/cases/{case['id']}/orders/{order['id']}/payments",
        json={
            "payment_date": "2026-06-01",
            "amount": "300.00",
            "applied_to_current": "100.00",
            "applied_to_arrears": "200.00",
        },
    )
    assert payment_resp.status_code == 201, payment_resp.text
    payment = payment_resp.json()

    list_resp = await client.get(f"{BASE}/cases/{case['id']}/orders")
    assert list_resp.status_code == 200, list_resp.text
    paid_order = next(o for o in list_resp.json() if o["id"] == order["id"])
    assert _money(paid_order["arrears_balance"]) == Decimal("800.00")

    delete_resp = await client.delete(
        f"{BASE}/cases/{case['id']}/orders/{order['id']}/payments/{payment['id']}"
    )
    assert delete_resp.status_code == 204, delete_resp.text

    list_resp = await client.get(f"{BASE}/cases/{case['id']}/orders")
    assert list_resp.status_code == 200, list_resp.text
    restored_order = next(o for o in list_resp.json() if o["id"] == order["id"])
    assert _money(restored_order["arrears_balance"]) == Decimal("1000.00")


@pytest.mark.parametrize(
    ("field", "detail"),
    [
        ("obligor_party_id", "Obligor party not found"),
        ("obligee_party_id", "Obligee party not found"),
    ],
)
async def test_create_order_rejects_cross_case_party_ids(client, field, detail):
    target_case = await _create_case(client, f"Target case for {field}")
    other_case = await _create_case(client, f"Other case for {field}")
    other_party = await _create_party(client, other_case["id"], "Other Parent")

    resp = await client.post(
        f"{BASE}/cases/{target_case['id']}/orders",
        json={
            "monthly_amount": "500.00",
            field: other_party["id"],
        },
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == detail


async def test_create_order_rejects_cross_case_calculation_id(client):
    target_case = await _create_case(client, "Target case for calc")
    other_case = await _create_case(client, "Other case for calc")
    other_calc = await _save_calculation(client, other_case["id"])

    resp = await client.post(
        f"{BASE}/cases/{target_case['id']}/orders",
        json={
            "monthly_amount": "500.00",
            "calculation_id": other_calc["id"],
        },
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Calculation not found"
