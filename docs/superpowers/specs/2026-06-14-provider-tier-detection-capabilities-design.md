# Provider Tier Detection & Capability Matrix — Design

**Date:** 2026-06-14
**Status:** Approved (design), pending implementation plan
**Scope option:** A — Detection + honest capabilities + better errors. Mixed-provider routing (auth on one provider, storage on another) is explicitly out of scope but the model must not block it.

## Problem

The integration layer assumes every connected Google or Microsoft account is a Workspace / Azure AD admin tenant. Directory sync calls the Google Admin SDK Directory API with `customer="my_customer"`, which only exists for Workspace / Cloud Identity tenants. A personal account (Gmail / Google One, even with a custom domain) returns `HTTP 400 Invalid Input`. The admin UI surfaces this as a red **"users synced · last sync failed"**, implying the integration is broken — when in fact Drive, Gmail, and Calendar all work fine on that account. The product cannot currently tell the user *which tier they are on* or *which features that tier supports*.

## Goals

1. Detect the account tier at connect time: Google `workspace` vs `personal`; Microsoft `azure_ad` vs `consumer`.
2. Resolve, per credential, which features are available on that tier and why.
3. Stop reporting tier-incompatible features (directory sync on Gmail) as hard failures; report them as "not available on this tier."
4. Surface the tier and a capability matrix in the Admin → Integrations UI.
5. Keep the data model open to future mixed-provider routing without building it now.

## Non-Goals

- Routing authentication to one provider and storage to another (future iteration).
- Any change to the OAuth scope sets or consent flow shape.
- Supporting paid-vs-free *sub-tiers* beyond what's needed to gate features (we care about "has a directory tenant" / "does not," not specific Workspace SKUs).

## Detection Strategy

Primary signal is the id_token already decoded in each OAuth admin callback — no extra API call on the connect path.

- **Google:** the `hd` (hosted-domain) claim is present only for Workspace / Cloud Identity accounts and absent for personal Gmail. `hd` present → `workspace` (store `hd` as `account_domain`); absent → `personal`.
- **Microsoft:** the `tid` claim equal to the well-known consumer tenant `9188040d-6c67-4c5b-b112-36a304b66dad` (or a consumer issuer) → `consumer`; any other tenant id → `azure_ad` (store the tenant domain if available).

The `hd` claim cleanly separates Workspace from Gmail, but it does **not** prove the user is a directory admin or that the Admin SDK API is enabled. Therefore the **directory-sync result is the authoritative confirmation** for that one feature: a `workspace` tier with admin scope still resolves directory_sync as `needs_reauth`/`error` if the live call fails (403 / API disabled), per the existing handlers.

**Backfill for existing tenants:** credentials connected before this change have `account_type=unknown`. A best-effort `backfill_*` path detects lazily — for Google, call the userinfo endpoint with the stored access token to read `hd`; for Microsoft, call Graph `/organization`. Triggered from `/status` when `account_type` is unknown (rate-limited to one attempt per status load) and recorded so it isn't repeated. If backfill cannot run (no valid token), tier stays `unknown` and is resolved correctly on the next reconnect.

## Components

### 1. Data model — migration `056_account_tier`

Add nullable columns to `tenant_credentials`:

- `account_type` `VARCHAR(20)` — `workspace` / `personal` / `unknown` (Google); `azure_ad` / `consumer` / `unknown` (Microsoft).
- `account_domain` `VARCHAR(255)` — the `hd` / org domain, or null.
- `account_detected_at` `TIMESTAMPTZ` — when detection last ran.

No new table. Capabilities are resolved per credential row, so a future tenant with both a Microsoft credential (auth) and a Google credential (storage) is already representable.

### 2. Detection service — `backend/app/services/account_detect.py`

- `detect_google(claims: dict) -> tuple[account_type, account_domain]` — pure, reads id_token claims.
- `detect_microsoft(claims: dict) -> tuple[account_type, account_domain]` — pure, reads id_token claims (`tid`, issuer).
- `async backfill_google(token) -> tuple[...]` / `async backfill_microsoft(token) -> tuple[...]` — live userinfo / `/organization` lookups for unknown existing credentials.
- `async persist(db, tenant_id, provider, account_type, account_domain)` — writes the three columns + `account_detected_at`.

Called at the end of each admin OAuth callback (Google + Microsoft) using the claims already parsed for `service_account_email`. Called lazily from `/status` when `account_type` is unknown.

