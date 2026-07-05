# Backend/API 500 Review — Root Cause & Systemic Fix

**Date:** 2026-07-05
**Scope:** All 48 routers (~32k lines) + 13 services with commits, plus live
production `error_logs` and `pg_policies` on the hypervisor.
**Trigger:** Recurring "Internal server error" responses across the frontend.

---

## TL;DR

Every production 500 in the last 7 days traces to **one root cause**: the RLS
tenant context is bound as a **transaction-local** Postgres GUC, and every
`await db.commit()` silently drops it. Any DB work after a commit runs with no
tenant context, so RLS filters out the tenant's own rows and the handler blows
up (`Could not refresh instance`, `NoResultFound`) or hard-errors
(`unrecognized configuration parameter`, `new row violates row-level security
policy`).

The intake (`acbbe64`) and matters (`d2e7851`) fixes were correct but were
per-route patches of a **systemic** bug. The codebase has **283 commit sites
across 42 routers**; the review confirmed **90+ vulnerable endpoints** that
have the exact same latent 500. Per-route patching is whack-a-mole; the fix
must be structural (Section 5).

---

## 1. Root cause mechanism

`backend/app/database.py`:

- `set_tenant_context()` runs
  `set_config('app.current_tenant_id', :tid, true)` — the third argument
  `true` makes the setting **transaction-local**.
- `get_db()` binds the tenant context **once**, at session creation, inside
  the session's first transaction.
- SQLAlchemy async sessions use "autobegin": each `commit()` ends the
  transaction; the next statement silently opens a **new** transaction — with
  no tenant GUC.

Consequences after any `await db.commit()` in a request:

| Post-commit operation | Failure mode | HTTP result |
|-|-|-|
| `db.refresh(obj)` | RLS hides the row → `Could not refresh instance` | 500 |
| `.scalar_one()` reload | `NoResultFound` | 500 |
| SELECT via strict policy (contacts/tasks/communication_logs/leads) | `unrecognized configuration parameter "app.tenant_id"` | 500 |
| INSERT/UPDATE | RLS `WITH CHECK` fails → `InsufficientPrivilegeError` | 500 |
| SELECT via hardened policy | silently returns **empty/None** | wrong data, no error |

The last row is the most insidious: some endpoints don't 500, they quietly
return empty lists after a commit.

Note: `enable_rls_bypass()` (auth flows) sets `app.rls_bypass` the same
transaction-local way — it has the identical drop-on-commit behavior.

## 2. Production evidence (error_logs, last 7 days)

Queried `error_logs` on the hypervisor (18 errors, **all** in this bug class
except one Pydantic serialization bug):

| Endpoint | Error | Class |
|-|-|-|
| `/api/intake/dashboard/calls` (x4, Jul 5) | `Could not refresh instance '<CommunicationLog …>'` | post-commit refresh (fixed in acbbe64) |
| `/api/matters` (Jul 5) | `Could not refresh instance '<Matter …>'` | post-commit refresh (fixed in d2e7851) |
| `/api/tasks/{id}/view` (Jul 5) | `Could not refresh instance '<Task …>'` | post-commit refresh — **still unfixed** (`tasks.py:488`) |
| `/api/documents/upload` (Jun 29) | `Could not refresh instance '<Document …>'` | post-commit refresh — **still unfixed** |
| `/api/intake/dashboard/recent-callers`, `/api/contacts` (Jul 3–4) | `unrecognized configuration parameter "app.tenant_id"` | strict RLS policy + missing GUC |
| `/api/intake/dashboard/zoom-phone/sync` (Jul 4) | same + `new row violates row-level security policy for table "communication_logs"` | INSERT after context drop (pre-acbbe64) |
| `/api/conversations/{id}/attachments` (Jun 29) | Pydantic: `ChatAttachmentResponse.id` expects str, got UUID | unrelated serialization bug |

## 3. Strict RLS policies (secondary root cause)

