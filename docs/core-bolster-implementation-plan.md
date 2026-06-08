# Core Standard Bolster — Implementation Plan

**Date:** 2026-06-05
**Source:** [`docs/competitive-gap-analysis.md`](competitive-gap-analysis.md)
**Goal:** Reach table-stakes parity with Clio / MyCase / PracticePanther on the
practice-management core so the AI moat wins deals instead of being disqualified
on a feature checklist. Everything here lands in the **standard** (flat-seat)
tier.

Migration numbering continues from the current head (`043_litellm_usage_route_audit`),
so new migrations start at **044**.

Conventions used below:
- **Reuse** = existing code we extend rather than build new.
- Each epic lists: data model → backend → frontend → reuse → acceptance → effort.
- Effort: SMALL (≤2d), MEDIUM (3–5d), LARGE (1–2wk), XL (>2wk).

---

## Epic 1 — Client Portal (P0, LARGE)

**Why:** The #1 "why we switched to Clio" reason. A secure client login to see
matter status, exchange messages, view/upload documents, and view/pay invoices.

**The unlock:** the mediation portal already implements the entire hard part —
tokenized invites (`MediationInvite`: sha256 `token_hash`, `kind`, `expires_at`,
`accepted_at`, `revoked`), scoped-JWT cookie auth (`mediation_portal.py`
`_set_cookie` / `_resolve` / `PortalContext`), and a portal SPA
(`PortalAcceptPage`, `PortalCasePage`). We generalize that pattern from
`MediationCase` to `Matter`.

### Data model — migration `044_client_portal`
- `client_portal_invites` — `id, tenant_id, matter_id (FK), contact_id (FK),
  token_hash, kind ('client_account'|'portal_magic'), email, expires_at,
  accepted_at, revoked, created_by, created_at`. RLS by `tenant_id`.
- `client_portal_messages` — threaded messages scoped to a matter (or reuse
  `communication_log` with a `channel='portal'` + `visible_to_client` flag —
  **preferred**, avoids a parallel store).
- Add `portal_visible: bool` to `MatterDocument` (default false) so the firm
  controls which case files the client sees. Add `portal_enabled: bool` to
  `Matter`.

### Backend
- `routers/client_portal.py` (mirror `mediation_portal.py`): `POST /accept`,
  `GET /matter` (status, key dates, assigned attorneys), `GET/POST /messages`,
  `GET /documents` + `POST /documents/upload` + `GET /documents/{id}/download`
  (route through `matter_file_store`), `GET /invoices` + `GET /invoices/{id}`,
  `POST /invoices/{id}/pay` (reuse the Stripe payment-link flow from
  `billing_extended`).
- `routers/matters.py`: add firm-side `POST /matters/{id}/portal/invite`,
  `GET /matters/{id}/portal/invites`, `DELETE .../invites/{id}` (revoke).
- Reuse the email sender for invite delivery.

### Frontend
- New unauthenticated routes `/portal/client/accept` + `/portal/client/matter`
  in `App.jsx` (sibling to existing `/portal/*`).
- New pages `ClientPortalAcceptPage.jsx`, `ClientPortalMatterPage.jsx` (tabs:
  Overview, Messages, Documents, Invoices).
- `MatterDetailPage.jsx`: a **Client Portal** tab — toggle access, manage
  invites, mark documents `portal_visible`.
- `api.js`: `clientPortal*` function group.

### Acceptance
- Firm invites a client from a matter → client receives email → accepts → sees
  only that matter's status, firm-shared documents, messages, and invoices.
- Client uploads a document → appears firm-side on the matter.
- Client pays an invoice via the portal → payment records against the invoice.
- Tenant isolation verified (no cross-tenant/cross-matter leakage).

**Dependencies:** none. **Effort:** LARGE.

---

## Epic 2 — Native E-Signature (P0, MEDIUM)

**Why:** Engagement letters, retainers, consents must be signed in-product.
Bundled by Clio/MyCase/PracticePanther.

**The unlock:** `DocumentTemplate` (render with `{{var}}` → `MatterDocument`)
already produces the document to be signed; the portal (Epic 1) is where clients
sign.

