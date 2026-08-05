# Standalone Call Intake — Plan/Tier Framework

Sell the Call Intake module as a self-contained product. A tenant on the `intake-only`
plan can only access the receptionist intake dashboard; everything else in the platform is
hidden in the UI and **blocked at the API**. The same machinery is a reusable plan/tier
framework — additional sellable tiers are added by a single registry entry — with a built-in
upsell path into the full platform.

- Design spec: `docs/superpowers/specs/2026-06-22-call-intake-standalone-plan-design.md`
- Implementation plan: `docs/superpowers/plans/2026-06-22-call-intake-standalone-plan.md`

## Concepts

### Plans (`backend/app/services/plans.py`)

A `Plan` is the unit of sale: a named bundle of modules plus metadata.

| Field | Meaning |
|-|-|
| `id` | Stable identifier stored on the tenant (e.g. `intake-only`) |
| `label` | Display name (`Call Intake`) |
| `modules` | Module ids the plan unlocks (`["tasks", "intake-dashboard"]`) |
| `default_module` | Landing module after login |
| `billing_tier` | Mapped to `Tenant.billing_tier` at signup (`intake_trial`) |
| `public_signup` | Whether the public signup endpoint may provision it |
| `upsell_target` | Plan id to upsell toward (`full-platform`), or `null` |

Current registry: `intake-only` (eligible for a future public flow, upsells to full) and
`full-platform` (default, not public). The global `PUBLIC_SIGNUP_ENABLED` switch remains
false for the first-customer release, so all new tenants are operator-provisioned. The
`intake-only` plan exposes the Call Intake dashboard and Tasks so staff
can receive, work, document, and close caller follow-ups. Contacts and communications
remain server-side workflow dependencies rather than separate navigation modules.

Adding a new tier = add one `Plan` entry. No endpoint or schema changes required.

### Resolution

`module_visibility.resolve_enabled_modules` reads `TenantSettings.custom_config["plan"]`,
looks it up in the registry, and returns the enabled modules + default route. Tenants with no
plan configured fall back to the full platform (backward compatible). `resolve_plan_meta`
returns `(plan_id, upsell_target)` for the auth payload and the JWT `plan` claim.

## Provisioning a tenant as intake-only

### 1. Operator console (existing tenants)

Platform admin UI → Tenants → expand a tenant → **Plan** → select `Call Intake` → **Set Plan**.

API (requires a short-lived platform bearer token with the matching read/write
scope; `X-Platform-Key` is accepted only by the bootstrap exchange route):

```
GET  /api/platform/plans                      # list available plans
PUT  /api/platform/tenants/{tenant_id}        # body: {"plan": "intake-only"}
```

The operator toggle sets `custom_config.plan` only; it does **not** change `billing_tier`
(billing stays operator/billing-managed).

### 2. Future public self-serve signup (disabled for this launch)

```
POST /api/auth/signup/plan
{
  "plan": "intake-only",
  "firm_name": "Reception Co",
  "email": "owner@reception.co",
  "password": "<min 12 chars>",
  "full_name": "Owner One"
}
```

- Rejected (403) while `PUBLIC_SIGNUP_ENABLED=false`, and also unless the named
  plan has `public_signup = true`.
- Creates the tenant (`billing_tier = intake_trial`), internal access-window metadata,
  and an **admin** user, then logs them in.
- Access-window metadata is not yet an enforcement or paid-conversion boundary.
  Production preflight therefore requires public signup to remain disabled and
  the public site routes prospects to the verified contact destination.

## Access enforcement (fail-closed)

`ModuleGuardMiddleware` (`backend/app/middleware/module_guard.py`) inspects the signed `plan`
claim in the access token. Requests to a module-scoped API prefix outside the plan get
**403 `{"detail": "Module not available on your plan"}`**.

