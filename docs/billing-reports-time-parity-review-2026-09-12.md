# Billing surfaces UX and competitor-parity review — 2026-09-12

A source read of the three finance surfaces a firm uses every day: **Time
Tracking**, **Invoices**, and **Reports**. The bar is what a small or midsize
firm switching from Clio Manage, MyCase, PracticePanther, Smokeball, or CosmoLex
takes for granted. This is the billing counterpart to
[`client-portal-ux-review-2026-09-11.md`](client-portal-ux-review-2026-09-11.md).

Reviewed at `947e192` on `claude/competitor-parity-reports-invoices-ssaonv`.
Source read, not a live walk; every finding cites the file and line that
produces it. Findings marked *unverified* were not proven in code.

**Remediation status.** The review itself changed no product code. The commits
that follow it on this branch fix every Blocking finding and most Majors; see
"What has been fixed" at the end for the list, and the CHANGELOG entry for the
detail. Line and file citations below describe the code *as reviewed*, so they
are the record of what was wrong, not of the current tree.

`BillingPage.jsx` and `BillingStatusBanner.jsx` are LawHand's own subscription
billing (tenant plan, Stripe checkout) and are out of scope here.

## Surfaces

| Route | Page | Backend | Who sees it |
| --- | --- | --- | --- |
| `/time-tracking` | `TimeTrackingPage.jsx` | `billing_extended.py` time-entry and timer routes | Paralegal, Attorney, Finance, Partner presets (`navigation.js:58-61`) |
| `/invoices`, `/invoices/:id` | `InvoicesPage.jsx`, `InvoiceDetailPage.jsx` | `billing_extended.py` invoice, payment, export, Stripe routes | Finance, Partner |
| `/reports` | `ReportsPage.jsx` | `reports.py` | Finance, Partner |
| Matter detail, Billing tab | `MatterDetailPage.jsx:1628-1708` | `matters.py` time-entry route | Anyone with matter access |

## Verdict

The backend is further along than the UI lets a user discover. Rate resolution,
increment rounding, source locking on invoice generation, sequential numbering,
payment state math, tenant scoping, Stripe payment links, QuickBooks sync, and an
A/R aging report all exist, are `Decimal`-safe, and are covered by tests. That is
a solid foundation and none of it needs to be redone.

What a switching firm will hit in the first week is the *bill review loop*.
A timer stops and lands as "Timer session" with no narrative. The entry cannot
then be edited without inflating its hours. The generated invoice cannot have a
line edited, discounted, or written down. It cannot be emailed. Trust money
cannot be applied to it. Nothing reminds the client. Every competitor on the list
does all of these, and firms will not move a book of business onto a system that
cannot.

Reports is the thinnest of the three surfaces and is covered in its own section.

Cross-cutting: billing settings (default rate, rounding increment) have API
functions in `api.js` and an admin-only backend but no screen anywhere; the
matter's Billing tab shows time and expenses but not invoices, balance due, or
trust balance; none of the end-to-end suites under `frontend/e2e/` touch any of
the three pages.

---

## Part 1 — Time Tracking

### What exists

- One running timer per user, started from the page header, with an elapsed
  clock, Stop & log, and Discard (`TimeTrackingPage.jsx:126-130, 361-371,
  397-433`). The timer is server-side and survives reload (`:114`).
- Stop rounds **up** to the tenant increment with a one-increment minimum,
  default 6 minutes (`services/billing_workflow.py:41-55`).
- Manual entry form: matter, description, hours, date, rate, billable toggle
  (`:435-494`). Edit and delete are hidden once an entry is invoiced (`:566,
  :613`) and the backend enforces it (`billing_extended.py:703-707, 750-754`).
- Rate resolution: explicit, then matter override, then user default, then
  tenant default (`billing_extended.py:134-147`). Non-billable forces amount to
  zero (`:369-370`).
- Filters for status, matter, and date range (`:105-109, 479-491`); the backend
  additionally supports `user_id`, `billable_only`, `unbilled_only`, and
  pagination with server-side totals (`:402-490`).
- Tenant billing settings endpoint for default rate and rounding minutes
  (`:1746-1798`), admin-only, tested.
- `utbms_task_code` and `utbms_activity_code` columns and schema fields
  (`models/billing.py:66-67`, `schemas/billing.py:21-22`).
- Row-level security enabled and forced on `time_entries`
  (`migrations/versions/015_create_billing_tables.py:217-222`).
- Per-matter read-only time table with totals (`MatterDetailPage.jsx:1655-1700`).

### Findings

Severity is from the timekeeper's point of view.

#### Blocking

**T1. A stopped timer has no narrative.** Start sends the description or the
literal `'Timer session'` (`TimeTrackingPage.jsx:147`), but the header Start
button is usable while the entry form is collapsed, so the description is almost
always empty. Stop sends `stopTimer({})` (`:165`) even though the backend accepts
a description at stop (`schemas/billing.py:84-85`, applied at
`billing_extended.py:618-619`). Every timer entry therefore needs a second edit
pass before it can be billed. Competitors prompt for the narrative at stop.
*Fix:* description and matter fields on the running-timer banner; Stop opens a
one-step confirm that sends the description.