95 live policies; **4 legacy policies** call `current_setting('app.tenant_id')`
**without** the `missing_ok` flag, so they raise `UndefinedObjectError` when the
GUC is absent instead of failing closed (empty result):

| Table | Policy | Created in |
|-|-|-|
| `contacts` | `contacts_tenant_isolation` | migration 018 |
| `tasks` | `tasks_tenant_isolation` | migration 019 |
| `communication_logs` | `commlogs_tenant_isolation` | migration 020 |
| `leads` | `leads_tenant_isolation` | migration 020 |

These 4 tables are exactly where the `unrecognized configuration parameter`
500s came from. They need re-creation with
`current_setting('app.tenant_id', true)` + `NULLIF(..., '')::uuid` (the
"residual NULLIF hardening" already noted in project memory).

## 4. Vulnerable endpoint census

Four parallel review agents scanned every commit site in every router and
commit-bearing service. Legend: **VULNERABLE** = DB activity follows a commit
with no `set_tenant_context` re-bind; **SAFE-REBIND** = already patched;
**SAFE** = nothing touches the DB after commit.

### Batch A — matters, intake_dashboard, platform_llm, chat

| File | Vulnerable | Notes |
|-|-|-|
| `matters.py` | **9** | update_matter:1074, add_assignment:1186, set_active_working:1236, add_note:1364, update_note:1410, update_budget:1570, create_retainer:1695, drawdown_retainer:1781, email_matter_client:2114, provision_matter_cloud_folder:2322, sync_matter_cloud_folder:2593 (worst — refresh + cloud sync + response builder all unscoped) |
| `intake_dashboard.py` | 0 | fully patched by acbbe64 |
| `platform_llm.py` | 0 (1 verify) | platform tables, no tenant RLS — `add_provider_key:1227` refresh after commit is benign if `llm_provider_keys` has no policy |
| `chat.py` | 0 | responses built from in-memory objects (`expire_on_commit=False`) |

### Batch C — estates, domestic, platform, trust_accounting, mediation, tasks

| File | Vulnerable | Notes |
|-|-|-|
| `estates.py` | **15** | every sub-resource create/update refreshes after commit (events, fiduciaries, beneficiaries, assets, liabilities, distributions, deadlines, accounting entries) |
| `domestic.py` | **12** | same pattern: parties, children, custody, orders, payments, deadlines, events, calculations |
| `trust_accounting.py` | **8** | plus compounded: `reconcile_trust_account` commits at :470 then INSERTs a `TrustReconciliation` unscoped → WITH CHECK 500 |
| `mediation.py` | **10** | sessions, parties, invites, assets, approve/send, documents, proposals |
| `tasks.py` | **6** | includes `mark_task_viewed:488` — the exact 500 seen in prod today; also `notify_task_created/updated(db,…)` called post-commit |
| `platform.py` | 0 (2 verify) | platform-operator tables; no tenant context anywhere in file |

### Batch B — integrations, billing_extended, admin, auth, plugins

**28 vulnerable commit sites.** Highlights:

| File | Vulnerable | Notes |
|-|-|-|
| `billing_extended.py` | **10** | create_time_entry:150, **start_timer:309, stop_timer:379** (brand-new timer feature 500s on use), update_time_entry:469, create_expense:536, update_expense:630, generate_invoice:829 (+`_load_invoice_response` chain), update_invoice:1114+1130, create_payment:1196 |
| `plugins.py` | **10** | litigation create/update matter, conflict-check, matter events, renewals, entitlement/setup/profile upserts; `:624`'s failure is swallowed by a bare `except: pass` — invisible in prod |
| `admin.py` | **5** | get/update tenant settings:549/590, resolve_error:993, **patch_user:1410 (every user edit 500s)**, invite_user:1483 (refresh + unscoped `tenant_credentials` query → notification silently unrouted) |
| `auth.py` | **2** | **register:930 and signup_with_plan:1017** — `enable_rls_bypass` dropped by commit, then `refresh(user)` + `provision_tenant_rbac(db,…)` run with no context. Matches the known RBAC-provisioning memory note. |
| `integrations.py` | 1 | cloud_init_retry:1690 — post-commit `select(Matter)` silently sees zero matters, reports `matters_checked: 0` |

