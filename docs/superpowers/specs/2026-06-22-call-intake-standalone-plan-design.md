# Call Intake — Standalone Plan & Tier Framework

**Date:** 2026-06-22
**Status:** Approved design, pending implementation plan
**Goal:** Sell the Call Intake module standalone. Provision a tenant that can only access
the intake dashboard, with a functional, exportable record of call logs **and** partner
(assignment) logs. Build it as a reusable plan/tier framework so future apps/tiers can be
sold the same way, with a clear upsell path into the full platform.

## Decisions (from brainstorming)

| Question | Decision |
|-|-|
| Separation level | Access-gate only — same app/login, tenant flagged intake-only, sees only intake |
| "Partner logs" | Durable, exportable assignment-history event log |
| Provisioning | Operator console toggle **and** public self-serve signup, on an extensible plan registry |
| Self-serve MVP | Provision intake-only tenant + admin + free trial (card-optional); billing/conversion later |
| Upsell surface | Subtle locked-nav teasers → "Upgrade to full platform" panel + lead-capture CTA |
| API enforcement | Yes — backend module guard, fail-closed (not UI-only) |

## Context — what already exists

- `backend/app/services/module_visibility.py` resolves enabled modules and a default route;
  it currently **hardcodes** an `intake-only` plan (`INTAKE_ONLY_MODULES = ["intake-dashboard","plugins"]`).
- `backend/app/routers/platform.py` lets operators set per-tenant `enabled_modules`/`default_module`
  in `TenantSettings.custom_config`.
- `frontend/src/App.jsx` gates routes by `user.enabled_modules` and redirects to `user.default_route`.
- `frontend/src/components/Sidebar.jsx` **hides** nav items whose `module` isn't enabled.
- The intake dashboard (`routers/intake_dashboard.py`, `pages/IntakeDashboardPage.jsx`) already has
  search/history, call capture, partner rotation, recent callers, and a CSV **calls export**.
- Migrations live in `backend/migrations/versions/` (latest `063`; intake tables `061`; RLS hardening `057`/`044`).

> **Precondition:** there are uncommitted changes in `intake_dashboard.py` (+201), its schemas (+6),
> tests (+63), and `IntakeDashboardPage.jsx` (+170). Reconcile/commit these before/within implementation
> so the new work builds on a known baseline.

## Architecture

Five units, each independently testable:

### 1. Plan registry (`backend/app/services/plans.py`) — new

Single source of truth for sellable plans.

```python
@dataclass(frozen=True)
class Plan:
    id: str                    # "intake-only", "full-platform"
    label: str                 # "Call Intake", "Full Platform"
    modules: list[str]         # subset of MODULE_ROUTES keys
    default_module: str        # landing module
    billing_tier: str          # maps to Tenant.billing_tier (signup only)
    public_signup: bool        # exposed to marketing-site signup
    upsell_target: str | None  # plan id to upsell toward

PLANS: dict[str, Plan] = {
  "intake-only":   Plan("intake-only", "Call Intake", ["intake-dashboard"],
                        "intake-dashboard", "intake_trial", True, "full-platform"),
  "full-platform": Plan("full-platform", "Full Platform", list(FULL_PLATFORM_MODULES),
                        "matters", "payg", False, None),
}
```

Helpers: `get_plan(id)`, `public_plans()`, `plan_for_tenant(custom_config)`.

`module_visibility.resolve_enabled_modules` is refactored to consume the registry:
- Read `custom_config["plan"]`; if it names a registry plan, return that plan's modules
  (after `_with_finance_admin` augmentation) and its default route.
- The current hardcoded `if plan == "intake-only"` block is **replaced** by the registry lookup.
- **Intake-only bundle drops `plugins`** (no marketplace; upsell handled by locked nav).
  `_with_finance_admin` still grants the `admin` module to admin/accountant users so an
  intake-only tenant admin can manage users (add receptionists) and rotation.
- Unchanged fallbacks (regression-guarded): explicit `enabled_modules` override still works;
  no plan + no entitlements → full-platform; license-inactive → basic portal.

### 2. Provisioning — two entry points, one registry

**Operator toggle** (`platform.py`):
- Tenant-update accepts `plan`, validated against the registry (reject unknown ids).
- New `GET /api/platform/plans` returns the registry for the operator UI dropdown.
- Sets `custom_config["plan"]`. Does **not** mutate `Tenant.billing_tier` (billing stays
  operator/billing-managed; avoids surprising existing billing).
- Operator frontend: plan dropdown on the tenant detail view.

**Public self-serve signup** — new `POST /api/auth/signup/plan`:
- Body: `{plan, firm_name, email, password, full_name, ...}`.
- **Reject** if `plan` is not a registry plan with `public_signup=True` (future tiers opt in by
  flag — no new endpoint per plan).
- Creates: Tenant (`billing_tier` = plan.billing_tier, e.g. `intake_trial`),
  `TenantSettings.custom_config = {"plan": plan.id, "trial_ends_at": <iso>}`, an **admin** user
  (`license_active=True`), reusing existing registration/user-creation helpers.
- Trial is **informational** for MVP (provision + label). Trial-expiry enforcement and
  Stripe conversion are explicit fast-follows, out of scope here.
- Marketing site posts to this endpoint; an in-app `/signup?plan=intake-only` page can reuse it.

### 3. Partner assignment log + exports

New append-only model `PartnerAssignmentLog` in `models/intake_dashboard.py`:

| Field | Notes |
|-|-|
| `id`, `tenant_id` | RLS-scoped |
| `lead_id`, `contact_id`, `communication_id` | nullable FKs, `ON DELETE SET NULL` |
| `practice_area` | str |
| `assigned_to_user_id` + `assigned_to_name` | FK SET NULL + **name snapshot** |
| `rotation_rule_id` | nullable FK SET NULL |
| `assignment_method` | `partner_rotation` \| `prior_attorney` \| `manual` \| `specific_staff` |
| `assigned_by_user_id` + `assigned_by_name` | actor + snapshot |
| `created_at` | event time |

