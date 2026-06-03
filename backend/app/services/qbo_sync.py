"""QBO sync service — bidirectional sync of legal billing data to QuickBooks Online.

Maps:
  - Matter → QBO Customer (client:matter naming convention)
  - TimeEntry → QBO TimeActivity (billable time, by service item)
  - Invoice → QBO Invoice (line items with custom fields for LEDES data)
  - Payment → QBO Payment (reconciliation back to matter balance)
"""

import logging

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
        self, db: AsyncSession, tenant_id: str, access_token: str, sandbox: bool = True
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.access_token = access_token
        self.base_url = QBO_API_BASE if sandbox else QBO_PROD_API_BASE

    @staticmethod
    def _safe_qbo_string(value: str | None) -> str:
        """Escape single quotes for safe QBO query interpolation."""
        if value is None:
            return ""
        return value.replace("'", "''")

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

    async def sync_customer(self, matter_id: str) -> dict | None:
        """Create or update a QBO Customer from a Matter.

        Naming convention: "ClientName — MatterName (CaseRef)"
        """
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

        # Prefer linked Contact name over counterparty string
        client_name = matter.counterparty
        if matter.client_contact_id:
            from app.models.contact import Contact
            from sqlalchemy import select as _select
            c_res = await self.db.execute(
                _select(Contact).where(Contact.id == matter.client_contact_id)
            )
            c = c_res.scalar_one_or_none()
            if c:
                client_name = c.display_name

        display_name = f"{client_name} — {matter.matter_name}"
        if len(display_name) > 100:
            display_name = display_name[:97] + "..."

        safe_display = self._safe_qbo_string(display_name)

        customer_data = {
            "DisplayName": display_name,
            "GivenName": client_name,
            "CompanyName": client_name,
            "Notes": (
                f"Matter: {matter.matter_name}\n"
                f"Type: {matter.matter_type}\n"
                f"Jurisdiction: {matter.jurisdiction}\n"
                f"Status: {matter.status}"
            ),
        }

        # Check if customer already exists (by DisplayName)
        url = self._api_url(realm_id, "query")
        query = f"SELECT * FROM Customer WHERE DisplayName = '{safe_display}'"
        existing = await self._request("GET", url, params={"query": query})

        if existing and existing.get("QueryResponse", {}).get("Customer"):
            qbo_customer = existing["QueryResponse"]["Customer"][0]
            customer_id = qbo_customer["Id"]
            # Update (sparse — only set changed fields)
            sparse_data = {
                "Id": customer_id,
                "sparse": True,
                **customer_data,
            }
            return await self._request(
                "POST", self._api_url(realm_id, "customer"), json_data=sparse_data
            )

        # Create new
        return await self._request(
            "POST", self._api_url(realm_id, "customer"), json_data=customer_data
        )

    # ── TimeActivity Sync ───────────────────────────────────────────────────

    async def sync_time_entry(self, time_entry_id: str) -> dict | None:
        """Sync a TimeEntry to QBO TimeActivity."""
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

        # Find or create the Item (service) for billable time
        item_ref = await self._ensure_service_item(realm_id, "Legal Services")

        time_data = {
            "NameOf": "Employee",  # or "Vendor"
            "CustomerRef": {
                "name": self._safe_qbo_string(
                    f"{matter.counterparty} — {matter.matter_name}"
                )
            },
            "ItemRef": {"value": item_ref["Id"], "name": "Legal Services"},
            "HourlyRate": float(entry.hourly_rate),
            "Hours": float(entry.hours),
            "Description": entry.description[:4000],
            "BillableStatus": "Billable",
            "TxnDate": entry.date.isoformat(),
        }

        return await self._request(
            "POST", self._api_url(realm_id, "timeactivity"), json_data=time_data
        )

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
        from app.models.billing import Invoice, InvoiceLineItem
        from app.models.plugin import Matter

        await set_tenant_context(self.db, self.tenant_id)
        result = await self.db.execute(
            select(Invoice).where(
                Invoice.id == invoice_id,
                Invoice.tenant_id == self.tenant_id,
            )
        )
        invoice = result.scalar_one_or_none()
        if not invoice or invoice.status == "draft":
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

        qbo_lines = []
        for li in line_items:
            qbo_lines.append(
                {
                    "Amount": float(li.amount),
                    "Description": li.description[:4000],
                    "DetailType": "SalesItemLineDetail",
                    "SalesItemLineDetail": {
                        "UnitPrice": float(li.unit_price),
                        "Qty": float(li.quantity),
                    },
                }
            )

        customer_name = f"{matter.counterparty} — {matter.matter_name}"
        if len(customer_name) > 100:
            customer_name = customer_name[:97] + "..."

        safe_customer = self._safe_qbo_string(customer_name)

        invoice_data = {
            "DocNumber": invoice.invoice_number,
            "TxnDate": invoice.issue_date.isoformat(),
            "DueDate": invoice.due_date.isoformat(),
            "CustomerRef": {"name": safe_customer},
            "Line": qbo_lines,
            "PrivateNote": invoice.notes or "",
            "TotalAmt": float(invoice.total),
        }

        # Check if already synced
        if invoice.qbo_invoice_id:
            invoice_data["Id"] = invoice.qbo_invoice_id
            invoice_data["sparse"] = True
            result = await self._request(
                "POST", self._api_url(realm_id, "invoice"), json_data=invoice_data
            )
        else:
            result = await self._request(
                "POST", self._api_url(realm_id, "invoice"), json_data=invoice_data
            )
            if result:
                invoice.qbo_invoice_id = result.get("Invoice", {}).get("Id")
                invoice.qbo_sync_status = "synced"
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

        payment_data = {
            "TotalAmt": float(payment.amount),
            "TxnDate": payment.payment_date.isoformat(),
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

        # Sync unbilled time entries
        from app.models.billing import TimeEntry

        await set_tenant_context(self.db, self.tenant_id)
        entries_result = await self.db.execute(
            select(TimeEntry).where(
                TimeEntry.tenant_id == self.tenant_id,
                TimeEntry.is_billable == True,
                TimeEntry.invoice_id.is_(None),
                TimeEntry.status == "draft",
            )
        )
        for entry in entries_result.scalars().all():
            try:
                await self.sync_time_entry(str(entry.id))
                summary["time_activities_synced"] += 1
            except Exception as exc:
                summary["errors"].append(f"TimeEntry {entry.id}: {exc}")

        # Sync unsynced invoices
        from app.models.billing import Invoice

        inv_result = await self.db.execute(
            select(Invoice).where(
                Invoice.tenant_id == self.tenant_id,
                Invoice.qbo_sync_status != "synced",
                Invoice.status.in_(["sent", "paid", "partially_paid"]),
            )
        )
        for inv in inv_result.scalars().all():
            try:
                await self.sync_invoice(str(inv.id))
                summary["invoices_synced"] += 1
            except Exception as exc:
                summary["errors"].append(f"Invoice {inv.id}: {exc}")

        return summary