**T2. Timer entries cannot be edited without changing their hours.** The
backend produces 0.10-hour entries (`billing_workflow.py:55`, asserted in
`tests/test_billing_timer_invoices.py:66`) but both forms reject anything under
0.25 (`TimeTrackingPage.jsx:207, 257`) and the inputs use `min="0.25"
step="0.25"` (`:438-439, 511`). Fixing the narrative from T1 on a 6-minute entry
forces the user to bill 15 minutes. That is a billing-integrity problem, not a
nit. *Fix:* validate `hours > 0`, and drive `step` from the tenant's
`time_rounding_minutes`.

**T3. The default date is UTC.** `new Date().toISOString().slice(0, 10)`
(`:87, :236`) and the server stamps timer entries with `now.date()` in UTC
(`billing_extended.py:534, 546`). A timekeeper working after 5pm Pacific sees
tomorrow's date pre-filled, and timer entries carry the wrong service date onto
the invoice. *Fix:* build the default from local date parts and let the timer
routes accept a client-supplied date.

#### Major

**T4. No global timer indicator.** The running clock is visible only on
`/time-tracking`; `AppShell.jsx` has no timer widget and the matter page has no
Start action. Clio, PracticePanther, and Smokeball keep a running-timer chip in
the header precisely so people do not forget timers. *Fix:* a header chip that
polls the active timer with Stop and Discard, plus Start on the matter header.

**T5. Rounding increment and default rate have no screen.** `getBillingSettings`
and `updateBillingSettings` exist (`api.js:2622-2625`) and nothing renders them.
The firm cannot choose 6 versus 15 minutes or set a house rate. *Fix:* a Billing
card on the Admin page.

**T6. UTBMS codes cannot be entered and the LEDES export ignores them.** No
frontend reference to either field. `services/ledes_export.py:188-189` hard-codes
`L220` and `A113` on every line. LEDES is advertised on the invoice page
(`InvoiceDetailPage.jsx:637`) and insurance or corporate clients will reject the
file. *Fix:* optional task and activity selects on the entry form; read the
entry's codes in the export with defaults as fallback.

**T7. Timekeeper identity is missing.** The list returns every user's entries
(`billing_extended.py:413`) with no user column and no user filter in the UI
(`:590`, `:105-109`). Edit and Delete render on every non-invoiced row (`:566,
:613`) but the backend returns 403 for non-owners without `manage_billing`
(`:696-697, 748-749`), so a paralegal sees buttons that fail. The matter page
renders `e.user_name` (`MatterDetailPage.jsx:1690`) but the endpoint never
returns it (`matters.py:2330-2342`), so the column is always a dash. *Fix:* add
`user_name` to both responses, add a User column and a Mine/All toggle, hide
the actions the user cannot take.

**T8. Totals come from the first 500 rows.** The UI never paginates (default
limit 500, `billing_extended.py:439`) and sums the loaded rows client-side
(`TimeTrackingPage.jsx:300-304`), discarding the server's `total_hours` and
`total_amount` (`:116`). Past 500 entries in range the metric strip silently
understates. *Fix:* use the server totals and add paging or "showing N of M".

**T9. No write-off or no-charge path, but the error message promises one.**
`"Cannot delete a billed time entry. Write it off instead."`
(`billing_extended.py:752-753`) points at a feature that does not exist for time
entries; status edits are rejected outright (`:699-702`). *Fix:* a no-charge
flag excluded from pre-bill and shown struck through, or correct the message.

**T10. No bulk actions, approval step, weekly timesheet, duration parsing, or
keyboard path.** Single-entry forms only; no selection column; no key handlers.
Hours is `type="number"` (`:437`) so `1:30` and `90m` are rejected. The matter
page styles an `approved` status (`MatterDetailPage.jsx:1694`) that no code
path produces. These are the day-to-day speed features Smokeball and Clio users
notice first. *Fix:* row selection with bulk billable toggle and delete; a
`parseDuration()` accepting `1.5`, `1:30`, `90m`; a week view grouped by day.

#### Minor

- **Explicit rate silently beats the matter rate.** The rate field pre-fills
  the user default (`:86`) and is sent whenever non-empty (`:150`), so the
  resolver's explicit tier (`billing_extended.py:141`) overrides the matter
  override set at `MatterDetailPage.jsx:2013, 2165`. Leave it blank by default
  and show the resolved rate.
- **Matter picker is a 200-row select** including closed matters (`:113`).
- **Matters and the active timer refetch on every filter change** (`:99-121`).
- **Elapsed clock uses client time against server start** (`:40-46`); billed
  hours are server-computed so money is right, only the display can drift.
- **Status filter cannot show running or non-billable entries** (`:29-33`);
  backend flags exist (`:434-437`).
- **Expenses are not reachable from this page**; only from the matter tab.
- **Detail routes filter by tenant but skip `set_tenant_context`**
  (`:660-662, 683-685, 742-744`) unlike create and list (`:359, 451, 511`).
  Isolation holds via the explicit filter; whether forced RLS fails closed for
  these depends on the DB role. *Unverified.*