- Mapped prefixes: `/api/matters`, `/api/chat`, `/api/calendar`, `/api/tasks`,
  `/api/communications`, `/api/contacts`, `/api/templates`, `/api/time-tracking`,
  `/api/invoices`, `/api/trust`, `/api/reports`, `/api/mcp`, and the complete
  `/api/plugins` namespace (catalog, specialized CRUD, setup/profile, and LLM
  skills). **New module routers must be added to `API_MODULE_MAP`.**
- Shared infra (`/api/auth`, `/api/me`, `/api/plan`, intake, admin, health,
  portal) is never blocked.
- The `plan` claim is signed by the server (set at token issuance), so it is trustworthy.
  Tokens with no claim default to full-platform — pre-existing sessions are never blocked;
  freshly provisioned intake tenants always carry the claim.

## Call logs & partner logs (exportable)

Two tenant-scoped, CSV-exportable records:

| Data | List | Export |
|-|-|-|
| Call records | recent-callers panel | `GET /api/intake/dashboard/calls/export` |
| Partner assignments | `GET /api/intake/dashboard/partner-log` | `GET /api/intake/dashboard/partner-log/export` |

The **partner assignment log** (`partner_assignment_log`, migration `064`, RLS-scoped) is an
append-only record written on every assignment event:

- `partner_rotation` — next-in-line rotation (`/leads/{id}/assign-next`)
- `prior_attorney` — assigned to the recommended prior attorney on call capture
- `specific_staff` — general task routed to a chosen staff member

Each row snapshots `assigned_to_name` / `assigned_by_name` at write time, so the log stays
accurate even if a user is renamed or removed. Both exports accept optional `start`/`end`
date-range query params. The intake dashboard shows a **Partner Log** panel with inline
export, alongside the existing call-records export.

## Follow-up accountability (read receipts + customer contact)

Every intake follow-up task tracks whether the assignee actually saw it and whether the
caller was contacted back (migration `073`, columns on `tasks`):

| Signal | Set when | Surfaced |
|-|-|-|
| `viewed_at` | The **assignee** opens task detail (`GET /api/tasks/{id}`) or explicitly marks it viewed (`POST /api/tasks/{id}/view`); loading the list or privacy-minimized board alone does not count, and views by other users never count | "Seen / Unread" badge on task rows, "Task seen" on the intake dashboard call panel, `task_viewed_at` in the calls CSV export |
| `customer_contacted_at` + `customer_contact_method` | Assignee (or admin) posts `POST /api/tasks/{id}/contacted` `{method: call\|email\|sms\|meeting\|other, note}` — the "Log contact" action on a task row. First-contact timestamp is preserved; the note is appended to the task description and a `pending` task moves to `in_progress` | "Contacted" badge, "Customer contacted" on the call panel, `customer_contacted_at` / `customer_contact_method` in the calls export |

These are **in-app receipts** — they measure engagement with the assigned task, not email
opens. Native M365/Google email read receipts (`Disposition-Notification-To` /
`Return-Receipt-To` headers on the assignment email) are recipient-dismissable and widely
blocked, so they are deliberately not relied on. Detecting a reply to the customer from
M365/Google sent-mail would require per-user `Mail.Read`/Gmail scopes and is a possible
future enhancement; the explicit "Log contact" action is the reliable signal today.

## Assignment notes, closure reasons & customer history (migration `074`)

**Assigner draft:** `assignment_note` on `POST /api/tasks` (create + assign) and
`PATCH /api/tasks/{id}` (reassign) carries a personal message from the assigner. It is
rendered as a highlighted **Message from assigner** row in the assignment alert email and
appended to the task description (`Assignment note (<assigner>):` /
`Reassignment note (<assigner>):`), so the instruction survives on the task itself. The
assignment email is otherwise a structured system template
(`EmailService.send_task_assignment_alert`): task, priority, type, due, customer, matter,
source, description.