OAuth callbacks (microsoft/google) are safe only **incidentally**: their
post-commit chain touches `tenants` (no RLS) and `_issue_access_token`
happens to rebind first — fragile ordering worth an explicit rebind.

### Batch D — remaining 26 routers + 13 services

**34 vulnerable commit sites.** Highlights:

| File | Vulnerable | Notes |
|-|-|-|
| `esignature.py` | **4** — most acute file | create:177, send:241, void:264, complete:363 — every state-changing endpoint re-queries via `_load_request`/`db.execute` right after commit with zero rebind anywhere in the file → false 404s after successful mutations; `_to_response` adds a second unscoped query layer and uses bare `scalar_one()` |
| `client_portal.py` | 3 | post message:331, upload doc:422, create invite:551 (all `refresh`) |
| `intake.py` | 3 | create lead:164 (+`_lead_to_response` double query), update lead:202, convert-to-matter:253 |
| `smb.py` | 4 | agent status:311, create share:365, update share:407, matter binding:483 (fresh execute) |
| `qbo.py` | 2 | update settings:360, item mapping:515 |
| `billing.py` | 2 | checkout session:89, portal session:140 (both `refresh(tenant)`) |
| `calendar.py` | 2 | create:356, update:405 scheduled events |
| `communications.py` | 2 | create:94, update:143 |
| `contacts.py` | 2 | create:111, update:187 |
| `document_templates.py` | 3 | create:101, update:157, render:248 |
| `matter_documents.py` / `matter_parties.py` | 2+2 | create/update pairs |
| `teams.py` | 2 | channel link:162, bulk notification settings:243 (loop of refreshes) |
| `roles.py` | 2 | create:77, update:95 |
| `firm.py` | 1 | branding update:97 (`refresh` + follow-up branding query) |
| `external_imports.py` | 1 | :355/:359 — `refresh(import_run)` runs after either commit branch |
| `matters_correspondence.py` | 1 | rules update:227 |
| `prompt_admin.py` | 1 | override update:153 |
| `dev.py` | 1 | dev login:129 (dev-only; verify bypass role) |

Services:

- `cloud_sync.py` — **vulnerable, silent**: `sync_all()` binds tenant once, then each provider sync (`google_drive`, `gmail`, `onedrive`, `sharepoint`, `outlook`) commits internally without re-binding. Every provider after the first runs unscoped; failures are swallowed as generic per-provider warnings. Explains partial/empty cloud syncs.
- `user_sync.py` (service) — `_save_sync_state` writes after the entry-point commit with no rebind (:147→149, :281→283).
- `mcp_product.py:101` — `refresh(product_key)` after commit.
- `correspondence_capture.py` and `email_agent.py::_auto_log_and_task` — **SAFE, correct reference implementations** (they re-bind with comments citing this exact bug class).
- `documents.py::_commit_and_restore_tenant_context` — an existing helper that already encodes the safe pattern; only documents.py uses it.

**Silent-failure items — resolved against production `pg_policies`:**

- `recurring_billing.py` — **CONFIRMED BUG (silent)**: `matters`, `invoices`,
  `time_entries`, `expenses` all have RLS with `FORCE`; the job never sets
  tenant context, so `generate_recurring_invoices()` sees zero matters and
  silently generates **no recurring invoices, ever**. Needs a per-tenant loop
  (enumerate tenants, `set_tenant_context` per tenant, process, commit,
  re-bind).
- `billing.py` Stripe webhooks — cleared: `tenants` has **no RLS**
  (`relrowsecurity = f`), so `_find_tenant_by_customer` works. The subsequent
  writes update the in-memory `tenant` row (`tenants` unprotected) — OK.
