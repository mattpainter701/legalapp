# Integrations Management + Daily User Sync — Design

**Date:** 2026-06-03
**Sprint:** 8 — Tenant Onboarding & Integration Hub
**Status:** Approved (design)

## Problem

The admin OAuth permissions UI lives in a separate "Permissions" tab and only
shows granted scopes. It does more than that conceptually (it is the hub for
cloud integration health), and it lacks two things admins need:

1. **Visibility into how many directory users have been pulled** from connected
   Google/Microsoft tenants.
2. **An ongoing sync.** Directory users are synced only once, on connect
   (`_onboarding_post_connect` → `UserSyncService.sync_*_users`). There is no
   scheduled refresh, so new hires/departures never propagate.

Synced users land on the **free tier** (`User.license_active = False`); the admin
then assigns license seats in the Licensing tab. The sync must never change that.

## Goals

- Rename/relocate the panel to "Integrations" (it covers perms + sync state).
- Surface a synced-user count and last-sync freshness per provider.
- Run a daily user sync for all connected tenants, with a manual "Sync now".
- Guarantee the sync never consumes license seats.

## Non-Goals (YAGNI)

- No separate sync-history table — `SchedulerLog` already logs job runs.
- No configurable sync hour — fixed nightly run.
- No changes to the existing every-N-minute `cloud-sync` metadata job.
- No inline license assignment in the Integrations panel — that stays in the
  Licensing tab.

## Design

### 1. UI relabel (frontend)

- `frontend/src/pages/AdminPage.jsx`: tab `{id:'permissions', label:'Permissions'}`
  → `{id:'integrations', label:'Integrations'}`, and update the render guard
  (`activeTab === 'integrations'`).
- Rename `frontend/src/components/PermissionsAudit.jsx` →
  `IntegrationsPanel.jsx`; update the import in `AdminPage.jsx`. Header text
  becomes "Integrations".
- Each provider card keeps the existing scope checklist and gains:
  - `"N users synced · last run 2h ago"`
  - a status pill (`ok` / `failed`)
  - a **"Sync now"** button.
- License info/assignment is **not** duplicated here; it remains in the
  Licensing tab.

### 2. Persist last-sync state (backend, migration `030_*`)

Add nullable columns to `TenantCredential`:

| Column | Type | Meaning |
|-|-|-|
| `last_user_sync_at` | timestamptz | when the last user sync ran |
| `last_user_sync_total` | int | directory users fetched |
| `last_user_sync_created` | int | new `User` rows created |
| `last_user_sync_updated` | int | existing `User` rows updated |
| `last_user_sync_status` | str | `ok` / `failed` |
| `last_user_sync_error` | text | error detail when `failed` |

`UserSyncService.sync_microsoft_users` / `sync_google_users` write these onto the
matching credential row within the same transaction: counts + `ok` on success,
`failed` + error message on exception. The **live user count** shown in the UI is
a query on the `User` table (`oauth_provider in ('google','microsoft')`); the
last-sync columns are the freshness/health layer.

### 3. Daily sync + manual trigger (backend)

- New `LegalScheduler` job `user-sync`, registered in `start()` with
  `CronTrigger(hour=2, minute=0)` (scheduler tz is already `America/New_York`).
  Add to `AGENT_REGISTRY`, to `agent_map` in `run_agent_manually`, and bump
  `agent_count`.
- `run_user_sync()`:
  1. `_bypass_rls`
  2. select distinct `tenant_id` from active `TenantCredential`
  3. for each tenant, for each connected provider, call the matching
     `sync_*_users`
  4. **per-tenant failure isolation** — a missing/expired token for one tenant
     logs a warning and continues; it does not abort the run
  5. persist last-sync metadata per credential
  6. write a `SchedulerLog` summary
  This mirrors the existing `run_cloud_sync` structure.
- **"Sync now"** reuses the existing scheduler manual-trigger admin endpoint with
  `agent_name="user-sync"`.

### 4. Correctness guardrail (licensing)

The sync **must never set `license_active = True`**. New synced users default to
free tier; seat assignment is admin-only in the Licensing tab. This prevents a
nightly sync from silently consuming seats and over-billing. Enforced in
`UserSyncService` (new users created with the column default; existing users'
`license_active` left untouched) and covered by a regression test.

### 5. API

- Extend `/admin/permissions` per-provider response with:
  `user_count`, `last_sync_at`, `last_sync_total`, `last_sync_status`.
- `frontend/src/api.js`: add `triggerUserSync()` (POST to the manual-trigger
  endpoint) and consume the new permission fields in the panel.

### 6. Error handling

- Per-tenant fail-open in the daily job: the run completes even if individual
  tenants fail; failures are recorded on the credential (`failed` + error) and in
  `SchedulerLog`.
- "Sync now" returns `ok`/`error`, surfaced in the panel.

## Testing

- Sync persists last-sync fields (`ok` path and `failed` path).
- `/admin/permissions` returns `user_count` + freshness fields per provider.
- **Regression A:** one tenant's missing/expired token does not abort the daily
  `run_user_sync` — other tenants still sync.
- **Regression B:** sync does not flip `license_active` on new or existing users.

## Affected files

- `backend/app/models/tenant_credential.py` — new columns
- `backend/migrations/versions/030_*.py` — migration
- `backend/app/services/user_sync.py` — persist last-sync, license guardrail
- `backend/app/services/scheduler.py` — `user-sync` job + registry/map
- `backend/app/routers/admin.py` — `/admin/permissions` fields
- `frontend/src/pages/AdminPage.jsx` — tab rename
- `frontend/src/components/PermissionsAudit.jsx` → `IntegrationsPanel.jsx`
- `frontend/src/api.js` — `triggerUserSync()`
- `backend/tests/` — new/updated tests