- **Focus does not move to the edit form** when it opens (`:499`).
- No AI narrative help, calendar or email auto-capture, description templates,
  copy-previous, daily target, or utilization. Nothing in the codebase.

### Parity table

| Capability | Expectation | LawHand | Citation |
| --- | --- | --- | --- |
| Running timer | Yes | Have | `billing_extended.py:498-647` |
| Multiple or paused timers | Clio, PracticePanther | Missing | `:513-524` returns 409 |
| Header timer chip | Yes | Missing | `AppShell.jsx` |
| Narrative at stop | Yes | Partial | `TimeTrackingPage.jsx:165` |
| Duration parsing (`1:30`, `90m`) | Yes | Missing | `:437` |
| Configurable increment | Yes | Partial, backend only | `:1746-1798`, no UI |
| Manual minimum matches increment | Yes | Missing | `:207` vs `billing_workflow.py:55` |
| Billable toggle | Yes | Have | `:471-474` |
| UTBMS codes | Yes | Missing in UI | `ledes_export.py:188-189` |
| Rate tiers user / matter / firm | Yes | Have | `billing_extended.py:134-147` |
| Client-level rate, flat fee on entry | Yes | Missing | resolver has no client tier |
| Lock after invoicing | Yes | Have | `:703-707` |
| Write-down / no-charge | Yes | Missing | `:752-753` |
| Approval before billing | Clio, CosmoLex | Missing | no state |
| Bulk actions | Yes | Missing | `:588-634` |
| Timekeeper filter and column | Yes | Missing in UI | `:434-437` |
| Pagination, server totals | Yes | Missing | `:300-304` |
| Weekly timesheet, daily target | Smokeball, Clio | Missing | — |
| Local-date default | Yes | Missing | `:87` |
| QuickBooks time sync | Clio, CosmoLex | Have | `qbo_sync.py:202-263` |
| Loading / empty / error states | Yes | Have | `:519-547` |

---

## Part 2 — Invoices

### What exists

- Invoice list with status filter, metric strip, mobile cards and desktop table,
  QuickBooks sync indicator (`InvoicesPage.jsx:28-35, 95-114, 235-246,
  397-506`).
- Pre-bill inside Generate: matter, work date range, terms, due days, tax, note,
  and a live preview of unbilled time and expenses with per-row include
  checkboxes and firm-cost versus client-amount (`:120-153, 248-385`). Defaults
  per matter and client (`:137-146`, `billing_extended.py:229-257`).
- Generation locks sources with `SELECT ... FOR UPDATE`, rejects stale
  selections with 409, quantizes tax to cents, and numbers sequentially per
  tenant with an IntegrityError retry (`:990-1149`, `billing_workflow.py:58-73`).
- Detail page: Mark as sent, draft metadata edit, record payment with an
  overpayment guard, Stripe payment link, print PDF, CSV, LEDES, QuickBooks sync,
  Void (`InvoiceDetailPage.jsx:150-338, 379-405, 418-503, 511-653`).
- Status transitions enforced server-side; `paid` requires payments to cover
  the total; void refuses when any payment exists and releases sources
  (`billing_extended.py:1363-1445`, `billing_workflow.py:17-31`). Overdue is
  derived at read time (`:14-16, 34-38`).
- Stripe webhook with idempotency and ordering guard (`:1805-2143`).
- Branded PDF with logo, address, contact, footer (`services/invoice_pdf.py`).
- Client portal lists sent invoices, pays by Stripe Checkout, and downloads an
  audited PDF (`client_portal.py:2315-2530`, `ClientPortalMatterPage.jsx:1359-1465`).
- Money is `Numeric`/`Decimal` throughout the invoice routes; tenant scoping is
  present on every invoice route checked; the detail page reloads after every
  mutation. Behaviours above are pinned by `tests/test_billing_timer_invoices.py`.

### Findings

Severity is from the billing attorney's point of view.

#### Blocking

**I1. Line items are frozen at generation.** `InvoiceUpdate` accepts only dates,
notes, terms, and status (`schemas/billing.py:290-295`). No route touches line
items; `InvoiceCreate` and `InvoiceLineItemCreate` (`:251-287`) are referenced
by nothing. The detail page renders lines read-only (`InvoiceDetailPage.jsx:428-449`)
while its label map advertises `flat_fee`, `adjustment`, and `discount` types
(`:79-87`) that cannot be created. Tax rate is also frozen. The only remedy for
a wrong line is void, fix the time entry, regenerate under a new number. The
"generate, adjust narratives and hours, discount, approve" loop is the heart of
every competitor's billing. *Fix:* draft-only line-item routes for edit, add
(`flat_fee`, `adjustment`, negative `discount`), delete, reorder, with totals
recomputed server-side; inline editable rows with Discount and Write down.

