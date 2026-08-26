"""API tests for the billing overhaul: live timers, draft invoice workflow,
sequential numbering, status transitions, and void-release behavior."""

import uuid
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import Matter


@pytest_asyncio.fixture
async def test_matter(db_session: AsyncSession, test_tenant, test_user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"test-matter-{uuid.uuid4().hex[:8]}",
        matter_name="Smith v. Jones",
        matter_type="litigation",
        counterparty="Acme Corp",
        hourly_rate=Decimal("250.00"),
    )
    db_session.add(matter)
    await db_session.commit()
    await db_session.refresh(matter)
    return matter


async def _log_time(client, matter_id: str, hours: str = "2.0") -> dict:
    resp = await client.post(
        "/api/billing/time-entries",
        json={
            "matter_id": str(matter_id),
            "description": "Drafted motion",
            "hours": hours,
            "date": "2026-07-01",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestTimer:
    async def test_start_stop_timer(self, client, test_matter):
        start = await client.post(
            "/api/billing/time-entries/timer/start",
            json={"matter_id": str(test_matter.id), "description": "Research"},
        )
        assert start.status_code == 201, start.text
        started = start.json()
        assert started["status"] == "running"
        assert started["timer_started_at"] is not None
        assert Decimal(started["hourly_rate"]) == Decimal("250.00")

        active = await client.get("/api/billing/time-entries/timer")
        assert active.status_code == 200
        assert active.json()["id"] == started["id"]

        stop = await client.post("/api/billing/time-entries/timer/stop", json={})
        assert stop.status_code == 200, stop.text
        stopped = stop.json()
        assert stopped["status"] == "draft"
        assert stopped["timer_started_at"] is None
        # Even a near-instant stop bills the minimum 6-minute increment
        assert Decimal(stopped["hours"]) == Decimal("0.10")
        assert Decimal(stopped["amount"]) == Decimal("25.00")

    async def test_only_one_running_timer(self, client, test_matter):
        first = await client.post(
            "/api/billing/time-entries/timer/start",
            json={"matter_id": str(test_matter.id)},
        )
        assert first.status_code == 201
        second = await client.post(
            "/api/billing/time-entries/timer/start",
            json={"matter_id": str(test_matter.id)},
        )
        assert second.status_code == 409

    async def test_cancel_timer_discards_entry(self, client, test_matter):
        start = await client.post(
            "/api/billing/time-entries/timer/start",
            json={"matter_id": str(test_matter.id)},
        )
        entry_id = start.json()["id"]

        cancel = await client.delete("/api/billing/time-entries/timer")
        assert cancel.status_code == 204

        gone = await client.get(f"/api/billing/time-entries/{entry_id}")
        assert gone.status_code == 404

    async def test_stop_without_timer_404(self, client):
        resp = await client.post("/api/billing/time-entries/timer/stop", json={})
        assert resp.status_code == 404

    async def test_running_entry_excluded_from_invoice(self, client, test_matter):
        await client.post(
            "/api/billing/time-entries/timer/start",
            json={"matter_id": str(test_matter.id)},
        )
        resp = await client.post(
            "/api/billing/invoices/generate",
            json={"matter_id": str(test_matter.id)},
        )
        # Only a running timer exists → nothing billable yet
        assert resp.status_code == 400


class TestInvoiceWorkflow:
    async def test_preview_and_selected_sources(self, client, test_matter):
        first = await _log_time(client, test_matter.id, hours="1.0")
        second = await _log_time(client, test_matter.id, hours="2.0")
        preview = await client.get(
            "/api/billing/invoices/preview", params={"matter_id": str(test_matter.id)}
        )
        assert preview.status_code == 200, preview.text
        assert {x["id"] for x in preview.json()["time_entries"]} == {
            first["id"],
            second["id"],
        }
        generated = await client.post(
            "/api/billing/invoices/generate",
            json={
                "matter_id": str(test_matter.id),
                "time_entry_ids": [first["id"]],
                "expense_ids": [],
            },
        )
        assert generated.status_code == 201, generated.text
        assert len(generated.json()["line_items"]) == 1

    async def test_overpayment_and_direct_paid_are_rejected(self, client, test_matter):
        await _log_time(client, test_matter.id, hours="1.0")
        inv = (
            await client.post(
                "/api/billing/invoices/generate",
                json={"matter_id": str(test_matter.id)},
            )
        ).json()
        assert (
            await client.patch(
                f"/api/billing/invoices/{inv['id']}", json={"status": "paid"}
            )
        ).status_code == 400
        assert (
            await client.patch(
                f"/api/billing/invoices/{inv['id']}", json={"status": "sent"}
            )
        ).status_code == 200
        over = await client.post(
            "/api/billing/payments",
            json={
                "invoice_id": inv["id"],
                "amount": str(Decimal(inv["total"]) + Decimal("0.01")),
                "payment_date": "2026-07-02",
            },
        )
        assert over.status_code == 400

    async def test_generate_creates_draft_with_sequential_number(
        self, client, test_matter
    ):
        await _log_time(client, test_matter.id)
        resp = await client.post(
            "/api/billing/invoices/generate",
            json={"matter_id": str(test_matter.id)},
        )
        assert resp.status_code == 201, resp.text
        inv = resp.json()
        assert inv["status"] == "draft"
        year = inv["issue_date"][:4]
        assert inv["invoice_number"] == f"INV-{year}-0001"
        assert Decimal(inv["balance_due"]) == Decimal(inv["total"])
        assert inv["matter_name"] == "Smith v. Jones"
        assert inv["billing_period_start"] == "2026-07-01"

        # Second invoice gets the next sequence number
        await _log_time(client, test_matter.id, hours="1.0")
        resp2 = await client.post(
            "/api/billing/invoices/generate",
            json={"matter_id": str(test_matter.id)},
        )
        assert resp2.status_code == 201
        assert resp2.json()["invoice_number"] == f"INV-{year}-0002"

    async def test_draft_cannot_jump_to_paid(self, client, test_matter):
        await _log_time(client, test_matter.id)
        inv = (
            await client.post(
                "/api/billing/invoices/generate",
                json={"matter_id": str(test_matter.id)},
            )
        ).json()

        resp = await client.patch(
            f"/api/billing/invoices/{inv['id']}", json={"status": "paid"}
        )
        assert resp.status_code == 400

    async def test_send_then_pay_flow(self, client, test_matter):
        await _log_time(client, test_matter.id)
        inv = (
            await client.post(
                "/api/billing/invoices/generate",
                json={"matter_id": str(test_matter.id)},
            )
        ).json()

        # Payments are blocked while the invoice is a draft
        blocked = await client.post(
            "/api/billing/payments",
            json={
                "invoice_id": inv["id"],
                "amount": "100.00",
                "payment_date": "2026-07-02",
            },
        )
        assert blocked.status_code == 400

        sent = await client.patch(
            f"/api/billing/invoices/{inv['id']}", json={"status": "sent"}
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["sent_at"] is not None

        pay = await client.post(
            "/api/billing/payments",
            json={
                "invoice_id": inv["id"],
                "amount": "100.00",
                "payment_date": "2026-07-02",
                "method": "check",
            },
        )
        assert pay.status_code == 201, pay.text

        detail = (await client.get(f"/api/billing/invoices/{inv['id']}")).json()
        assert detail["status"] == "partially_paid"
        assert Decimal(detail["amount_paid"]) == Decimal("100.00")
        assert Decimal(detail["balance_due"]) == Decimal(inv["total"]) - Decimal(
            "100.00"
        )

    async def test_void_releases_time_entries(self, client, test_matter):
        entry = await _log_time(client, test_matter.id)
        inv = (
            await client.post(
                "/api/billing/invoices/generate",
                json={"matter_id": str(test_matter.id)},
            )
        ).json()

        billed = (await client.get(f"/api/billing/time-entries/{entry['id']}")).json()
        assert billed["status"] == "invoiced"
        assert billed["invoice_id"] == inv["id"]

        void = await client.patch(
            f"/api/billing/invoices/{inv['id']}", json={"status": "void"}
        )
        assert void.status_code == 200, void.text

        released = (await client.get(f"/api/billing/time-entries/{entry['id']}")).json()
        assert released["status"] == "draft"
        assert released["invoice_id"] is None

        # Released time can be re-invoiced
        resp = await client.post(
            "/api/billing/invoices/generate",
            json={"matter_id": str(test_matter.id)},
        )
        assert resp.status_code == 201

    async def test_cannot_void_paid_invoice(self, client, test_matter):
        await _log_time(client, test_matter.id)
        inv = (
            await client.post(
                "/api/billing/invoices/generate",
                json={"matter_id": str(test_matter.id)},
            )
        ).json()
        await client.patch(
            f"/api/billing/invoices/{inv['id']}", json={"status": "sent"}
        )
        await client.post(
            "/api/billing/payments",
            json={
                "invoice_id": inv["id"],
                "amount": inv["total"],
                "payment_date": "2026-07-02",
            },
        )
        resp = await client.patch(
            f"/api/billing/invoices/{inv['id']}", json={"status": "void"}
        )
        assert resp.status_code == 400


class TestTimeEntryFilters:
    async def test_nonbillable_entry_records_zero_even_when_matter_has_a_rate(
        self, client, test_matter
    ):
        response = await client.post(
            "/api/billing/time-entries",
            json={
                "matter_id": str(test_matter.id),
                "description": "Internal team meeting",
                "hours": "0.5",
                "date": "2026-07-01",
                "is_billable": False,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["hourly_rate"] == "0.00"
        assert response.json()["amount"] == "0.00"

    async def test_date_filters_and_pagination_totals(self, client, test_matter):
        for day, hours in (("2026-06-01", "1.0"), ("2026-07-01", "2.0")):
            resp = await client.post(
                "/api/billing/time-entries",
                json={
                    "matter_id": str(test_matter.id),
                    "description": "Work",
                    "hours": hours,
                    "date": day,
                },
            )
            assert resp.status_code == 201

        july = await client.get(
            "/api/billing/time-entries", params={"date_from": "2026-07-01"}
        )
        data = july.json()
        assert data["total"] == 1
        assert Decimal(data["total_hours"]) == Decimal("2.0")

        # Totals cover the filtered set even when the page is smaller
        paged = await client.get("/api/billing/time-entries", params={"limit": 1})
        pdata = paged.json()
        assert len(pdata["items"]) == 1
        assert pdata["total"] == 2
        assert Decimal(pdata["total_hours"]) == Decimal("3.0")


class TestMatterExpenses:
    async def test_internal_expense_is_recorded_but_never_enters_prebill(
        self, client, test_matter
    ):
        created = await client.post(
            "/api/billing/expenses",
            json={
                "matter_id": str(test_matter.id),
                "description": "Team lunch to discuss case strategy",
                "amount": "74.50",
                "date": "2026-08-25",
                "category": "meals",
                "vendor": "Main Street Cafe",
                "reference_number": "RCPT-8821",
                # The server enforces the internal-only category even if a
                # caller mistakenly asks to pass it through to the client.
                "is_billable": True,
                "payment_method": "firm_card",
                "payment_account": "Firm Amex",
                "expense_account": "Meals and entertainment",
                "tax_amount": "5.25",
                "notes": "Internal strategy meeting; never show on client invoice.",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["is_billable"] is False
        assert body["review_status"] == "ready"
        assert body["reference_number"] == "RCPT-8821"
        assert body["qbo_payment_account_name"] == "Firm Amex"
        assert body["qbo_expense_account_name"] == "Meals and entertainment"

        preview = await client.get(
            "/api/billing/invoices/preview",
            params={"matter_id": str(test_matter.id)},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["expenses"] == []

        ledger = await client.get(
            "/api/billing/expenses", params={"matter_id": str(test_matter.id)}
        )
        assert ledger.status_code == 200, ledger.text
        assert ledger.json()["total"] == 1
        assert Decimal(ledger.json()["total_amount"]) == Decimal("74.50")

    async def test_client_amount_is_distinct_from_cost_and_drives_invoice(
        self, client, test_matter
    ):
        created = await client.post(
            "/api/billing/expenses",
            json={
                "matter_id": str(test_matter.id),
                "description": "Service of process",
                "amount": "100.00",
                "client_amount": "125.00",
                "date": "2026-08-25",
                "category": "process service",
                "vendor": "County Process LLC",
                "is_billable": True,
            },
        )
        assert created.status_code == 201, created.text
        expense = created.json()

        preview = await client.get(
            "/api/billing/invoices/preview",
            params={"matter_id": str(test_matter.id)},
        )
        assert preview.status_code == 200, preview.text
        row = preview.json()["expenses"][0]
        assert Decimal(row["cost_amount"]) == Decimal("100.00")
        assert Decimal(row["amount"]) == Decimal("125.00")
        assert Decimal(preview.json()["expense_amount"]) == Decimal("125.00")

        invoice = await client.post(
            "/api/billing/invoices/generate",
            json={
                "matter_id": str(test_matter.id),
                "time_entry_ids": [],
                "expense_ids": [expense["id"]],
            },
        )
        assert invoice.status_code == 201, invoice.text
        assert Decimal(invoice.json()["subtotal"]) == Decimal("125.00")
        assert Decimal(invoice.json()["line_items"][0]["amount"]) == Decimal(
            "125.00"
        )

    async def test_receipt_review_state_gates_prebill(self, client, test_matter):
        created = (
            await client.post(
                "/api/billing/expenses",
                json={
                    "matter_id": str(test_matter.id),
                    "description": "Court filing fee",
                    "amount": "85.00",
                    "date": "2026-08-25",
                    "category": "court filing",
                    "is_billable": True,
                },
            )
        ).json()
        pending = await client.patch(
            f"/api/billing/expenses/{created['id']}",
            json={"review_status": "needs_review"},
        )
        assert pending.status_code == 200, pending.text

        preview = await client.get(
            "/api/billing/invoices/preview",
            params={"matter_id": str(test_matter.id)},
        )
        assert preview.json()["expenses"] == []

        approved = await client.patch(
            f"/api/billing/expenses/{created['id']}",
            json={"review_status": "approved"},
        )
        assert approved.status_code == 200, approved.text
        preview = await client.get(
            "/api/billing/invoices/preview",
            params={"matter_id": str(test_matter.id)},
        )
        assert [item["id"] for item in preview.json()["expenses"]] == [created["id"]]


class TestBillingSettings:
    async def test_settings_roundtrip_and_timer_rounding(self, client, test_matter):
        # Defaults
        resp = await client.get("/api/billing/settings")
        assert resp.status_code == 200
        assert resp.json()["time_rounding_minutes"] == 6

        # Update to quarter-hour rounding
        put = await client.put(
            "/api/billing/settings",
            json={"time_rounding_minutes": 15, "default_hourly_rate": "300"},
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["time_rounding_minutes"] == 15
        assert Decimal(body["default_hourly_rate"]) == Decimal("300")

        # Timer now bills a minimum of 0.25h
        await client.post(
            "/api/billing/time-entries/timer/start",
            json={"matter_id": str(test_matter.id)},
        )
        stop = await client.post("/api/billing/time-entries/timer/stop", json={})
        assert Decimal(stop.json()["hours"]) == Decimal("0.25")