### Data model — migration `045_esignature`
- `signature_requests` — `id, tenant_id, matter_id (FK), document_id (FK
  matter_documents), status ('draft'|'sent'|'partially_signed'|'completed'|
  'declined'|'voided'), provider ('internal'|'dropbox_sign'|'docusign'),
  provider_envelope_id, created_by, sent_at, completed_at, created_at`.
- `signature_signers` — `id, request_id (FK), contact_id (FK, nullable),
  name, email, order, status, signed_at, signed_ip, audit JSON`.

### Backend
- `services/esign/` with a provider interface + two adapters:
  - `internal` adapter — typed/drawn signature captured in the client portal,
    stamped into a generated PDF, with an audit trail (cheapest; good enough for
    standard tier).
  - `dropbox_sign` (or DocuSign) adapter behind the same interface for firms that
    require a certified provider.
- `routers/esignature.py`: create request from a `MatterDocument`, add signers,
  send, status, webhook receiver, download signed PDF.
- On completion → write signed PDF back as a new `MatterDocument` version + event
  on the matter timeline.

### Frontend
- In `MatterDetailPage.jsx` Documents tab: "Request signature" → pick signers →
  send. Status chips on documents.
- In the client portal (Epic 1): pending-signature list + sign action.

### Acceptance
- Generate engagement letter from a template → request signature → client signs
  in portal → signed PDF stored on the matter, status `completed`, timeline event.
- Provider adapter swap works without touching routers.

**Dependencies:** Epic 1 (portal is the signing surface). **Effort:** MEDIUM.

---

## Epic 3 — Trust Accounting Frontend + Reconciliation UI (P0, MEDIUM)

**Why:** Compliance blocker for any firm holding client funds. **The three-way
reconciliation logic already exists** (`trust_accounting.py`
`POST /accounts/{id}/reconcile`, `GET /accounts/{id}/reconciliation`) — it is
**headless** (no pages, no `api.js`, no routes; see TASKS BK05).

### Data model — migration `046_trust_ledger`
- Today there is **one `TrustAccount` per matter**. For true three-way
  reconciliation across a pooled IOLTA bank account, add a pooled-account concept:
  - `trust_bank_accounts` — the real (pooled) bank account; `bank_name,
    account_number_masked, last_reconciled_at`.
  - Link `trust_accounts.bank_account_id` (existing per-matter accounts become
    **client ledgers** within a pooled bank account).
  - `trust_reconciliations` — saved snapshots (`as_of_date, bank_balance,
    book_balance, client_ledger_total, difference, is_balanced, performed_by,
    statement_ref`) so reconciliations are auditable, not ephemeral.
- Extend reconcile logic to assert the third leg: **sum of client ledgers ==
  book balance == adjusted bank balance**.

### Backend
- Extend `trust_accounting.py`: pooled-account CRUD, persisted reconciliation
  snapshots, per-client-ledger statement endpoint, trust-activity export (CSV/PDF).
- Guardrails: block disbursement that would overdraw a client ledger (negative
  balance) — a core IOLTA rule.

### Frontend
- New `TrustAccountingPage.jsx` + route `/trust-accounting`; sidebar nav link.
- Views: account list, per-matter/client ledger, deposit/disbursement entry,
  **Reconcile** screen (enter bank statement → see the three balances + reconciling
  items → save snapshot).
- `MatterDetailPage.jsx`: trust balance card + recent trust activity.
- `api.js`: `trust*` function group.

### Acceptance
- Record deposit/disbursement against a matter's client ledger; balance updates.
- Disbursement overdrawing a client ledger is rejected.
- Reconcile screen shows bank == book == sum-of-client-ledgers and saves a snapshot.
- Trust report exports.

**Dependencies:** none (independent of portal). **Effort:** MEDIUM.

---

## Epic 4 — Public Intake Forms + Online Scheduling (P1, LARGE)

**Why:** Top-of-funnel revenue. Our intake pipeline is staff-only; competitors
capture leads via public practice-area forms (conditional logic) and consult
booking.