**I2. Invoices cannot be sent.** The Mark as sent dialog says so:
"It does not send an email automatically" (`InvoiceDetailPage.jsx:161`). There
is no send route, no template, no batch send, no reminder job
(`services/scheduler.py` has task and e-sign reminders only), and no viewed
status. `Contact.billing_delivery_method` is loaded into the defaults
(`billing_extended.py:253`) and never acted on. Portal downloads are audited
(`client_portal.py:2500-2530`) but staff never see them. A firm cannot run
collections from LawHand. *Fix:* `POST /invoices/{id}/send` with PDF and portal
link on a templated email; bulk send from the list; a reminder schedule in
billing settings; portal downloads surfaced as Viewed on the detail timeline.

**I3. Recurring billing is unwired and would collide on numbers.**
`generate_recurring_invoices` is called by nothing outside its own module. Its
numbering restarts at 1 on each run (`recurring_billing.py:163-165`) and uses a
5-digit format (`:259`) against the manual path's 4-digit one, so a second run
violates `uq_invoices_tenant_number` (`models/billing.py:246-248`); the per-matter
`except` (`:250-252`) then leaves the session in a failed transaction for the
rest of the tenant. *Fix:* reuse `next_invoice_number`, wrap each matter in a
savepoint, register the job in the scheduler, or remove the module until it is
real.

#### Major

**I4. Trust and retainer money never reaches an invoice.** The payment form
offers a `retainer` method (`InvoiceDetailPage.jsx:461`) but `create_payment`
writes a `Payment` only (`billing_extended.py:1479-1487`). Conversely
`drawdown_retainer` decrements the retainer and logs a transaction with an
`invoice_id` but creates no `Payment` (`matters.py:2215-2290`). The trust router
has no invoice references. `Retainer.minimum_balance` exists
(`models/retainer.py:56`) and feeds only template merge fields. A firm can post a
"retainer" payment while the retainer balance stays untouched, or the reverse.
Applying trust to a bill is the defining legal-billing feature. *Fix:* one
transaction that draws down and records the payment with method `trust`;
"Apply from trust, available $X" on the detail page; an evergreen replenishment
request when balance drops below minimum.

**I5. A wrong partially-paid invoice is stuck.** Void requires zero payments
(`InvoiceDetailPage.jsx:342`, `billing_extended.py:1411-1419`). `written_off` is
a legal transition (`billing_workflow.py:20`) but no UI sets it. There is no
credit note, refund, or delete anywhere. *Fix:* Write off balance with a reason,
Issue credit, refund through Stripe for `stripe_payment_intent_id`, and void of
a paid invoice that moves payments to client credit.

**I6. The PDF is not a legal bill yet.** No Bill To block; the info grid is
number, dates, terms, status, matter, amount due (`invoice_pdf.py:184-204`).
Lines have no date or timekeeper because `InvoiceLineItem` stores neither
(`models/billing.py:343-370`) and the generated description is
`"{description} ({hours}h @ ${rate}/hr)"` (`billing_extended.py:1033`). The
footer is the literal `"Page 1 of 1"` (`:449`). One layout, no column options.
Most clients and every insurer will reject a bill without client address, entry
dates, and timekeeper. *Fix:* persist date, user, and UTBMS codes on line items
at generation; Bill To from the matter's client; a real page-number callback;
two or three selectable templates.

**I7. LEDES 1998B would be rejected by any e-billing vendor.** Client ID is the
tenant UUID prefix, matter ID is the internal UUID (`ledes_export.py:180-181`),
every line is `L220`/`A113` (`:187-188`), timekeeper is `LAWYER01 / Attorney /
PARTNER` (`:205-207`), line date is the issue date (`:194`), expenses use unit
`H` (`:154`), and `ledes_exported_at` is never written. *Fix:* depends on I6;
add LEDES client and matter ID fields on Contact and Matter; `E` unit with
`E1xx` codes for expenses.

**I8. The list cannot be searched, sorted, paged, or bulk-acted on.**
`list_invoices` supports matter, status, and overdue only and returns every row
(`billing_extended.py:1229-1264`); no search box or sortable header
(`InvoicesPage.jsx:388-395, 462-471`); no row selection; no void or written-off
filter (`:28-35`); the matter select caps at 200 (`:104`), so a firm past 200
matters cannot invoice the rest from this page. *Fix:* `q`, `client_id`, issue
date range, `sort`, `page` on the route; a typeahead matter picker; bulk PDF,
Send, Approve.

**I9. No bulk generation, flat fee, or contingency invoicing.** Generate takes
one `matter_id` (`schemas/billing.py:348-362`) and ingests only time and expenses
(`billing_extended.py:1024-1058`). `Matter.billing_method` and
`contingency_percentage` (`models/plugin.py:249-255`) have no invoicing consumer.
*Fix:* generate-batch over matters with unbilled work through a date into a
review queue; a flat-fee line at generation for flat-fee matters.

**I10. No interest, late fees, payment plans, split billing, statements, or
numbering settings.** Numbering is fixed `INV-{year}-{seq:04d}`
(`billing_workflow.py:65-73`); billing settings hold only rate and rounding
(`schemas/billing.py:426-428`); one payor per invoice. Firms migrating expect
their sequence to continue. *Fix:* prefix, next sequence, interest rate, grace
days, reminder schedule in settings; a per-client statement PDF.