Assignment alerts are a best-effort notification around a durable task. The
sender returns a machine-readable result (`sent`, `disabled`, `unconfigured`,
`invalid_recipient`, or `failed`) and never treats disabled development mode as
a successful send. Task, intake, rotation, and attorney-handoff records remain
saved when SMTP is unavailable, so an email outage cannot discard receptionist
work; the UI reports the durable assignment, not a delivery receipt. The
explicit manual-reminder action is different: disabled/incomplete SMTP maps to
HTTP 503, an invalid recipient maps to 422, and provider rejection maps to 502,
so it can never show **Sent** without a successful provider call. Production
intentionally uses `EMAIL_ENABLED=false`; LawHand does not operate an SMTP
sender. Operational incidents are delivered through GitHub production-health
issues, while task and intake state remain durable.

**Close with reason:** completing or cancelling via `PATCH /api/tasks/{id}` records
`closed_reason` and `closed_by_user_id`. Cancelling **requires** `closed_reason`
(422 otherwise); the checkbox quick-complete stays reason-optional. Reopening
(status back to pending/in-progress) clears both. The Tasks page has per-row
**Reassign** (new assignee + message) and **Close** (outcome + required reason) actions,
and closed rows show their reason.

**Customer history:** every lifecycle event on a *contact-linked* task is documented as a
`CommunicationLog` row on that contact (`app/services/task_history.py`,
`external_ref = task:{task_id}:{event}`):

| Event | When | Channel |
|-|-|-|
| `assigned` / `reassigned` | Task created with an assignee, reassigned, intake-dashboard lead follow-up / general-call task upserts, partner qualify handoff | `other` |
| `contacted` | `POST /api/tasks/{id}/contacted` (Log Contact) | the contact method (`call`/`email`/`sms`/`meeting`, else `other`), direction `outbound` |
| `completed` / `cancelled` | Status transition, with the closure reason in the body | `other` |

Tasks without a `contact_id` are never written to the history — the communication log is
the customer's record, not an internal audit trail. Outbound task rows do not pollute the
intake call feed, which filters on `channel="call" AND direction="inbound"`.

## Upsell

On a limited plan, the sidebar renders the other modules as **locked teasers** (greyed, lock
icon). Clicking opens an **Upgrade to the full platform** modal. A "Request upgrade" CTA posts
to `POST /api/plan/upgrade-request` and records the lead in `plan_upgrade_requests`
(migration `065`, RLS-scoped) for sales follow-up. The auth payload (`/api/auth/me`) exposes
`plan` and `upsell_target` to drive this UI.

## Files

| Area | Path |
|-|-|
| Plan registry | `backend/app/services/plans.py` |
| Module resolution | `backend/app/services/module_visibility.py` |
| API guard | `backend/app/middleware/module_guard.py` |
| Partner log model | `backend/app/models/intake_dashboard.py` (`PartnerAssignmentLog`) |
| Upgrade-request model | `backend/app/models/plan_upgrade.py` |
| Intake/partner-log endpoints | `backend/app/routers/intake_dashboard.py` |
| Operator toggle | `backend/app/routers/platform.py` |
| Public signup | `backend/app/routers/auth.py` (`/signup/plan`) |
| Upgrade-request endpoint | `backend/app/routers/plan.py` |
| Migrations | `backend/migrations/versions/064_partner_assignment_log.py`, `065_plan_upgrade_requests.py` |
| Sidebar locked nav | `frontend/src/components/Sidebar.jsx`, `components/UpgradeModal.jsx` |
| Intake dashboard | `frontend/src/pages/IntakeDashboardPage.jsx` (Partner Log panel) |
| Operator plan selector | `frontend/src/pages/PlatformPage.jsx` |

## Tests

`backend/tests/`: `test_plans.py`, `test_module_guard.py`, `test_platform_plans.py`,
`test_auth_signup_plan.py`, `test_upgrade_request.py`, plus partner-log and licensing
coverage in `test_intake_dashboard.py` / `test_licensing_access.py`.