- `scheduler.py::_log_start` — cleared: `scheduler_logs` has no RLS.
- `platform_llm.py:1227` — cleared: `llm_provider_keys` has no RLS.
- `dev.py:129` — dev-only router; low priority either way.

## 5. Systemic fix (recommended)

**Do not patch 90+ call sites.** Make the tenant context survive transaction
boundaries automatically, in one place: `get_db()`.

### 5a. Auto re-bind on every transaction begin

Attach a SQLAlchemy `after_begin` session event when the request has a tenant.
It fires each time the session opens a (new) transaction — including the
autobegin after every `commit()` — and re-issues the `set_config` before any
route code runs a statement:

```python
# backend/app/database.py
from sqlalchemy import event, text

async def get_db(request: Request = None) -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        tenant_id = getattr(request.state, "tenant_id", None) if request else None
        if tenant_id:
            tid = str(UUID(str(tenant_id)))

            @event.listens_for(session.sync_session, "after_begin")
            def _rebind_tenant(sync_session, transaction, connection):
                connection.execute(
                    text(
                        "SELECT set_config('app.current_tenant_id', :tid, true),"
                        "       set_config('app.tenant_id', :tid, true)"
                    ),
                    {"tid": tid},
                )
        try:
            yield session
        ...
```

Details that matter:

- The listener is **per-session-instance**, so it dies with the request — no
  cross-request leakage, no engine-level state.
- `set_tenant_context()` stays for explicit calls (`get_current_user`, portal
  context, background jobs); calling it redundantly is harmless.
- Routes where `get_current_user` derives tenant from the JWT but the
  middleware didn't set `request.state.tenant_id` (auth-prefixed paths) keep
  the current behavior — those use `enable_rls_bypass` per-transaction and
  must re-invoke it after any commit (auth.py findings, Batch B).
- `clear_tenant_context()` semantics are preserved within a transaction; a
  later transaction re-binds, which matches the request's declared tenant —
  cross-tenant work must use its own session (already the rule).
- **Do not** switch to session-level GUCs (`set_config(..., false)`): pooled
  connections would leak tenant context across requests. Transaction-local +
  re-bind-on-begin is the safe shape.

### 5b. Harden the 4 strict policies

One migration recreating `contacts_tenant_isolation`,
`tasks_tenant_isolation`, `commlogs_tenant_isolation`,
`leads_tenant_isolation` with
`tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`
(match the pattern used by migration 057/069 hardening). This converts
"hard 500 when GUC missing" into "fail closed, zero rows".

### 5c. Regression test

Add a test that (1) opens a request-scoped session via `get_db` with a tenant,
(2) commits, (3) asserts `current_setting('app.current_tenant_id')` still
returns the tenant in the next statement, and (4) `db.refresh()` on an
RLS-protected row succeeds post-commit. This pins the contract so future
refactors can't regress it.

### 5d. Cleanup (optional, after 5a ships)

The per-route `set_tenant_context(db, …)` re-binds added in acbbe64/d2e7851
become redundant but are harmless; remove opportunistically.

## 5.5 Census totals

| Batch | Vulnerable | Safe-rebind | Safe (no follow-up) |
|-|-|-|-|
| A (matters, intake, platform_llm, chat) | 9 | 4 | 24 |
| B (integrations, billing_ext, admin, auth, plugins) | 28 | 6 | 35 |
| C (estates, domestic, platform, trust, mediation, tasks) | 51 | 7 | 24 |
| D (26 small routers + services) | 34 | 6 | ~35 |
| **Total** | **~122** | 23 | ~118 |

Dominant pattern (~100 of 122): `await db.commit()` immediately followed by
`await db.refresh(obj)` on an RLS table. Note `expire_on_commit=False` means
most of these `refresh()` calls are **redundant anyway** — the in-memory
object already has the committed state (server defaults/DB triggers being the
exception).

## 6. Other 500 classes found (not tenant-context)

### Confirmed silent failures (worse than 500s — no error, wrong behavior)