**I11. Single-invoice QuickBooks sync promotes a draft to sent.**
`qbo_sync.py:483-488` sets `billed_at` and flips `draft` to `sent`; the detail
page enables Sync on drafts and toasts "marked billed" (`InvoiceDetailPage.jsx:264,
630`), while bulk sync deliberately excludes drafts (`qbo_sync.py:724`). One
click turns an unreviewed draft into outstanding A/R in both systems. *Fix:*
disable Sync on drafts or reuse the Mark as sent confirm.

**I12. Endpoints with no screen.** `getMatterInvoices`, `getBillingSettings`,
`updateBillingSettings` (`api.js:2564-2565, 2622-2625`) are unused, so the matter
Billing tab cannot show its bills. `GET /matters/{id}/invoices` serialises money
as `float` (`matters.py:2385-2412`), the one place in billing that does. *Fix:*
Invoices panel on the matter tab; `str(Decimal)` on the route.

#### Minor

- **Tax input float artefact.** `String(Number(rate) * 100)`
  (`InvoicesPage.jsx:143`) renders `7.000000000000001` for a 7% matter. The
  preview totals are JS floats (`:194-197`) against a `Decimal` server
  (`billing_extended.py:1068`), so the on-screen total can differ by a cent.
- **Outstanding metric follows the filter** (`:186-190`), so the Paid tab shows
  $0 outstanding.
- **Overdue badge hides Part paid** (`:425, 474`; `InvoiceDetailPage.jsx:71`)
  and staff never see days overdue though the portal computes it.
- **Due date cannot be extended after sending** (`billing_extended.py:1387-1388`).
- **Stripe SDK calls block the event loop** (`:1616-1631`,
  `client_portal.py:2443`) where sibling code uses `asyncio.to_thread`.
- **Webhook catches `stripe.error.SignatureVerificationError`** (`:1834`) while
  the subscription webhook uses `stripe.SignatureVerificationError`
  (`billing.py:250`). Whether `stripe.error` resolves on the pinned 11.3.0 is
  *unverified*; if not, a bad signature is a 500.
- **Payment-link description leaks the matter UUID** to the client (`:1621`).
- **Failed Stripe payments are appended to client-visible notes**
  (`:2081-2088`) which print on the PDF (`invoice_pdf.py:421-424`).
- **Portal has no post-checkout confirmation.** Stripe returns with
  `?payment=success` (`client_portal.py:2459-2460`) and the page ignores it, so
  the client lands back on a balance that has not updated yet because the
  payment row is webhook-created.
- **Portal Pay now is an anchor with `aria-disabled`** (`ClientPortalMatterPage.jsx:1445-1452`).
- **Currency hard-coded to USD** in staff pages (`InvoicesPage.jsx:37-40`) while
  expenses and the portal carry a currency.
- **Two Stripe webhook routes** (`billing.py:223`, `billing_extended.py:1805`);
  only the second handles invoice payments. Operator misconfiguration risk.

### Parity table

| Capability | Expectation | LawHand | Citation |
| --- | --- | --- | --- |
| Pre-bill review and approval | Draft, reviewer edits, approve, send | Partial | `InvoicesPage.jsx:332-384` |
| Line-item edit, discount, write-down | Yes | Missing | `schemas/billing.py:290-295` |
| Bulk generation | Yes | Missing | `schemas/billing.py:348-362` |
| Recurring billing | Yes | Missing, dead code | `recurring_billing.py:163-165` |
| Numbering prefix and sequence | Yes | Partial | `billing_workflow.py:58-73` |
| Templates and Bill To | Yes | Partial | `invoice_pdf.py:184-204` |
| Tax | Per-invoice or per-line | Partial | `billing_extended.py:1061-1068` |
| Interest and late fees | Yes | Missing | — |
| Payment plans, split billing | Yes | Missing | — |
| Apply trust or retainer to invoice | Yes | Missing | `billing_extended.py:1479-1487` |
| Evergreen replenishment request | Yes | Missing | `models/retainer.py:56` |
| Flat fee and contingency | Yes | Missing | `models/plugin.py:249-255` |
| LEDES 1998B | Vendor-valid file | Partial | `ledes_export.py:180-207` |
| Email send, batch send, templates | Yes | Missing | `InvoiceDetailPage.jsx:161` |
| Reminders and dunning | Yes | Missing | `scheduler.py` |
| Viewed tracking | Yes | Partial, audited not shown | `client_portal.py:2500-2530` |
| Portal card payment | Yes | Have, Stripe Checkout | `client_portal.py:2393-2479` |
| Portal ACH, saved methods | Yes | Missing | — |
| Payment links | Yes | Have | `billing_extended.py:1566-1647` |
| Manual and partial payments | Yes | Have | `:1448-1539` |
| Credits, refunds | Yes | Missing | — |
| Void and write-off | Yes | Partial | `:1411-1445` |
| Aging and statements | Yes | Partial, aging only | `reports.py` |
| QuickBooks sync | Yes | Have | `qbo_sync.py:479-488` |
| Xero sync | Clio, PracticePanther | Missing | — |
| List search, sort, filters, bulk | Yes | Partial | `billing_extended.py:1229-1264` |
| Matter Billing tab shows invoices | Yes | Missing | `api.js:2564-2565` unused |
| Loading / empty / error states | Yes | Have | `InvoicesPage.jsx:397-421` |