**The unlock:** `Lead` + `Contact` models + the intake pipeline already exist;
we add a public capture surface that writes into them.

### Data model — migration `047_intake_forms`
- `intake_forms` — `id, tenant_id, slug (public), title, practice_area, schema
  JSON (fields + conditional logic), is_published, redirect_url, created_by`.
- `intake_form_submissions` — `id, tenant_id, form_id, payload JSON, lead_id (FK,
  set on conversion), source, ip, created_at`.
- `booking_slots` / availability — or derive availability from the
  Google/Microsoft calendars we already sync (preferred; less to maintain).

### Backend
- `routers/intake_forms.py`: firm CRUD + **public** unauthenticated
  `GET /public/intake/{slug}` (schema) and `POST /public/intake/{slug}` (submit →
  create `Contact` + `Lead`, fire notification to assigned attorney, optional
  conflict pre-check via `services/conflict_check.py`).
- Public scheduling: `GET /public/schedule/{slug}` availability from synced
  calendars, `POST` to book → calendar event + `Lead`/`CommunicationLog` entry.
- Rate-limit + spam protection on public endpoints (reuse middleware/limiter).

### Frontend
- Firm: `IntakeFormsPage.jsx` (builder: drag fields, conditional rules, publish).
- Public: lightweight standalone render of the form + booking widget (no auth).
- `IntakePage.jsx`: show form submissions feeding the pipeline.

### Acceptance
- Publish a form → public URL renders → submission creates a Contact + Lead and
  notifies the attorney → appears in the intake pipeline.
- Conditional fields show/hide correctly.
- Public consult booking creates a calendar event and a lead.

**Dependencies:** none (conflict pre-check optional). **Effort:** LARGE.

---

## Epic 5 — Court-Rules Deadline / Docketing Engine (P1, MEDIUM→LARGE)

**Why:** Litigation malpractice-risk deal-breaker. We store `key_dates` as free
JSON and only aggregate them; we cannot *calculate* deadlines from a triggering
event under jurisdiction rules.

### Approach — phase it
- **Phase 1 (MEDIUM):** integrate **LawToolBox API** (industry standard; 50
  states / 2,300+ jurisdictions). Fast path to parity.
- **Phase 2 (LARGE, later):** evaluate a native rules engine seeded from the
  CourtListener pipeline — more defensible and on-brand, but slower.

### Data model — migration `048_deadlines`
- `deadline_rulesets` — cached jurisdiction rule metadata.
- `matter_deadlines` — `id, tenant_id, matter_id, name, trigger_event,
  trigger_date, calculated_date, rule_id, jurisdiction, source
  ('rule'|'manual'), is_complete, reminder_offsets JSON`. Migrate existing
  `Matter.key_dates` JSON into rows.

### Backend
- `services/docketing.py` — LawToolBox client: given trigger event + jurisdiction
  + date → returns the deadline set; persist as `matter_deadlines`.
- `routers/deadlines.py`: CRUD + "calculate from trigger" action; feed the
  existing aggregated calendar.
- Hook into the existing task-reminder scheduler for deadline alerts.

### Frontend
- `MatterDetailPage.jsx`: Deadlines section — pick trigger event + jurisdiction →
  auto-populate the deadline chain; manual add still supported.
- Surface calculated deadlines in `CalendarPage.jsx` (already aggregates key dates).

### Acceptance
- Enter "complaint served on <date>" in jurisdiction X → system calculates the
  rule-based deadline chain and shows them on the matter + calendar with reminders.

**Dependencies:** LawToolBox API credentials (commercial agreement). **Effort:**
MEDIUM (phase 1).

---

## Epic 6 — Two-Way SMS / Text (P1, SMALL→MEDIUM)

**Why:** Clients expect texting; conversations should thread into the matter.

**The unlock:** `CommunicationLog` already models channels — add SMS as a channel.

### Data model — migration `049_sms` (minimal)
- Add `external_id`, `direction`, `from_number`, `to_number` to
  `communication_log` if not present; tenant Twilio config on `TenantSettings` /
  `tenant_credential`.

