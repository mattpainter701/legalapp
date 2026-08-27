# Live Demo Mode — Revised Plan (2026-08-16)

> Implementation status: complete on draft PR #115. Production remains disabled
> until the synthetic fixture is seeded and the deployment settings in
> `docs/LIVE_DEMO_RUNBOOK.md` are configured.

## Goal

Let a salesperson create an isolated, populated LawHand workspace while standing in a
prospect's office. The prospect visits `/demo`, enters a name, email address, and shared
access code, and receives a full-platform demo with Standard AI, 20 completed AI
operations, and automatic expiry and deletion after 72 hours.

This is a dedicated branch and PR. Self-service trials remain a separate follow-up.

## Product contract

- Public entry route: `/demo`.
- Required fields: full name, email, shared access code.
- Every normal product module is available; Premium AI and live external integrations
  are disabled.
- The workspace is cloned from a maintained synthetic fixture so private-document RAG
  works on the first message.
- A demo permits 20 completed, user-initiated AI operations across chat, plugins, and
  Office surfaces. Internal helper calls within one operation do not consume extra slots.
- The tenant becomes inactive at 72 hours and is purged by the hourly cleanup job.
- Demo mode is off by default. The endpoint returns 404 while disabled.

## Decisions revised after review

### Clone and purge use one registry, not one identical allowlist

The demo service owns a registry describing every tenant-scoped table. Each entry has
independent `clone` and `purge` policies, dependency order, key remapping rules, and any
file cleanup hook.

- `purge=true` is required for every table with a `tenant_id` column.
- `clone=true` is a deliberately small subset containing only synthetic demo content.
- Credential, OAuth, Stripe, API-key, SMB, Teams-link, access-log, scheduler-log, and
  error-log tables are never cloned, but tenant-scoped instances are still purged.
- A metadata-coverage test fails whenever a new tenant-scoped table lacks a registry
  decision.
- Clone tests assert that no fixture primary key or foreign key remains in the new
  tenant. This covers matter, contact, conversation, document, chunk, task, invoice,
  event, user, and JSON-contained identifiers—not only `tenant_id` and `user_id`.
- Document files are copied to tenant-specific storage and `storage_path` is rewritten.
  Purging a demo must never remove fixture files.

The clone set is therefore a subset of the purge set:

```text
all tenant tables == purge registry
safe synthetic content ⊂ purge registry == clone registry
```

### Quota is an atomic reservation ledger

Counting `UsageRecord` rows before a request is racy because those rows are created after
provider work. A normalized `DemoSession` record tracks `quota`, `reserved`, and `used`.

1. Before provider work, atomically reserve one slot with an `UPDATE ... WHERE
   used + reserved < quota RETURNING ...` operation.
2. Commit the reservation before calling the provider.
3. Settle it to `used` only after the user-visible operation succeeds.
4. Release it after a terminal provider failure.
5. Use an idempotency key so retries cannot double-charge a slot.

The quota gate is called from every user-initiated LLM entry point. Background memory,
retrieval-planning, and guardrail calls inherit their parent operation and do not reserve
additional slots.

### Public rate limiting and active-session creation are atomic

`POST /api/demo/session` has no user identity before it creates the session, so it does
not receive the authenticated per-user limit. It is added explicitly to `AUTH_LIMITS`
for a five-attempt, 15-minute source-IP window.

The `DEMO_MAX_ACTIVE` check and tenant/session creation run under a PostgreSQL advisory
transaction lock. Concurrent requests cannot both observe an available slot.

### Passcode rotation requires a process restart

The shared access code remains environment configuration, but application settings are
cached at startup. Rotation requires updating the secret and restarting the API
processes. It does not require a code change, but it is not a hot reload.

### Prospect PII is not retained in the global operator audit

Name and email exist in the disposable user/session records and are purged. The global
operator audit stores only action, result, demo session/tenant identifiers, fixture
version, and its standard request metadata. Retaining prospect contact details for sales
follow-up requires a separate consented CRM workflow and retention policy.

## Data model

### `Tenant.expires_at`

Add nullable `Tenant.expires_at`. `require_active_tenant` rejects an expired tenant on
every normal authenticated request. The generic column is intentionally reusable by the
future trial flow.

### `DemoSession`

One row per disposable demo tenant:

- `id`, `tenant_id` (unique), `fixture_tenant_id`, `fixture_version`
- `prospect_name`, `prospect_email`
- `created_at`, `expires_at`, `purged_at`
- `quota`, `reserved`, `used`
- `status`: `provisioning`, `active`, `expired`, `purging`, `purged`, `failed`

The row is tenant-scoped and included in purge verification. Operational purge outcomes
are retained only as sanitized operator-audit events after the disposable row is removed.

## Configuration

```dotenv
DEMO_MODE_ENABLED=false
DEMO_ACCESS_CODE=
DEMO_FIXTURE_TENANT_DOMAIN=
DEMO_SESSION_TTL_HOURS=72
DEMO_MESSAGE_QUOTA=20
DEMO_MAX_ACTIVE=10
```

When demo mode is enabled, startup refuses to boot unless:

- the access code is at least 16 characters and is not a known placeholder;
- the fixture domain is configured;
- TTL is between 1 and 168 hours;
- quota is between 1 and 100; and
- maximum active sessions is between 1 and 25.

Configuration is read at startup. Secret rotation requires an API restart.