---

## Part 3 — Reports

### What exists

Four tabs on `/reports` (`ReportsPage.jsx:187-192`):

- **Overview**: one call to `GET /api/reports/bundle` (`reports.py:457-474`)
  rendering matter counts by status, type, and risk (`:56-101`), an intake
  funnel with conversion rate (`:104-130`), and the full list of overdue tasks
  filtered by the user's task visibility (`:133-175`).
- **Realization**: per-matter billable hours, billable time plus expense value,
  payments collected, and a percentage (`:178-270`).
- **WIP**: per-matter unbilled billable time and expenses (`:273-329`).
- **A/R Aging**: per-matter open balance in 0-30, 31-60, 61-90, 90+ buckets
  past due date (`:332-404`).
- CSV export for the three billing reports (`:407-421`) and client-side column
  sort (`ReportsPage.jsx:47-119`).
- Every query filters `tenant_id` and an isolation test exists
  (`tests/test_reports_billing.py:347`). Aggregation happens in SQL per matter.
- Backend-only: a single-matter budget-versus-actual endpoint (`:477-518`) whose
  wrapper `getMatterBudget` (`api.js:2420-2421`) has no caller.
- Report-shaped surfaces that live elsewhere and are not linked from Reports:
  trust statement PDF and reconciliation under Trust Accounting
  (`trust_accounting.py:373-957`, `TrustAccountDetail.jsx:136`); per-check
  conflict report PDF; per-invoice CSV and LEDES.
- No frontend test for `ReportsPage`.

### Findings

Severity is from the managing partner's point of view.

#### Blocking

**R1. Firm financials are visible to any signed-in user.** Every reports route
depends only on `get_current_user` (`reports.py:429, 439, 449, 459, 480, 524,
540, 556`). Sibling finance routes use `require_finance_admin`
(`billing_extended.py:292, 1658, 1752`). The frontend route passes only
`module="reports"` (`App.jsx:348-349`); the `financeOnly` gate exists
(`App.jsx:183`) and is used for Admin (`:460`) but not for Reports or Invoices.
Role presets only hide the nav item (`navigation.js:58-61`). A receptionist can
type `/reports` and read every matter's A/R and realization. Every competitor
gates financial reports behind a permission. *Fix:* `require_finance_admin` on
the billing report routes and the bundle; `financeOnly` on the `/reports` and
`/invoices` routes. Keep overdue tasks on the visibility predicate.

**R2. No date range or filter on any report.** The only query parameter in the
router is `format` (`reports.py:523, 539, 555`). Realization and WIP are
lifetime numbers. There are no controls on the page (`ReportsPage.jsx:123-183`).
Every competitor's report opens with a period picker and filters by timekeeper,
client, and practice area. Without them the reports cannot answer "how did we do
last month." *Fix:* `start`, `end`, `matter_id`, `user_id`, `matter_type`
applied to entry, expense, payment, and issue dates; a shared preset and custom
range bar across tabs.

**R3. Aging counts written-off invoices as receivables.** The exclusion set is
`{"draft", "paid", "cancelled", "void"}` (`reports.py:47`, applied at `:349`).
`written_off` is a real terminal status (`billing_workflow.py:19-23`) and its
unpaid balance lands in a bucket. `cancelled` is not a status anywhere. The
collections report is inflated with forgiven debt. *Fix:* add `written_off` to
the set and a case beside `test_aging_buckets` (`test_reports_billing.py:254`).

#### Major

**R4. No timekeeper dimension.** `TimeEntry.user_id` exists (`models/billing.py:53`)
but nothing groups by it. No billable hours by user, utilization, collection
rate, or revenue by originating or responsible attorney. Productivity by user is
the most-used report in Clio and MyCase. *Fix:* `group_by=matter|user` on
realization and WIP; a productivity endpoint keyed on `user_id`.

**R5. Realization can exceed 100% and is not what the industry calls
realization.** The base is time plus expenses worked (`reports.py:180-237`) and
the numerator is all payments on the matter's invoices (`:243-257`), which can
include flat-fee and adjustment lines (`schemas/billing.py:252`). No invoiced
amount is shown. Billing realization is invoiced over worked; collection rate
is collected over invoiced. *Fix:* add `invoiced_amount` and compute both,
labelled.

**R6. The 0-30 bucket includes invoices not yet due.** `days_overdue <= 30` at
`reports.py:395` is true for negative values. Competitors show a Current column.
*Fix:* a `current` bucket for `<= 0`, then 1-30, 31-60, 61-90, 90+, plus totals.

**R7. No drill-down.** Rows are plain cells (`ReportsPage.jsx:103-113`) although
`matter_id` is in every payload (`reports.py:199, 294, 385`); overdue tasks are
not linked either (`:339-345`). *Fix:* matter name links to the matter's
Billing tab, task title to the task.