### Backend
- `services/sms.py` (Twilio): send + inbound webhook → write `CommunicationLog`
  with matter/contact linkage (match by phone).
- `routers/communications.py`: `POST /communications/sms` send; webhook receiver.

### Frontend
- `CommunicationsPage.jsx` + `MatterDetailPage.jsx`: SMS thread view + composer.

### Acceptance
- Send a text from a matter → logged; client reply lands on the matter thread.

**Dependencies:** Twilio account/number. **Effort:** SMALL–MEDIUM.

---

## Epic 7 — No-Code Workflow Automation (P1, LARGE)

**Why:** PracticePanther's headline ("8 hrs/week saved") — triggers that
auto-create tasks/events/documents. We only have the recurring-billing scheduler.
Natural pairing with our AI (suggest the next action).

### Data model — migration `050_workflows`
- `workflows` — `id, tenant_id, name, trigger_type ('matter_opened'|
  'stage_changed'|'lead_created'|'invoice_overdue'|...), trigger_filter JSON,
  is_active`.
- `workflow_actions` — ordered actions (`create_task`, `create_event`,
  `send_template_email`, `request_signature`, `generate_document`,
  `notify_user`) with config JSON + relative offsets.
- `workflow_runs` — execution audit.

### Backend
- `services/workflow_engine.py` — evaluate triggers (emit domain events from
  matter/lead/invoice mutations) → enqueue actions; run via APScheduler.
- `routers/workflows.py`: CRUD + manual run + run history.

### Frontend
- `WorkflowsPage.jsx`: trigger → action builder; run log.

### Acceptance
- "On matter opened in practice area X → create task checklist + send welcome
  email + schedule kickoff" fires automatically and is auditable.

**Dependencies:** benefits from Epics 2 (e-sign action) & 8 (doc action).
**Effort:** LARGE.

---

## Epic 8 — Depth & polish (P2)

Smaller items that close the long tail; schedule opportunistically.

- **Document automation overhaul** (MEDIUM) — native DOCX/PDF assembly with field
  mapping (already in TASKS "Future"); replaces text-only `{{var}}` templates.
- **Contact/matter custom fields + contact relationships** (MEDIUM) — per-tenant
  custom fields; contact↔contact links (company↔people, related parties).
- **Email-to-matter auto-filing** (MEDIUM) — auto-file inbound/outbound email to
  the matter and include in conflict search (à la PracticeMaster).
- **Conflict-check hardening** (SMALL) — promote `services/conflict_check.py` to a
  first-class, indexed, partial/phonetic search to rival Tabs3.
- **Reporting/BI depth** (MEDIUM) — realization/collection, WIP, A/R aging,
  matter profitability.
- **Native mobile apps** (XL) — defer; responsive web covers near-term.

---

## Sequencing & milestones

| Milestone | Epics | Theme | Outcome |
|-|-|-|-|
| **M1 — Client-facing core** | 1, 2, 3 | P0 table stakes | Firms holding client funds + needing a portal can switch |
| **M2 — Intake & litigation** | 4, 5, 6 | P1 growth + litigation | Top-of-funnel capture + litigation deadline safety + modern client comms |
| **M3 — Efficiency & depth** | 7, 8 | P1/P2 | Automation + the long-tail polish |

**Critical path inside M1:** Epic 1 (portal) unblocks Epic 2 (signing surface).
Epic 3 (trust) is parallel. Recommend starting **Epic 1 and Epic 3 concurrently**.

### External dependencies to line up early
- E-sign provider (Dropbox Sign / DocuSign) — Epic 2.
- LawToolBox commercial API — Epic 5.
- Twilio account + number — Epic 6.

### Cross-cutting requirements (apply to every epic)
- **RLS** on all new tables (tenant isolation is enforced at the DB layer).
- **Audit** new mutations into the existing event/usage logging.
- **Tier gating** via `TenantSettings.features` (see `standard_premium.md`) — these
  ship in the standard tier; rate limits apply to public endpoints.
- Alembic migration + `models/__init__.py` registration + Pydantic v2 schemas.
</content>
