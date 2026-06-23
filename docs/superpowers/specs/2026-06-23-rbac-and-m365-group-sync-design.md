# RBAC Core + Microsoft 365 Group Sync — Design

Date: 2026-06-23
Status: Approved (design); pending implementation plan

## Problem

Today the app (Clarity) has only a coarse, hardcoded permission model. `User.role`
is a free-text column but the code honors exactly four values — `admin`,
`accountant`, `user`, `client` — and `access_control.normalize_role` silently
collapses anything else to `user`. Permission decisions live in ~40+ scattered
checks (`user.role != "admin"`, `require_admin`, `require_finance_admin`,
`module_guard`). There is no concept of firm job roles (partner, attorney,
paralegal, secretary) and no way to derive roles from the firm's existing
Microsoft 365 directory groups.

Module entitlement is currently **tenant/plan-level** (every licensed user in a
firm gets the firm's modules); per-user/per-module licensing is explicitly
deferred (see Out of Scope).

## Goals

1. Let a firm admin define custom **roles** that carry a checklist of
   **capabilities** (a real, if modest, RBAC system).
2. Assign roles to users; a user may hold multiple roles (capabilities union).
3. Map **Microsoft 365 groups → roles** so role assignment can be driven from
   the firm's existing directory, with manual overrides that always win.
4. Make directory sync **strictly non-destructive**: it annotates roles only and
   never owns user accounts. A dropped/failed/empty integration is a no-op.

## Non-Goals (Out of Scope / Backlog)

- Google Workspace group sync (no Workspace tenants yet).
- Per-attorney / per-module billing (modules stay per-firm add-ons for now).
- Specialty (`practice_areas`) ↔ module suggestion or auto-entitlement.
- On-login automatic sync (manual "Sync now" only in v1).
- Per-module scoping of capabilities (e.g. "manage_matters but only Estate").

## Phasing

The two pieces are sequential because a group cannot be mapped to a
permission-bearing role until such roles exist.

- **Phase 1 — RBAC core.** No directory dependency. Valuable standalone.
- **Phase 2 — M365 group sync.** Layers on Phase 1.

## Phase 1 — RBAC Core

### Data model

- `roles` — tenant-scoped role registry.
  `(id, tenant_id, name, description, capabilities (JSON list of strings),
  is_system (bool), created_at, updated_at)`.
  `is_system` marks seeded built-ins that cannot be deleted.
- `user_roles` — assignment join.
  `(user_id, role_id, source)` where `source ∈ {manual, group_sync}`.
  - A user may hold multiple roles; effective capabilities = **union**.
  - `manual` rows are admin-set and are **never** touched by sync.
  - `group_sync` rows are the only ones the sync reconciles.
- **Capability catalog** — a fixed, code-level enum (NOT a DB table), rendered
  as checkboxes in the UI. Initial set (~10–15):
  `manage_users, manage_roles, manage_billing, view_billing, manage_matters,
  manage_intake, manage_documents, manage_integrations, admin_settings,
  use_premium_ai`. Extendable in code.

### Migration / compatibility with the existing tier

- Seed four **system roles** per tenant from the current four values, with
  equivalent capability sets:
  - `Administrator` → all capabilities (incl. `admin_settings`, `manage_roles`).
  - `Accountant` → finance caps (`view_billing`, `manage_billing`).
  - `User` → baseline caps.
  - `Client` → client-portal baseline (kept separate; client login path
    unchanged).
- Backfill `user_roles(source='manual')` for every existing user from their
  current `user.role`.
- The legacy `user.role` column **stays** as a derived/compat field during the
  migration so nothing breaks mid-refactor. Checks move to capabilities
  incrementally; the column can be dropped in a later cleanup once no check
  reads it.

### Enforcement

- New helpers:
  - `user_has_capability(user, cap) -> bool` — unions capabilities across the
    user's roles.
  - `require_capability(cap)` — FastAPI dependency, 403 on miss.
- Migrate scattered checks incrementally:
  - `require_admin` → `require_capability("admin_settings")` (or a more specific
    cap per route).
  - `require_finance_admin` → `require_capability("view_billing" / "manage_billing")`.
  - inline `user.role != "admin"` → the matching capability check.
- The login JWT carries the user's **effective capability list**, minted from
  `user_roles` at login — middleware checks stay claim-based and cheap, mirroring
  today's `module_guard` pattern.
- `module_guard` (tenant-plan module entitlement) is **unchanged**.

### Admin UI (Phase 1)

- New **"Roles"** tab in the existing `AdminPage` tab set:
  - Roles list → create/edit role (name, description, capability checkboxes).
    System roles are visible but not deletable.
  - Per-user role assignment (multi-select) in the existing Users table,
    replacing the current 3-way `user → accountant → admin` cycle toggle.

### Testing (Phase 1)

- Capability union across multiple roles.
- `require_capability` allow/deny (200 vs 403).
- Migration seeds the four system roles and backfills `user_roles` correctly.
- Last-admin guard: cannot remove the final `admin_settings`-capable user.

## Phase 2 — Microsoft 365 Group Sync

### OAuth scope

- Add `GroupMember.Read.All` (or `Directory.Read.All`) to the Microsoft OAuth
  scopes. Existing connections must re-consent to gain it; surface this in the
  integrations UI.

### Data model

- `group_role_mappings` — `(id, tenant_id, ms_group_id, ms_group_name, role_id,
  created_at)`.
  - One group may map to multiple roles; multiple groups may map to one role.

### Flow

1. **Pull groups:** admin clicks "Sync groups" → Graph `/groups` (tenant group
   list) cached as `(id, displayName)` for the mapping UI.
2. **Map:** admin authors `group_role_mappings` rows in the UI.
3. **Apply:** for each user, resolve M365 group memberships
   (`/users/{id}/memberOf` or batched) → derive the set of mapped roles →
   reconcile `user_roles` rows where `source='group_sync'` **only**.

### Non-destructive rules (hard requirements)

1. Sync **never creates or deactivates user accounts** — annotation only.
2. A failed, errored, or empty Graph response is a **no-op**: keep last-known
   `group_sync` roles. Only an authoritative, successful sync that actually
   returned a given user's membership may remove that user's `group_sync` roles.
3. `manual` role assignments are never touched by sync.
4. A sync may never strip the **last** `admin_settings`-capable user — guard,
   skip, and surface a warning.
5. Mappings that grant an admin-level capability are flagged in the UI at author
   time (deliberate-action confirmation).

### Trigger

- Manual **"Sync now"** button in v1. On-login additive refresh is a possible
  cheap follow-on, intentionally deferred.

### Admin UI (Phase 2)

- Extend the "Roles" tab with a **"Microsoft Groups"** panel: synced group list,
  group→role mapping rows, and a "Sync now" action.

### Testing (Phase 2)

- Successful sync adds/removes only `group_sync` roles.
- Errored/empty sync is a **no-op** (regression for the highest-risk failure
  mode — silent role/user loss on integration drop).
- `manual` roles survive any sync.
- Last-admin guard holds during sync.

## Security Notes

- Allowing directory groups to grant capabilities is acceptable **because the
  admin explicitly authors each group→role mapping** — it is a deliberate act,
  not "anyone in AD becomes admin." Safeguards: admin-cap mappings are flagged,
  and the last-admin guard prevents lockout.
- Directory sync touches roles/capabilities only — never module entitlement
  (tenant-plan) and never account existence.