**R8. CSV only, and a broken empty CSV.** `_csv_response` writes nothing at all
for zero rows, not even a header (`reports.py:411-415`). CSV includes internal
fields and raw floats, no totals row, no date in the filename. No PDF, Excel, or
print stylesheet. *Fix:* always write the header; totals row; `format=pdf` via
the existing ReportLab pattern in `trust_statement_pdf.py`.

**R9. The library is a fraction of the competitor baseline.** Missing entirely:
billing history and invoice status, payments received, collections, write-offs
and discounts, revenue by practice area, client ledger and statement of account,
trust balances and three-way reconciliation summary, expense report, time-entry
detail, flat-fee summary, fee allocation, upcoming deadlines, lead source. Trust
statement and reconciliation exist under Trust but are not reachable from
Reports. *Fix:* first link Trust Balances and Trust Statement from Reports; then
Payments Received and Invoice Status, which are cheap aggregations over
`Payment` and `Invoice`.

**R10. Budget-versus-actual is dead from Reports.** The endpoint has no UI
caller and there is no firm-wide over-budget list. *Fix:* delete it or add a
firm-level budget report.

**R11. No saved views or scheduled delivery.** Nothing in the codebase. Clio and
PracticePanther email scheduled reports; CosmoLex and Smokeball save views.

#### Minor

- **Float money.** Numeric columns are cast to `float` and summed in Python
  (`reports.py:197, 201, 223, 237, 257, 297, 325, 371, 376, 396-402`) where
  `matter_budget.py:77-80` correctly uses `Decimal`.
- **Matter Status counts archived matters** (`:62-94` never checks
  `archived_at`). Whether that is intended is *unverified*.
- **Overdue tasks list is unbounded** (`:142-162`) and rendered in a card with
  120px truncation (`ReportsPage.jsx:339-345`).
- **Loading blocks the whole page** with an `h-screen` message and the error
  state has no Retry (`:208-222`); billing tabs refetch on every tab switch
  (`:129-138`).
- **Sortable headers are `<th onClick>`** with no button, key handler, or
  `aria-sort` (`:85-98`); the tab bar has no tablist semantics (`:246-258`).
- **`format` is unvalidated**; any value but `csv` returns JSON (`:532, 548, 564`).
- **Three single-report wrappers are unused** (`api.js:2100-2107`).

### Parity table

| Report or capability | Expectation | LawHand | Citation |
| --- | --- | --- | --- |
| A/R aging with Current bucket and totals | Yes, write-offs excluded | Partial | `reports.py:47, 395` |
| Billing history / invoice status | Yes | Missing | — |
| Payments received / collections | Yes | Missing | `Payment` model only |
| WIP by matter and timekeeper, dated | Yes | Partial, matter and lifetime | `:273-329` |
| Productivity by timekeeper | Core report | Missing | no `user_id` grouping |
| Revenue by practice area or attorney | Yes | Missing | — |
| Trust balances, three-way reconciliation | In library | Partial, under Trust only | `trust_accounting.py:373-957` |
| Client ledger / statement of account | Yes | Partial, trust statement only | `TrustAccountDetail.jsx:136` |
| Budget versus actual | Firm-wide | Partial, single matter, no UI | `reports.py:477-518` |
| Expense, time-detail, flat-fee, write-off reports | Yes | Missing | — |
| Task and deadline report | Overdue and upcoming | Partial, overdue only | `:133-175` |
| Intake conversion by source | Yes | Partial, no source, lifetime | `:104-130` |
| Date presets and filters | Every report | Missing | `:523, 539, 555` |
| CSV export | Yes | Have, empty file has no header | `:407-421` |
| PDF or Excel export, print | Yes | Missing | — |
| Scheduled or saved reports | Yes | Missing | — |
| Drill-down | Yes | Missing | `ReportsPage.jsx:103-113` |
| Charts | Yes | Missing | tables only |
| Permission gate on financials | Yes | Missing | `reports.py:429-556`, `App.jsx:348` |
| Tenant scoping | Required | Have | `test_reports_billing.py:347` |

---

## Recommended order

Ranked by how soon a switching firm hits the gap, weighted toward fixes where
the backend already does most of the work.

1. **Close the two integrity holes this week.** Gate Reports and Invoices behind
   the finance permission on both route and API (R1). Add `written_off` to the
   aging exclusion set (R3). Both are a few lines with an existing test file to
   extend.
2. **Make the timer produce a billable entry** (T1, T2, T3). Narrative and
   matter on the banner, hours validated against the tenant increment, local
   date default. Add a header timer chip (T4) and the Billing settings card
   (T5) so the increment is something a firm can actually choose.
3. **Finish the bill review loop** (I1, I2, I5). Draft line-item editing with
   discount and write-down, a real Send with a templated email and PDF, and a
   reachable write-off. These three are the difference between "generates
   invoices" and "does billing."
4. **Connect trust to billing** (I4). One transaction that draws down and posts
   the payment. This unlocks the evergreen replenishment request and makes the
   `retainer` payment method truthful.
5. **Make the PDF and LEDES real** (I6, I7, T6). Persist date, timekeeper, and
   UTBMS codes on line items at generation; Bill To block; page numbers; read
   codes in the LEDES writer. One data-model change serves all three.
