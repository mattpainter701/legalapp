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
| `modules` | Module ids the plan unlocks (`["intake-dashboard"]`) |
| `default_module` | Landing module after login |
| `billing_tier` | Mapped to `Tenant.billing_tier` at signup (`intake_trial`) |
| `public_signup` | Whether the public signup endpoint may provision it |
| `upsell_target` | Plan id to upsell toward (`full-platform`), or `null` |

Current registry: `intake-only` (public, upsells to full) and `full-platform` (default,
not public). The `intake-only` plan bundles `intake-dashboard` **and** `tasks` — call
follow-up is task-driven, so assignees need the Tasks page to see and work their leads and
the receptionist needs it to watch follow-up state. Admin/accountant users additionally get
the `admin` module so an intake-only firm can manage its own users and partner rotation.

Adding a new tier = add one `Plan` entry. No endpoint or schema changes required.

### Resolution

`module_visibility.resolve_enabled_modules` reads `TenantSettings.custom_config["plan"]`,
looks it up in the registry, and returns the enabled modules + default route. Tenants with no
plan configured fall back to the full platform (backward compatible). `resolve_plan_meta`
returns `(plan_id, upsell_target)` for the auth payload and the JWT `plan` claim.

## Provisioning a tenant as intake-only

### 1. Operator console (existing tenants)

Platform admin UI → Tenants → expand a tenant → **Plan** → select `Call Intake` → **Set Plan**.

API (requires `X-Platform-Key`):

```
GET  /api/platform/plans                      # list available plans
PUT  /api/platform/tenants/{tenant_id}        # body: {"plan": "intake-only"}
```

The operator toggle sets `custom_config.plan` only; it does **not** change `billing_tier`
(billing stays operator/billing-managed).

### 2. Public self-serve signup (new tenants, from the marketing site)

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

- Rejected (403) unless the named plan has `public_signup = true` — future tiers opt in by flag.
- Creates the tenant (`billing_tier = intake_trial`), `TenantSettings.custom_config =
  {plan, trial_ends_at}` (14-day trial), and an **admin** user, then logs them in.
- Trial is informational in this release; expiry enforcement + Stripe conversion are
  planned fast-follows.

## Access enforcement (fail-closed)

`ModuleGuardMiddleware` (`backend/app/middleware/module_guard.py`) inspects the signed `plan`
claim in the access token. Requests to a module-scoped API prefix outside the plan get
**403 `{"detail": "Module not available on your plan"}`**.

- Mapped prefixes: `/api/matters`, `/api/chat`, `/api/calendar`, `/api/tasks`,
  `/api/communications`, `/api/contacts`, `/api/templates`, `/api/time-tracking`,
  `/api/invoices`, `/api/trust`, `/api/reports`, `/api/mcp`. **New module routers must be
  added to `API_MODULE_MAP`.**
- Shared infra (`/api/auth`, `/api/me`, `/api/plan`, intake, admin, plugins listing, health,
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
| `viewed_at` | The **assignee** opens the Tasks page or fetches the task (`GET /api/tasks/{id}` or `POST /api/tasks/{id}/view`); views by other users never count | "Seen / Unread" badge on task rows, "Task seen" on the intake dashboard call panel, `task_viewed_at` in the calls CSV export |
| `customer_contacted_at` + `customer_contact_method` | Assignee (or admin) posts `POST /api/tasks/{id}/contacted` `{method: call\|email\|sms\|meeting\|other, note}` — the "Log contact" action on a task row. First-contact timestamp is preserved; the note is appended to the task description and a `pending` task moves to `in_progress` | "Contacted" badge, "Customer contacted" on the call panel, `customer_contacted_at` / `customer_contact_method` in the calls export |

These are **in-app receipts** — they measure engagement with the assigned task, not email
opens. Native M365/Google email read receipts (`Disposition-Notification-To` /
`Return-Receipt-To` headers on the assignment email) are recipient-dismissable and widely
blocked, so they are deliberately not relied on. Detecting a reply to the customer from
M365/Google sent-mail would require per-user `Mail.Read`/Gmail scopes and is a possible
future enhancement; the explicit "Log contact" action is the reliable signal today.

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