Indexes: `(tenant_id)`, `(tenant_id, created_at)`, `(tenant_id, assigned_to_user_id)`.
**Migration `064`** creates the table and enables/forces RLS with a tenant policy mirroring `057`/`061`.

Write path — one helper `record_partner_assignment(db, ...)` appends a row (event, never upsert)
at each existing assignment point, snapshotting names at write time:
- `assign_next_partner` → `partner_rotation`
- `create_dashboard_call` (rotation recommendation / prior attorney) → `partner_rotation` / `prior_attorney`
- specific-staff general task → `specific_staff`

No change to existing assignment behavior — purely additive recording.

Endpoints (beside the existing calls export):
- `GET /api/intake/dashboard/partner-log` — paginated; date-range + partner filter.
- `GET /api/intake/dashboard/partner-log/export` — CSV, date-range, same export shape as calls.

Schemas: `PartnerAssignmentLogEntry`, `PartnerAssignmentLogResponse`.

### 4. Intake-only UX + upsell (frontend)

- **Auth payload** (`auth.py` / `schemas/auth.py`, where `enabled_modules`/`default_route` originate):
  also expose `plan` and `upsell_target`.
- **Sidebar**: when `user.upsell_target` is set (a limited plan), render non-enabled nav items as
  **locked** (greyed + lock icon) instead of hiding; click opens the Upgrade panel. Full-platform
  tenants unchanged (still hidden). Footer `billing_tier` shows `intake_trial`.
- **UpgradeModal**: "Upgrade to the full platform" + CTA that records interest via
  `POST /api/plan/upgrade-request` (notifies operators — upsell lead capture).
- **Intake dashboard page**: add a **Partner Log** panel (list + export button) beside the
  existing call-records export.

### 5. Backend module enforcement (fail-closed)

New guard so a limited-plan tenant is walled at the API, not just the UI.

- An `API_MODULE_MAP` of `/api/...` path prefixes → module id (e.g. `/api/matters`→`matters`,
  `/api/invoices`→`invoices`, `/api/trust`→`trust`, …).
- Enforcement layer (FastAPI dependency or middleware after auth): for a request whose path prefix
  is in the map, resolve the tenant's enabled modules; if the mapped module isn't enabled → **403**.
- **Fail-closed for mapped prefixes**; unmapped/shared infra (`/api/auth`, `/api/me`, `/api/users`,
  `/api/notifications`, `/health`, portal, intake, admin, plugins-listing) passes. New module routers
  must be added to the map (documented next to the map).
- Enabled-modules resolution is cached on `request.state` to avoid duplicate DB lookups per request.
  (Optimization noted, not required for MVP: carry `plan` as a JWT claim to avoid the lookup.)

## Data flow

```
Marketing signup ─POST /api/auth/signup/plan─▶ Tenant(plan=intake-only, intake_trial)
Operator console ─PATCH tenant {plan}───────▶ custom_config.plan
                                                   │
login / /me ──resolve_enabled_modules(plan)──▶ enabled_modules, default_route, plan, upsell_target
                                                   │
Frontend: route gate + locked-nav upsell  ◀───────┤
Backend:  API_MODULE_MAP guard (403)      ◀───────┘

Reception logs call ─▶ CommunicationLog (call log)
        └ assigns ───▶ Lead/Task + record_partner_assignment ─▶ PartnerAssignmentLog (partner log)
Exports: /calls/export (CSV) · /partner-log/export (CSV)
```

## Error handling

- Unknown `plan` (operator or signup) → 422.
- Non-public plan via signup → 403.
- `record_partner_assignment` failures must not break the user-facing assignment commit — it writes
  within the same transaction as the assignment; if the log insert is the failure, surface it (the
  assignment is the product, the log is the record — they commit together to stay consistent).
- Module guard denial → 403 `{"detail": "Module not available on your plan"}`.

## Testing

- **plans**: resolution for both plans; `public_signup` gating; `upsell_target` surfaced.
- **module_visibility regressions**: full-platform fallback, `enabled_modules` override,
  license-inactive → basic portal — all unchanged.
- **operator toggle**: `plan` validated against registry; `GET /plans`.
- **signup**: public plan provisions tenant+admin+trial; non-public plan rejected (403).
- **partner log**: each assignment path writes one row with correct `assignment_method` and name
  snapshots; list + export; **RLS tenant isolation**.
- **module guard**: intake-only tenant gets 403 on `/api/matters` etc.; allowed on intake APIs;
  full-platform unaffected.
- **frontend**: Sidebar renders locked items for `upsell_target` plans; UpgradeModal fires
  lead-capture; Partner Log panel + export.

## Out of scope (explicit fast-follows)

- Trial-expiry enforcement and Stripe checkout/conversion (MVP provisions + labels the trial).
- White-label branding (chosen separation = access-gate only).
- Migrating off `custom_config.plan` to a full entitlements/billing engine.

## Build order

1. Plan registry + `module_visibility` refactor (+ regression tests).
2. Backend module guard (`API_MODULE_MAP`).
3. `PartnerAssignmentLog` model + migration `064` + `record_partner_assignment` wiring + endpoints.
4. Operator toggle (`platform.py` + operator UI) and public signup endpoint.
5. Auth payload (`plan`/`upsell_target`) + Sidebar locked-nav + UpgradeModal + upgrade-request endpoint.
6. Intake dashboard Partner Log panel + export.
7. Tests across all units; reconcile pre-existing uncommitted intake changes.