6. **Give Reports a period and a person** (R2, R4, R5, R6). Date range and
   `group_by` on the three billing reports, a Current aging bucket, invoiced
   amount alongside collected. Then link Trust reports from the Reports page (R9).
7. **List ergonomics** (I8, T7, T8, R7). Search, sort, paging, server totals,
   timekeeper column, drill-down links, typeahead matter pickers replacing the
   200-row selects.
8. **Decide recurring billing** (I3). Wire it with correct numbering and a
   savepoint, or delete the module so nobody mistakes it for a feature.

Each of these overlaps an open backlog item: `COMP-10` (operating accounting and
payment depth), `TC-09` (branded invoice PDFs), and the Sprint D "Bill this"
item in `TASKS.md`. This review does not authorise duplicate implementation of
those; it sharpens what they need to contain.

## What is genuinely good

Worth keeping intact through any remediation:

- Invoice generation is careful: sources are locked with `FOR UPDATE`, stale
  selections get a 409, tax is quantized to cents, numbering retries on
  collision (`billing_extended.py:990-1149`).
- Payment math is right and tested: overpayment refused, `paid` requires
  coverage, void refused with payments, void releases the source entries.
- Money is `Decimal` everywhere in the billing routes.
- The pre-bill preview separates firm cost from client amount and lets the
  reviewer include or exclude each row before anything is generated.
- Internal-only expense categories and un-reviewed receipts never reach a bill
  (`test_billing_timer_invoices.py:349, 437`).
- Rate resolution has the right tiers and the timer rounds the way firms bill.
- The Stripe webhook guards ordering and idempotency.
- Row-level security is forced on the billing tables, and every report query is
  tenant-scoped with a test proving it.
- Both list pages have honest loading, empty, and error states, and the invoice
  detail page reloads after every mutation so it never shows stale money.

---

## What has been fixed

Everything below landed on this branch after the review, each with tests. The
findings above are left as written so the reasoning stays legible.

**Fixed — reports**

- `R1` Finance gate on the billing report endpoints, the matter budget report,
  and the `/reports` and `/invoices` routes. The frontend check now honours the
  billing capabilities, not just the admin and accountant roles.
- `R2` Date range on realization and WIP, with presets, a custom range, an
  inverted-range 400, and the window in the CSV filename.
- `R3` Written-off invoices no longer count as receivables.
- `R5` Invoiced amount reported alongside collected; billing realization and
  collection rate shown separately.
- `R6` A Current bucket, so an invoice that is not yet due is not read as late.
  Each row carries a Total Due and each table a totals row.
- `R7` Matter rows link through to the matter's billing tab; overdue tasks link
  to Tasks and the card caps its preview.
- `R8` Empty CSV exports carry a header row.
- Minor: `format` validated, tablist semantics, `aria-sort` on sortable headers,
  Retry on a failed load, loading no longer blanks the page.

**Fixed — time tracking**

- `T1` Narrative required and recorded when a timer stops.
- `T2` Manual entry accepts the increment the timer produces; durations parse as
  `1.5`, `1:30`, `90m` or `1h15m` and round to the firm's increment.
- `T3` Local calendar date on manual entries and on timer start.
- `T5` Billing defaults card in Admin for the firm rate and the increment, plus
  a timekeeper-readable endpoint for the increment alone.
- `T7` Timekeeper name returned and shown, a Mine/All filter, and edit and
  delete hidden on entries the user cannot change.
- `T8` Server totals instead of summing the first page.
- Also fixed, beyond the review: a Pydantic field named `date` annotated
  `Optional[date]` resolved to `NoneType`, so **editing any time entry or
  expense failed with a 422** whenever the date was sent, which both edit forms
  always do. Found while adding the local-date field.

**Fixed — invoices**

- `I1` Draft line items can be reworded, re-priced and removed, and discount,
  flat fee and adjustment lines added. Removing a line releases its work.
- `I2` Invoices are emailed with their PDF; a delivery failure leaves the bill a
  draft rather than claiming it was sent.
- `I3` Recurring numbering continues the tenant's sequence, commits per matter,
  and recovers on failure.
- `I4` Trust and retainer funds apply to a bill in one transaction that both
  draws the retainer down and records the payment, with an evergreen shortfall
  flagged.
- `I5` Write-off reachable once a part payment rules out voiding.
- `I11` QuickBooks refuses to sync an unreviewed draft.
- `I12` Money on the matter invoice endpoint serialised as decimal strings.

**Still open**

The larger items are unchanged and still need their own work: per-line dates,
timekeeper and UTBMS codes persisted at generation, which the PDF (`I6`) and a
vendor-valid LEDES file (`I7`, `T6`) both depend on; a Bill To block and
selectable templates; bulk generation and bulk send (`I9`, part of `I2`);
interest, payment plans, split billing, statements and numbering settings
(`I10`); list search, sort and paging (`I8`); credits and refunds (part of
`I5`); a timekeeper dimension and the missing report library entries (`R4`,
`R9`); saved and scheduled reports (`R11`); and a global header timer (`T4`).