- **Stripe payment reconciliation is broken end-to-end** —
  `billing_extended.py:1453` `/api/billing/webhooks/stripe`: webhooks carry no
  JWT, so no tenant context is ever bound; `_handle_payment_intent_succeeded/
  _failed` query `invoices`/`payments` (forced RLS) → zero rows → logs
  "invoice not found" and returns 200. Payments made via Stripe payment links
  are never marked paid. Fix: resolve the tenant from the payment-intent
  metadata (or invoice number) with a system context, then
  `set_tenant_context` before the queries. (Verified directly: no
  `set_tenant_context` anywhere in the webhook call path.)
- **Recurring invoices never generate** — `recurring_billing.py` (see 4D):
  scheduled job has no tenant context against forced-RLS tables.
- **Cloud sync partially no-ops** — `cloud_sync.py::sync_all` loses context
  after the first provider's internal commit; providers 2–5 run unscoped with
  errors swallowed as warnings.
- `auth.py:1052` — login with an email that exists in multiple tenants
  silently picks the most-recently-created user (`order_by created_at desc
  limit 1`) — potential wrong-tenant login.

### Ordinary bugs

- `chat.py` attachments: `ChatAttachmentResponse.id` Pydantic type mismatch
  (UUID vs str) — seen in prod Jun 29.
- `billing_extended.py:1276` — `scalar_one_or_none()` result used unchecked →
  AttributeError 500 instead of 404.
- `plugins.py:626-628` bare `except Exception: pass` (and
  `_build_plugin_cloud_context`'s `except: return ""`) mask RLS failures —
  remove or log at error level.
- `auth.py:508/524, 725/731` — OAuth token exchange / id-token parsing
  unwrapped → provider hiccup becomes a raw 500 instead of a clean 4xx.
- `admin.py:502` — `update_billing` catches only `stripe.StripeError`;
  network timeouts propagate as 500.
- `matters.py:2094`: `EmailService().send_email()` unwrapped — provider
  exception → raw 500.
- `matters.py:2578`: `initialize_matter_folders()` unwrapped in
  `sync_matter_cloud_folder` (its sibling in `provision_…` is wrapped).
- `trust_accounting.py:1092`: bare `scalar_one()` on tenant lookup in
  statement PDF branch → `NoResultFound` 500 instead of 404.
- Read-then-write races in intake task upserts (data integrity, not 500).

## 7. Priority order

1. **5a** — auto re-bind in `get_db` (kills ~122 latent 500s in one change).
   Note: auth `register`/`signup_with_plan` also need `enable_rls_bypass`
   re-invoked after their first commit — the `after_begin` listener only
   restores tenant context, not the bypass GUC, and signup paths have no
   `request.state.tenant_id` (middleware skips `/api/auth/*`). Handle those
   two routes explicitly.
2. **Stripe payment webhook tenant resolution** (Section 6) — payments are
   currently never reconciled; revenue-affecting.
3. **`recurring_billing.py` per-tenant loop** — recurring invoices currently
   never generate; revenue-affecting.
4. **5b** — policy hardening migration for the 4 strict policies (kills the
   `UndefinedObjectError` class; future gaps fail closed instead of 500).
5. **`cloud_sync.py`** — rebind per provider (or move rebind into each
   `sync_*` after internal commits).
6. **5c** — regression test.
7. Chat attachment Pydantic bug (one-liner, seen in prod).
8. Section 6 stragglers as routine cleanup.

## 8. Method / provenance

- Root cause verified by reading `database.py`, `middleware/tenant.py`, and
  the diffs of acbbe64/d2e7851.
- Production evidence from `error_logs` and `pg_policies` on the hypervisor
  (read-only queries, 2026-07-05).
- Endpoint census by four parallel review agents (Sonnet), one per router
  batch; VERIFY items re-checked by hand against live `pg_policies` and the
  actual code (Stripe webhook path confirmed manually).
- Line numbers are as of commit `daea13a`.