## Demo plan and model routing

Add a non-public `demo` plan using `FULL_PLATFORM_MODULES`, defaulting to `matters`, with
the explicit `demo` billing tier. Add `demo: 200` to `TENANT_DAILY_LIMITS`; do not change
the unknown-tier fallback or the existing `intake_trial` allowance in this PR.

The current unknown-tier behavior and the proposed `intake_trial` 10,000 → 1,000 change
ship as a separately reviewed rate-limit PR after live-tenant impact is measured.

Demo users have `premium_ai_enabled=false`. Premium requests are rejected rather than
silently upgraded. Standard routing must use the configured paid, low-latency alias and
must not depend on a shared free-model pool.

## Provisioning transaction

`POST /api/demo/session` performs:

1. Return 404 when disabled or incompletely configured.
2. Compare the access code using `secrets.compare_digest`.
3. Apply the explicit IP rate limit.
4. Acquire the demo-provisioning advisory transaction lock.
5. Validate the fixture is synthetic and contains no credentials, OAuth tokens, Stripe
   identifiers, API keys, SMB agents/shares, or live integration bindings.
6. Enforce the active-session cap under the same lock.
7. Create the demo tenant, `DemoSession`, and admin user; set `Tenant.expires_at`, plan
   `demo`, `billing_tier=demo`, and `premium_ai_enabled=false`.
8. Clone registry-approved data, remapping every primary/foreign/embedded identifier and
   copying document files into the demo tenant's storage root.
9. Provision RBAC and mint existing hardened access/refresh cookies.
10. Commit and write a sanitized operator-audit event.

Provisioning is all-or-nothing. A failed clone leaves no active tenant and no usable
session cookies.

## Expiry and purge

At request time, `require_active_tenant` rejects `expires_at <= now` with 403.

The hourly scheduler job:

1. Selects only rows joined through `DemoSession` with an expired timestamp and a demo
   domain prefix; the fixture tenant is always excluded.
2. Marks the tenant inactive and the session `purging` before destructive work.
3. Cancels or waits out tenant durable jobs and prevents new tenant work from starting.
4. Deletes tenant files using registry cleanup hooks.
5. Deletes every `purge=true` table in reverse dependency order.
6. Verifies zero rows across the entire purge registry.
7. Deletes the tenant and demo-session rows only after verification succeeds.
8. Emits a sanitized success/failure operator-audit event.

An expired non-demo tenant is logged as an anomaly and never deleted. Purge is protected
by the existing scheduler advisory-lock mechanism and is safe to retry.

## Frontend

- Add public `/demo` and `DemoLoginPage` matching the login visual language.
- After successful session creation, call `AuthContext.login()` to resolve `/auth/me` and
  redirect to the plan's default route.
- Add a persistent banner: `Demo session — 7 of 20 AI operations used · expires in 61h`.
- `/auth/me` returns an optional demo block with expiry, quota, reserved, and used values.
- Hide or disable Premium and live-integration controls in demo tenants. The demo script
  explicitly identifies external integrations that are shown only via recorded material.

## Required tests

1. Demo A cannot read demo B, a real tenant, or the fixture tenant.
2. Every tenant-scoped metadata table has an explicit purge registry entry.
3. Clone copies only `clone=true` tables and carries no fixture IDs afterward.
4. Credentials, OAuth tokens, Stripe/API keys, SMB, Teams links, and live bindings never
   clone.
5. Document files are copied to a demo-specific path; purge leaves fixture files intact.
6. Disabled demo mode returns 404; weak enabled configuration fails startup.
7. Wrong access code returns 401 and is rate-limited by IP.
8. Concurrent session requests cannot exceed `DEMO_MAX_ACTIVE`.
9. Premium requests are rejected for demo users.
10. Concurrent operation 20/21 attempts allow only one reservation; the loser makes no
    provider call.
11. Failed provider work releases a reservation; successful work settles exactly once.
12. Expired tenants fail every authenticated request.
13. Purge covers clone-excluded but demo-generated rows such as usage/error/access logs.
14. Purge refuses non-demo and fixture tenants and detects an intentionally missed row.
15. Non-demo tenants retain their existing plan, quota, expiry, and response behavior.

## Delivery sequence

Each item is a logical commit:

1. `DEMO-01`: migration/model for `Tenant.expires_at` and `DemoSession`; expiry gate.
2. `DEMO-02`: demo plan, explicit rate-limit tier, config, and startup validation.
3. `DEMO-03`: purge-complete registry and metadata coverage tests.
4. `DEMO-04`: safe clone implementation, identifier graph, file-copy hooks, fixture
   validator, and seed tooling.
5. `DEMO-05`: session endpoint, advisory lock, RBAC, cookies, and sanitized audit.
6. `DEMO-06`: atomic quota reservation/settlement across every LLM entry point.
7. `DEMO-07`: expiry transition and verified hourly purge.
8. `DEMO-08`: `/demo`, banner, `/auth/me` data, demo-specific disabled controls.
9. `DEMO-09`: synthetic fixture content and salesperson runbook.

## Follow-ups kept out of this PR

- Self-service 14-day trials and paid conversion. Existing `trial_ends_at` is written but
  not enforced; the follow-up reuses `Tenant.expires_at`.
- The unknown-billing-tier fail-closed change and `intake_trial` daily-limit reduction.
- Consented CRM capture/retention for demo prospects.
