---
slug: quickbooks-administration
title: QuickBooks administration
description: Connect the intended company, control mappings, synchronize invoices, and reconcile every transfer.
order: 150
read_time: 8 min
icon: chart
---

# QuickBooks administration

[Integrations → QuickBooks](/admin?tab=integrations&integration=quickbooks) connects LawHand billing data to an approved QuickBooks Online company. The integration supports workflow and transfer; QuickBooks remains an accounting system that requires reconciliation.

## Permission boundary and current use

Intuit grants the broad `com.intuit.quickbooks.accounting` permission for the selected company, plus identity scopes used during connection. That provider grant is not technically limited to invoices. LawHand's current implementation uses it to read active service items, matching customers, and existing synchronized invoice state, and to write configured customers, time activity, invoices, and payments.

The dominant transfer direction is LawHand to QuickBooks. Supporting reads are still necessary for mapping, matching, and safe updates. See [Integration permissions and data visibility](/guide/integration-data-visibility) for the field-level disclosure.

## Connect the company

Use an Intuit administrator authorized for the intended company. During consent, verify the company name and realm. After returning to LawHand, confirm the displayed company before configuring or synchronizing anything.

Do not connect a sandbox, former company, accountant test file, or similarly named entity to a production tenant.

## Field mapping

Review customer, matter, service item, account, tax, class, payment, and other mappings exposed by the panel. Document the accounting owner's approved choices. Use stable identifiers where supported rather than names that may change.

The implemented object mapping includes:

| LawHand record | QuickBooks destination | Data currently sent |
| --- | --- | --- |
| Client and matter | Customer | Client/matter display name, company or counterparty context, and notes with matter name, type, jurisdiction, and status |
| Final billable time | TimeActivity | Customer, service item, date, duration, rate, description, and billable status |
| Non-draft invoice | Invoice | Invoice number, issue/due dates, customer, service-item lines, descriptions, quantity, rate, amount, and private notes |
| Payment | Payment | Customer, amount, date, payment method, and linked invoice |

Test edge cases such as a new client, multiple matters for one client, discounts, taxes, trust-related activity, voids, and an already-synchronized invoice.

## Synchronize invoices

Start with a small approved batch. Compare LawHand invoice number, client, dates, line descriptions, quantities, rates, adjustments, taxes, totals, status, and resulting QuickBooks identifiers.

Prevent duplicate transfers by respecting synchronization state. Do not retry an ambiguous timeout until you check whether QuickBooks created the record.

## Reconcile and recover

Reconcile every initial or exceptional sync in QuickBooks. For mapping or validation failures, correct the source or mapping and retry only the affected record. Do not create manual offsetting entries merely to hide an integration error.

Before disconnecting or reconnecting, preserve mapping and sync evidence and understand how existing external IDs will be treated. Suspected transfer to the wrong company requires immediate containment and accounting/security escalation.

## User notice, retention, and revocation

Tell billing users which LawHand records are eligible for export, which status makes them eligible, who can initiate a sync, and whether QuickBooks or LawHand is authoritative for corrections. Make clear that matter descriptions and notes can contain client context and should be reviewed before export.

LawHand retains encrypted connection credentials, the QuickBooks realm/company identifier, mappings, provider object identifiers, synchronization state, and error/history information. Disconnecting or revoking the Intuit grant stops future successful API calls but does not remove transactions from QuickBooks or erase LawHand billing records and sync history.

Before production use, verify the company realm, service-item mappings, a known customer, one time entry, one invoice, one payment, update behavior, duplicate prevention, and the accounting owner's reconciliation sign-off.
