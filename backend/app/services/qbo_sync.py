"""QBO sync service — bidirectional sync of legal billing data to QuickBooks Online.

Maps:
  - Matter → QBO Customer (client:matter naming convention)
  - TimeEntry → QBO TimeActivity (billable time, by service item)
  - Invoice → QBO Invoice (line items with custom fields for LEDES data)
  - Payment → QBO Payment (reconciliation back to matter balance)
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context

settings = get_settings()
logger = logging.getLogger(__name__)

QBO_API_BASE = "https://sandbox-quickbooks.api.intuit.com"
QBO_PROD_API_BASE = "https://quickbooks.api.intuit.com"


class QBOSyncService:
    """Sync legal billing entities to QuickBooks Online."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: str,
        access_token: str,
        sandbox: bool = True,
        ar_account_id: str | None = None,
        ar_account_name: str | None = None,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.access_token = access_token
        self.base_url = QBO_API_BASE if sandbox else QBO_PROD_API_BASE
        self.ar_account_id = ar_account_id
        self.ar_account_name = ar_account_name

    @staticmethod
    def _safe_qbo_string(value: str | None) -> str:
        """Escape a value for safe interpolation into a QBO query-language string.

        QBO's REST API has no parameterized-query support — filter values are
        interpolated directly into a SQL-like query string — so this is the
        only injection defense available. Escapes backslash first (so a
        trailing backslash can't consume the closing quote we add), then
        single quotes (QBO's string-literal escape, matching standard SQL),
        and strips control characters (newline/CR/NUL) that could otherwise
        be used to smuggle additional query clauses or malformed request
        bodies past this single-line string context.
        """
        if value is None:
            return ""
        value = value.replace("\\", "\\\\").replace("'", "''")
        return "".join(ch for ch in value if ch >= " " or ch == "\t")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _get_realm_id(self) -> str | None:
        """Get the QBO company/realm ID for this tenant."""
        from app.models.qbo import QBOIntegration

        await set_tenant_context(self.db, self.tenant_id)
        result = await self.db.execute(
            select(QBOIntegration).where(
                QBOIntegration.tenant_id == self.tenant_id,
                QBOIntegration.is_active,
            )
        )
        qbo = result.scalar_one_or_none()
        return qbo.qbo_realm_id if qbo else None

    def _api_url(self, realm_id: str, entity: str, entity_id: str | None = None) -> str:
        url = f"{self.base_url}/v3/company/{realm_id}/{entity}"
        if entity_id:
            url += f"/{entity_id}"
        return url

    async def _request(
        self,
        method: str,
        url: str,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> dict | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method, url, headers=self._headers, json=json_data, params=params
            )
            if resp.status_code in (200, 201):
                return resp.json()
            logger.warning(
                f"QBO {method} {url} → {resp.status_code}: {resp.text[:300]}"
            )
            return None

    # ── Customer Sync (Matter → QBO Customer) ───────────────────────────────

    async def _matter_display_name(self, matter) -> str:
        """QBO Customer DisplayName for a matter: "ClientName — MatterName"."""
        client_name = matter.counterparty
        if getattr(matter, "client_contact_id", None):
            from app.models.contact import Contact

            c_res = await self.db.execute(
                select(Contact).where(Contact.id == matter.client_contact_id)
            )
            c = c_res.scalar_one_or_none()
            if c:
                client_name = c.display_name

        display_name = f"{client_name} — {matter.matter_name}"
        if len(display_name) > 100:
            display_name = display_name[:97] + "..."
        return display_name

    async def _ensure_customer(self, realm_id: str, matter) -> dict | None:
        """Find or create the QBO Customer for a matter; returns the Customer dict.

        QBO transaction endpoints require CustomerRef.value (the Id) — a
        name-only reference is rejected — so anything that posts an invoice,
        payment, or time activity must resolve the Id through here first.
        """
        display_name = await self._matter_display_name(matter)
        safe_display = self._safe_qbo_string(display_name)

        url = self._api_url(realm_id, "query")
        query = f"SELECT * FROM Customer WHERE DisplayName = '{safe_display}'"
        existing = await self._request("GET", url, params={"query": query})
        if existing and existing.get("QueryResponse", {}).get("Customer"):
            return existing["QueryResponse"]["Customer"][0]

        customer_data = {
            "DisplayName": display_name,
            "CompanyName": (matter.counterparty or display_name)[:100],
            "Notes": (
                f"Matter: {matter.matter_name}\n"
                f"Type: {matter.matter_type}\n"
                f"Jurisdiction: {matter.jurisdiction}\n"
                f"Status: {matter.status}"
            ),
        }
        created = await self._request(
            "POST", self._api_url(realm_id, "customer"), json_data=customer_data
        )
        if created and created.get("Customer"):
            return created["Customer"]
        return None

    async def sync_customer(self, matter_id: str) -> dict | None:
        """Create or update a QBO Customer from a Matter."""
        from app.models.plugin import Matter

        await set_tenant_context(self.db, self.tenant_id)
        result = await self.db.execute(
            select(Matter).where(
                Matter.id == matter_id, Matter.tenant_id == self.tenant_id
            )
        )
        matter = result.scalar_one_or_none()
        if not matter:
            return None

        realm_id = await self._get_realm_id()
        if not realm_id:
            return None

        customer = await self._ensure_customer(realm_id, matter)
        if not customer:
            return None

        # Sparse-update the notes/company fields so QBO reflects matter changes
        sparse_data = {
            "Id": customer["Id"],
            "SyncToken": customer.get("SyncToken", "0"),
            "sparse": True,
            "Notes": (
                f"Matter: {matter.matter_name}\n"
                f"Type: {matter.matter_type}\n"
                f"Jurisdiction: {matter.jurisdiction}\n"
                f"Status: {matter.status}"
            ),
        }
        return await self._request(
            "POST", self._api_url(realm_id, "customer"), json_data=sparse_data
        )

    # ── TimeActivity Sync ───────────────────────────────────────────────────

    async def sync_time_entry(self, time_entry_id: str) -> dict | None:
        """Sync a TimeEntry to QBO TimeActivity.

        Idempotent: entries already carrying a qbo_timeactivity_id are
        skipped, so repeated full syncs no longer create duplicate
        TimeActivities in QBO.
        """
        from app.models.billing import TimeEntry
        from app.models.plugin import Matter

        await set_tenant_context(self.db, self.tenant_id)
        result = await self.db.execute(
            select(TimeEntry).where(
                TimeEntry.id == time_entry_id,
                TimeEntry.tenant_id == self.tenant_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry or not entry.is_billable:
            return None
        if entry.qbo_timeactivity_id:
            return {"TimeActivity": {"Id": entry.qbo_timeactivity_id}}
        if entry.timer_started_at is not None:
            return None  # Timer still running — nothing final to sync yet

        # Get the matter for this entry
        matter_result = await self.db.execute(
            select(Matter).where(Matter.id == entry.matter_id)
        )
        matter = matter_result.scalar_one_or_none()
        if not matter:
            return None

        realm_id = await self._get_realm_id()
        if not realm_id:
            return None

        customer = await self._ensure_customer(realm_id, matter)
        if not customer:
            return None

        # Find or create the Item (service) for billable time
        item_ref = await self._ensure_service_item(realm_id, "Legal Services")

        time_data = {
            "NameOf": "Employee",  # or "Vendor"
            "CustomerRef": {"value": customer["Id"]},
            "ItemRef": {"value": item_ref["Id"], "name": "Legal Services"},
            "HourlyRate": float(entry.hourly_rate),
            "Hours": float(entry.hours),
            "Description": entry.description[:4000],
            "BillableStatus": "Billable",
            "TxnDate": entry.date.isoformat(),
        }

        result = await self._request(
            "POST", self._api_url(realm_id, "timeactivity"), json_data=time_data
        )
        if result and result.get("TimeActivity", {}).get("Id"):
            entry.qbo_timeactivity_id = result["TimeActivity"]["Id"]
            await self.db.commit()
        return result

    async def _ensure_service_item(self, realm_id: str, item_name: str) -> dict:
        """Find or create a service-type Item in QBO."""
        safe_item = self._safe_qbo_string(item_name)
        url = self._api_url(realm_id, "query")
        query = f"SELECT * FROM Item WHERE Name = '{safe_item}' AND Type = 'Service'"
        existing = await self._request("GET", url, params={"query": query})

        if existing and existing.get("QueryResponse", {}).get("Item"):
            return existing["QueryResponse"]["Item"][0]

        item_data = {
            "Name": item_name,
            "Type": "Service",
            "UnitPrice": 0,
            "IncomeAccountRef": {"value": "1"},  # Will need account mapping
        }
        result = await self._request(
            "POST", self._api_url(realm_id, "item"), json_data=item_data
        )
        return result.get("Item", {}) if result else {"Id": "1"}

    # ── Invoice Sync ────────────────────────────────────────────────────────

    async def sync_invoice(self, invoice_id: str) -> dict | None:
        """Sync an Invoice to QBO Invoice."""
        from app.models.billing import Invoice, InvoiceLineItem, TimeEntry
        from app.models.plugin import Matter

        await set_tenant_context(self.db, self.tenant_id)
        result = await self.db.execute(
            select(Invoice).where(
                Invoice.id == invoice_id,
                Invoice.tenant_id == self.tenant_id,
            )
        )
        invoice = result.scalar_one_or_none()
        if not invoice:
            return None

        # Get matter
        matter_result = await self.db.execute(
            select(Matter).where(Matter.id == invoice.matter_id)
        )
        matter = matter_result.scalar_one_or_none()
        if not matter:
            return None

        realm_id = await self._get_realm_id()
        if not realm_id:
            return None

        # Get line items
        li_result = await self.db.execute(
            select(InvoiceLineItem)
            .where(InvoiceLineItem.invoice_id == invoice.id)
            .order_by(InvoiceLineItem.sort_order)
        )
        line_items = li_result.scalars().all()

        # Pre-load item mappings for this tenant
        from app.models.qbo import QBOItemMapping

        mapping_result = await self.db.execute(
            select(QBOItemMapping).where(QBOItemMapping.tenant_id == self.tenant_id)
        )
        item_mappings = {
            (m.source_type, m.expense_category): (m.qbo_item_id, m.qbo_item_name)
            for m in mapping_result.scalars().all()
        }

        # Resolve expense categories so expense lines can hit their
        # category-specific item mapping (filing_fee, travel, ...) instead of
        # only the generic expense fallback.
        from app.models.billing import Expense

        expense_ids = [
            li.source_id
            for li in line_items
            if li.source_type == "expense" and li.source_id
        ]
        expense_categories: dict = {}
        if expense_ids:
            cat_result = await self.db.execute(
                select(Expense.id, Expense.category).where(Expense.id.in_(expense_ids))
            )
            expense_categories = {row[0]: row[1] for row in cat_result.all()}

        def _item_ref(source_type: str, expense_category: str | None) -> dict | None:
            key = (source_type, expense_category)
            fallback_key = (source_type, None)
            legacy_category = {
                "court filing": "filing_fee",
                "travel/mileage/parking": "travel",
                "lodging": "travel",
                "postage/courier": "courier",
                "certified mail": "courier",
                "process service": "courier",
            }.get(expense_category or "")
            pair = (
                item_mappings.get(key)
                or (
                    item_mappings.get((source_type, legacy_category))
                    if legacy_category
                    else None
                )
                or item_mappings.get(fallback_key)
            )
            if pair:
                return {"value": pair[0], "name": pair[1]}
            return None

        qbo_lines = []
        for li in line_items:
            detail: dict = {
                "UnitPrice": float(li.unit_price),
                "Qty": float(li.quantity),
            }
            category = (
                expense_categories.get(li.source_id)
                if li.source_type == "expense"
                else None
            )
            item_ref = _item_ref(li.source_type, category)
            if item_ref:
                detail["ItemRef"] = item_ref

            qbo_lines.append(
                {
                    "Amount": float(li.amount),
                    "Description": li.description[:4000],
                    "DetailType": "SalesItemLineDetail",
                    "SalesItemLineDetail": detail,
                }
            )

        customer = await self._ensure_customer(realm_id, matter)
        if not customer:
            return None

        invoice_data = {
            "DocNumber": invoice.invoice_number[:21],  # QBO DocNumber limit
            "TxnDate": invoice.issue_date.isoformat(),
            "DueDate": invoice.due_date.isoformat(),
            "CustomerRef": {"value": customer["Id"]},
            "Line": qbo_lines,
            "PrivateNote": f"LawHand invoice {invoice.invoice_number}; matter {invoice.matter_id}",
        }
        if self.ar_account_id:
            invoice_data["ARAccountRef"] = {
                "value": self.ar_account_id,
                "name": self.ar_account_name or "",
            }
        if invoice.notes:
            invoice_data["CustomerMemo"] = {"value": invoice.notes[:1000]}
        if getattr(matter, "case_number", None):
            invoice_data["PONumber"] = matter.case_number[:15]

        # Include the client's billing contact data when LawHand has it.
        if getattr(matter, "client_contact_id", None):
            from app.models.contact import Contact

            contact_result = await self.db.execute(
                select(Contact).where(Contact.id == matter.client_contact_id)
            )
            contact = contact_result.scalar_one_or_none()
            if contact:
                if contact.email:
                    invoice_data["BillEmail"] = {"Address": contact.email}
                address = contact.address or {}
                bill_addr = {
                    key: value
                    for key, value in {
                        "Line1": address.get("street") or address.get("line1"),
                        "Line2": address.get("street2") or address.get("line2"),
                        "City": address.get("city"),
                        "CountrySubDivisionCode": address.get("state"),
                        "PostalCode": address.get("zip") or address.get("postal_code"),
                        "Country": address.get("country"),
                    }.items()
                    if value
                }
                if bill_addr:
                    invoice_data["BillAddr"] = bill_addr

        # Check if already synced
        if invoice.qbo_invoice_id:
            # QBO updates require the current SyncToken (optimistic locking)
            current = await self._request(
                "GET", self._api_url(realm_id, "invoice", invoice.qbo_invoice_id)
            )
            sync_token = "0"
            if current and current.get("Invoice"):
                sync_token = current["Invoice"].get("SyncToken", "0")
            invoice_data["Id"] = invoice.qbo_invoice_id
            invoice_data["SyncToken"] = sync_token
            invoice_data["sparse"] = True
            result = await self._request(
                "POST", self._api_url(realm_id, "invoice"), json_data=invoice_data
            )
        else:
            result = await self._request(
                "POST", self._api_url(realm_id, "invoice"), json_data=invoice_data
            )
        if result and result.get("Invoice"):
            qbo_invoice = result["Invoice"]
            invoice.qbo_invoice_id = qbo_invoice.get("Id") or invoice.qbo_invoice_id
            invoice.qbo_sync_token = qbo_invoice.get("SyncToken")
            invoice.qbo_sync_status = "synced"
            invoice.qbo_synced_at = datetime.now(timezone.utc)
            invoice.qbo_sync_error = None
            if getattr(invoice, "billed_at", None) is None:
                invoice.billed_at = invoice.qbo_synced_at
            # QBO creation is the accounting/billing event. It makes the
            # receivable outstanding locally, but does not claim it was emailed.
            if invoice.status == "draft":
                invoice.status = "sent"
            time_entry_ids = [
                line.source_id
                for line in line_items
                if line.source_type == "time_entry" and line.source_id
            ]
            if time_entry_ids:
                entries_result = await self.db.execute(
                    select(TimeEntry).where(TimeEntry.id.in_(time_entry_ids))
                )
                for entry in entries_result.scalars().all():
                    entry.status = "invoiced"
            await self.db.commit()

        return result

    # ── Payment Sync ────────────────────────────────────────────────────────

    async def sync_payment(self, payment_id: str) -> dict | None:
        """Sync a Payment to QBO Payment (receive payment against invoice)."""
        from app.models.billing import Payment, Invoice

        await set_tenant_context(self.db, self.tenant_id)
        result = await self.db.execute(
            select(Payment).where(
                Payment.id == payment_id,
                Payment.tenant_id == self.tenant_id,
            )
        )
        payment = result.scalar_one_or_none()
        if not payment:
            return None

        if payment.qbo_payment_id:
            return {"Payment": {"Id": payment.qbo_payment_id}}  # Already synced

        # Get the linked invoice
        inv_result = await self.db.execute(
            select(Invoice).where(Invoice.id == payment.invoice_id)
        )
        invoice = inv_result.scalar_one_or_none()
        if not invoice or not invoice.qbo_invoice_id:
            return None

        realm_id = await self._get_realm_id()
        if not realm_id:
            return None

        # QBO Payment requires CustomerRef — pull it from the synced invoice
        qbo_invoice = await self._request(
            "GET", self._api_url(realm_id, "invoice", invoice.qbo_invoice_id)
        )
        customer_ref = (
            qbo_invoice.get("Invoice", {}).get("CustomerRef") if qbo_invoice else None
        )
        if not customer_ref:
            return None

        payment_data = {
            "TotalAmt": float(payment.amount),
            "TxnDate": payment.payment_date.isoformat(),
            "CustomerRef": {"value": customer_ref["value"]},
            "PaymentMethodRef": {"value": self._map_payment_method(payment.method)},
            "Line": [
                {
                    "Amount": float(payment.amount),
                    "LinkedTxn": [
                        {
                            "TxnId": invoice.qbo_invoice_id,
                            "TxnType": "Invoice",
                        }
                    ],
                }
            ],
        }

        result = await self._request(
            "POST", self._api_url(realm_id, "payment"), json_data=payment_data
        )

        if result:
            payment.qbo_payment_id = result.get("Payment", {}).get("Id")
            await self.db.commit()

        return result

    @staticmethod
    def _map_payment_method(method: str) -> str:
        mapping = {
            "stripe": "2",  # Credit Card
            "check": "5",  # Check
            "wire": "6",  # Wire Transfer
            "cash": "1",  # Cash
            "other": "7",  # Other
        }
        return mapping.get(method, "7")

    # ── Retry Logic ───────────────────────────────────────────────────────────

    async def _retry_with_backoff(
        self, coro, operation_name: str, max_attempts: int = 3
    ):
        """Execute with exponential backoff retry. Logs failures to ErrorLog."""
        from app.services.error_tracker import capture_error

        last_exception = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await coro()
                if result is not None:
                    return result
                # QBO returned None (API error) — treat as retryable
                last_exception = Exception(
                    f"QBO API returned null for {operation_name}"
                )
            except Exception as exc:
                last_exception = exc

            if attempt < max_attempts:
                delay = 2**attempt  # 2s, 4s
                logger.warning(
                    f"QBO {operation_name} attempt {attempt}/{max_attempts} failed: {last_exception}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"QBO {operation_name} failed after {max_attempts} attempts: {last_exception}"
                )
                try:
                    await capture_error(
                        db=self.db,
                        error_type="qbo_sync_error",
                        severity="error",
                        message=f"QBO {operation_name} failed after {max_attempts} retries: {str(last_exception)[:500]}",
                        tenant_id=self.tenant_id,
                    )
                except Exception:
                    pass

        return None

    async def sync_invoice_with_retry(self, invoice_id: str) -> dict | None:
        """Sync invoice with retry and status lifecycle management."""
        from app.models.billing import Invoice

        # Set status to syncing
        await set_tenant_context(self.db, self.tenant_id)
        inv_result = await self.db.execute(
            select(Invoice).where(
                Invoice.id == invoice_id,
                Invoice.tenant_id == self.tenant_id,
            )
        )
        invoice = inv_result.scalar_one_or_none()
        if not invoice:
            return None

        invoice.qbo_sync_status = "syncing"
        invoice.qbo_sync_error = None
        await self.db.commit()

        result = await self._retry_with_backoff(
            lambda: self.sync_invoice(invoice_id),
            f"sync_invoice({invoice_id})",
        )

        # Update status based on result
        await set_tenant_context(self.db, self.tenant_id)
        inv_result = await self.db.execute(
            select(Invoice).where(Invoice.id == invoice_id)
        )
        invoice = inv_result.scalar_one_or_none()
        if invoice:
            if result:
                invoice.qbo_sync_status = "synced"
            else:
                invoice.qbo_sync_status = "failed"
                invoice.qbo_sync_error = (
                    "QuickBooks did not accept the invoice after 3 attempts."
                )
            await self.db.commit()

        return result

    async def sync_payment_with_retry(self, payment_id: str) -> dict | None:
        """Sync payment with retry."""
        return await self._retry_with_backoff(
            lambda: self.sync_payment(payment_id),
            f"sync_payment({payment_id})",
        )

    # ── Full Sync ───────────────────────────────────────────────────────────

    async def sync_all(self) -> dict:
        """Run a full sync of all pending entities. Returns sync summary."""
        summary = {
            "customers_synced": 0,
            "time_activities_synced": 0,
            "invoices_synced": 0,
            "payments_synced": 0,
            "errors": [],
        }
        realm_id = await self._get_realm_id()
        if not realm_id:
            summary["errors"].append("No QBO realm ID found")
            return summary

        # Sync unbilled time entries that haven't been pushed yet — the
        # qbo_timeactivity_id filter keeps repeat runs from duplicating
        # TimeActivities in QBO.
        from app.models.billing import Invoice, Payment, TimeEntry

        await set_tenant_context(self.db, self.tenant_id)
        entries_result = await self.db.execute(
            select(TimeEntry).where(
                TimeEntry.tenant_id == self.tenant_id,
                TimeEntry.is_billable.is_(True),
                TimeEntry.invoice_id.is_(None),
                TimeEntry.status == "draft",
                TimeEntry.qbo_timeactivity_id.is_(None),
            )
        )
        for entry in entries_result.scalars().all():
            try:
                result = await self.sync_time_entry(str(entry.id))
                if result:
                    summary["time_activities_synced"] += 1
            except Exception as exc:
                summary["errors"].append(f"TimeEntry {entry.id}: {exc}")

        # Sync unsynced invoices
        inv_result = await self.db.execute(
            select(Invoice).where(
                Invoice.tenant_id == self.tenant_id,
                Invoice.qbo_sync_status != "synced",
                Invoice.status.in_(["sent", "paid", "partially_paid"]),
            )
        )
        for inv in inv_result.scalars().all():
            try:
                result = await self.sync_invoice(str(inv.id))
                if result:
                    summary["invoices_synced"] += 1
                else:
                    summary["errors"].append(f"Invoice {inv.id}: sync returned null")
            except Exception as exc:
                summary["errors"].append(f"Invoice {inv.id}: {exc}")

        # Sync payments not yet pushed, for invoices that exist in QBO
        pay_result = await self.db.execute(
            select(Payment)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                Payment.tenant_id == self.tenant_id,
                Payment.qbo_payment_id.is_(None),
                Invoice.qbo_invoice_id.is_not(None),
            )
        )
        for payment in pay_result.scalars().all():
            try:
                result = await self.sync_payment(str(payment.id))
                if result:
                    summary["payments_synced"] += 1
            except Exception as exc:
                summary["errors"].append(f"Payment {payment.id}: {exc}")

        return summary