### 3. Capability resolver — `backend/app/services/capabilities.py`

One pure function:

```
resolve(provider: str, account_type: str | None, scopes: str | None,
        last_sync_status: str | None) -> dict[str, Capability]
```

`Capability = {available: bool, status: "ok"|"unavailable"|"needs_reauth"|"error", reason: str}`.

Feature matrix:

| feature | workspace | personal (Gmail) | azure_ad | consumer (MS) |
|-|-|-|-|-|
| directory_sync | available; `needs_reauth` if admin scope missing; `error` if last sync failed | unavailable ("not available on personal Google accounts") | available (same scope/error rules) | unavailable |
| cloud_storage | available | available | available | available |
| email | available | available | available | available |
| calendar | available | available | available | available |
| teams (MS only) | n/a | n/a | available; `needs_reauth` if Teams scopes missing | unavailable |

`unknown` tier resolves conservatively: storage/email/calendar `available`, directory_sync `unavailable` with reason "account tier not yet detected — reconnect to confirm." Pure function → table-driven unit tests.

### 4. Honest sync — `backend/app/services/user_sync.py`

`sync_google_users` / `sync_microsoft_users` consult `capabilities.resolve(...)` before calling the directory endpoint. If `directory_sync.available` is false for the tier, short-circuit and record `last_user_sync_status="not_applicable"` with the capability `reason` as the message — never `"failed"`. `sync_all` treats `not_applicable` as a non-error outcome (no `record_sync_failure`). The existing 400/403 handlers remain for genuine Workspace misconfiguration on a `workspace`/`azure_ad` tier.

### 5. API + UI

`IntegrationStatus` (schema) gains:

- `account_type: str | None`
- `account_label: str | None` — human label derived from type+provider ("Google Workspace", "Personal Google (Gmail)", "Microsoft 365 (Work/School)", "Personal Microsoft Account").
- `account_domain: str | None`
- `capabilities: dict` — the resolver output.

`/status` populates these from the stored columns + resolver, running lazy backfill when type is unknown.

Admin → Integrations (cloud-search tab): render a tier badge per provider and a capability matrix (✓ available / ✗ not on this tier / ⚠ reconnect needed). Suppress the directory-sync failure banner when `last_user_sync_status == "not_applicable"`; show the neutral capability reason instead.

## Data Flow

```
admin OAuth callback
  → decode id_token claims (existing)
  → account_detect.detect_*(claims) → (account_type, account_domain)
  → account_detect.persist(...)
  → existing token storage + post-connect sync

post-connect / scheduled sync
  → capabilities.resolve(...) → directory_sync.available?
        no  → record status="not_applicable" (friendly reason), stop
        yes → call directory API (existing handlers) → ok / needs_reauth / error

GET /status
  → load credential row; if account_type unknown → best-effort backfill_*
  → capabilities.resolve(...)
  → IntegrationStatus{account_type, account_label, account_domain, capabilities, ...}
```

## Error Handling

- Detection from claims never raises; missing/garbled claims → `unknown`.
- Backfill failures are swallowed (logged); tier stays `unknown`.
- Directory sync on an incompatible tier is a *non-error* (`not_applicable`), distinct from `failed`.
- Genuine 400/403 on a compatible tier keeps the existing detailed RuntimeError messages.

## Testing

- **Unit — detection:** sample id_token claim sets → Gmail (`personal`), Workspace (`workspace` + domain), MSA `tid` (`consumer`), Azure AD `tid` (`azure_ad`); empty/garbled → `unknown`.
- **Unit — capabilities:** full table coverage of (provider × account_type) → expected per-feature `{available, status}`; scope-missing and last_sync_status variations for directory_sync and teams.
- **Integration — honest sync:** personal Google sync records `not_applicable`, not `failed`, and `sync_all` does not call `record_sync_failure`. Workspace sync with a 400/403 still records `failed` with the detailed message.
- **Regression (highest-risk new failure mode):** a previously-failing personal-account tenant, after this change, shows `not_applicable` + a true capability matrix on `/status` (the exact bug the user reported).

## Rollout / Backfill

- Migration adds nullable columns; no data backfill required at deploy.
- Existing credentials classify as `unknown` until the next `/status` (lazy backfill) or reconnect.
- No scope/consent changes, so no user reconsent is forced.
