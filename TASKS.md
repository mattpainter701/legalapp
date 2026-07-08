# TASKS.md

## Recent Merge Review And Plan Integration — 2026-07-08 (DONE)

**Goal:** Pull local state current, review the recent unmerged merge stack and
planning branches, integrate the low-risk pieces in a sensible order, and leave
superseded/high-conflict branch work out of `main`.

- [x] Confirm local `main` pull state and identify new remote branches
- [x] Integrate SBOM/AI-BOM inventory tracking from the SBOM planning branch
- [x] Integrate probate, estate, and template automation plans without
      duplicating Sprint 15 or task IDs
- [x] Repair Admin Users/RBAC response contract so assigned role badges reload
      from backend role assignment data
- [x] Preserve legacy accountant role support while fixing admin user contract
- [x] Selectively port Teams reauthorization/notification safety fixes
- [x] Leave old Zoom recordings call-intake router and large add-on UI patch
      unmerged as superseded/high-conflict follow-ups

Summary: `git pull --ff-only` was already current; local `main` remains one
commit ahead of `origin/main`. The recent stack should not be merged wholesale:
it conflicts in `TASKS.md`, `backend/app/main.py`,
`backend/app/routers/admin.py`, `backend/app/routers/integrations.py`, and
`frontend/src/pages/MatterPortfolioPage.jsx`. Integrated the clean SBOM
inventory generator/docs, imported probate planning as backlog `BK12`-`BK17`
instead of a duplicate Sprint 15, fixed `/api/admin/users` to return manual
RBAC `role_ids`/`roles` plus billing/license fields, preserved accountant as a
valid legacy role, and manually ported the Teams scope-preservation and
non-mutating notification fixes around the current PKCE OAuth flow. The old
Zoom recordings router remains skipped because current `main` already has the
newer Zoom Phone call-history/webhook intake path.

## Intake Prod Session Follow-Ups — 2026-07-07 (DONE)

**Goal:** Fix production-session issues reported after login: refresh should not
log users out, call-capture draft cards need a close/delete affordance, Zoom
Phone intake should refresh recent calls without requiring manual sync, intake
tasks should carry caller context and working links, calendar events should name
the task creator and link back to the task, and assigned users should receive an
email notification.

- [x] Trace auth/session refresh behavior
- [x] Add call-capture draft close/delete control
- [x] Improve Zoom Phone intake auto-sync behavior and DB logging confidence
- [x] Fix intake-created task title/link/customer context
- [x] Add creator context and task link to calendar/email notifications
- [x] Validate focused backend/frontend paths

Summary: refresh-after-login was not intentionally strict security; the
frontend still treated the missing legacy localStorage bearer token as logged
out even though production now relies on httpOnly auth cookies. Auth boot now
checks `/auth/me` with cookie refresh before deciding the session is gone.
Call-capture draft cards now have an X close/delete affordance, with a discard
confirmation for non-empty drafts. The intake dashboard now triggers a
throttled Zoom Phone sync on load for connected tenants, while the backend
continues writing imported calls idempotently into `communication_logs`. Staff
intake tasks now prefix the caller name in the task title, include the creator
in the task description, and notification calendar/email payloads include
creator/customer context plus a `/tasks/{id}` link. The frontend now serves
`/tasks/{taskId}` and highlights the linked task. Validation: backend compile
passed; non-DB task notification tests passed; frontend production build
passed; `git diff --check` passed; DB-backed notification/intake regressions
remain locally blocked by Postgres `ConnectionRefusedError` as in prior intake
work.

## Admin Role-Assign Badge Never Updated — 2026-07-07 (DONE)

**Goal:** Investigate user report of "inability to assign perm/roles to
users" in Admin → Users.

- [x] Ruled out backend failure: nginx logs show 3 successful `200`
      responses on `PUT /api/admin/roles/assign/{user_id}` minutes before
      the report
- [x] Ruled out lockout/missing-role data: direct DB query confirms every
      tenant has 1+ users holding `manage_roles`, and no user in any tenant
      has zero `user_roles` rows (migration 068's backfill held)
- [x] Found actual bug: `RoleAssignCell`'s badge label was hard-coded to the
      legacy `user.role` string and never reflected the roles actually
      assigned via the checkbox dropdown — a successful assignment looked
      like a no-op, which is why it appeared broken
- [x] Fixed: badge now derives its label from the user's real assigned role
      names, falling back to the legacy role only when no manual assignment
      exists
- [x] Verified frontend production build passes

Summary: this was a pure UX defect, not a backend bug — assignments were
always succeeding. Fixed in `frontend/src/pages/AdminPage.jsx`
(`RoleAssignCell`).

## Intake Call Draft: Raw Error Page Leak — 2026-07-07 (DONE)

**Goal:** Fix user report of a raw nginx 429 HTML error page appearing inside
the intake dashboard's call draft tab, and the surrounding "receipt trail"
section looking broken as a result.

- [x] Trace root cause: `normalizeApiError()` treated non-JSON proxy error
      bodies (nginx's HTML error pages) as literal message text
- [x] Confirm via nginx logs: a 2026-07-06 429 burst (2,593 hits in seconds,
      same draft IDs) predates and appears to have triggered the earlier
      "stop draft autosave flood" fix; `retryReceipt()` stored that page's
      full HTML as a receipt's `error` with no sanitization or length limit
- [x] Fix `normalizeApiError()` to detect HTML bodies and fall back to a
      short status-based message (friendly text for 429/502/503/504)
- [x] Sanitize receipt errors in `useCallDrafts` (including already-persisted
      ones, on next load) so any prior corrupted receipt self-heals
- [x] Truncate/line-clamp receipt error text in `ReceiptTrail` as a render-
      layer defense
- [x] Verify fix logic against the exact HTML body from the user's report
      (node simulation) and confirm frontend build passes

Summary: root cause was leaking a proxy/gateway's raw HTML error page as a
user-facing message, not a still-live autosave loop — no new 429 flood was
found in the last 24h of nginx logs; the only burst on record predates the
earlier fix by ~5 minutes. The visible "weird" call-draft section was a
stale, persisted receipt from that incident, now self-healed by the
sanitization added at load time. Fixed in `frontend/src/api.js`,
`frontend/src/hooks/useCallDrafts.js`, and
`frontend/src/components/intake/ReceiptTrail.jsx`. No JS test framework
exists in this repo; validated via a standalone node reproduction of the
exact HTML payload plus a passing production build.

## Production Backend/Page Failure Triage — 2026-07-06 (DONE)

**Goal:** Diagnose and fix the current production issue where pages appear not
to load or APIs fail after the latest OAuth hotfix deploy, without shipping the
pending mobile/PWA frontend work until the backend health is understood.

- [x] Check production health, container state, and recent backend/nginx logs
- [x] Identify whether failures are 429 rate limit, auth/session, 500s, or app startup
- [x] Apply the smallest safe fix or operational recovery
- [x] Validate public pages/API health after recovery

Summary: production health and authenticated Microsoft/mobile page APIs were
responding, but persisted error logs showed Google login still failed with
`Google id_token verification failed: No access_token provided to compare
against at_hash claim`. The Google callback now passes the provider access
token into `verify_google_id_token()`, allowing `python-jose` to validate
Google's `at_hash` claim instead of rejecting valid callbacks. Added a focused
regression that signs a Google ID token with a matching `at_hash`. Validation:
20 focused OAuth tests passed, auth router/security helper syntax compile
passed, and the frontend production build passed.

## Mobile/Tablet Frontend Audit + Design Polish — 2026-07-06 (DONE)

**Goal:** Assess whether the installable web app (manifest-based, no service
worker) is sufficient for mobile users, fix critical/high mobile issues, add
the missing favicon, and harmonize off-brand pages with the design system.

- [x] Verdict: current PWA-lite setup is sufficient — installable via
      manifest, shell already has hamburger + bottom tab nav + 44px tap
      targets; no native app or service worker needed for this user base
- [x] Fix `h-screen` → `100dvh` in AppShell (bottom nav cut off by mobile
      browser URL bar)
- [x] Add `viewport-fit=cover` + safe-area padding on mobile bottom nav
      (iPhone home indicator in standalone mode)
- [x] iOS-only 16px input font-size rule (prevents Safari focus auto-zoom)
- [x] Fix CalendarPage/CommunicationsPage `h-screen`-inside-AppShell overflow
      (→ `h-full`)
- [x] Add horizontal-scroll wrappers / `overflow-x-auto` to 16 tables across
      12 files that clipped on narrow screens
- [x] Create missing `favicon.svg` (referenced by index.html but absent);
      add apple-touch-icon + theme-color meta
- [x] Remap ~155 off-brand inline hex colors in InvoicesPage,
      InvoiceDetailPage, TimeTrackingPage, ProfilePage onto the brand palette
- [x] Production build verified (vite build passes)

### Backlog (mobile/design, lower priority)
- [ ] **Code-split the JS bundle (P1):** single 1.65 MB chunk (390 KB gzip) —
      slow first load on cellular. Route-level `React.lazy` or Rollup
      `manualChunks`.
- [ ] **Card-style mobile layouts for dense tables (P2):** horizontal scroll
      is a stopgap; portfolio/invoice/time tables would read better as
      stacked cards under `md:`.
- [ ] **Convert inline-styled pages to Tailwind (P2):** InvoicesPage,
      InvoiceDetailPage, TimeTrackingPage, ProfilePage still use inline
      styles (colors now on-brand); converting to Tailwind brand classes +
      serif headings would finish the visual unification.
- [ ] **PWA maturity (P3):** service worker + explicit update prompt +
      offline shell. Deliberately deferred — offline caching of legal
      documents needs an encryption/privacy design first.
- [ ] **Touch-target audit (P3):** some icon buttons (e.g. 13–15px trash
      icons in table rows) are below the 44px guideline.
- [ ] **Real-device pass (P3):** Calendar drag interactions, Reports charts,
      and Communications three-pane layout on an actual phone/tablet.
- [ ] **Frontend dependency updates (P3):** routine minor bumps + planned
      major migrations (React 19, Vite 7, Tailwind 4) from `npm outdated`.

## Mobile OAuth Callback Duplicate Handling — 2026-07-06 (DONE)

**Goal:** Fix mobile Google/Microsoft login failures where provider callback
URLs can be requested more than once, causing one-time OAuth state/code
handling to surface "Invalid or expired OAuth state" instead of completing or
returning the user to login gracefully.

- [x] Reproduce/confirm production callback failure mode from logs
- [x] Patch OAuth callback/exchange handling for duplicate mobile redirects
- [x] Add focused regression coverage
- [x] Validate backend/frontend auth flow checks
- [x] Commit, push, deploy, and verify production OAuth redirects

Summary: production logs showed mobile Chrome repeatedly requesting the same
Google/Microsoft OAuth provider callback URL after login redirects. The first
callback consumes the one-time CSRF state and the duplicate previously surfaced
`Invalid or expired OAuth state` as a raw API error page, even when the first
Microsoft callback had already produced a frontend exchange code. The auth
router now records a 60-second replay entry bound to the exact provider
`state` + authorization code after a successful callback, waits briefly for
in-flight duplicates, and mints a fresh frontend callback exchange code for
safe duplicate callbacks without reusing the provider authorization code.
Google token exchange failures are now logged with provider status/body for
diagnosis. Validation: focused OAuth replay tests passed, route-client
contract passed with 394 matched frontend API call sites, frontend production
build passed, and auth router syntax compile passed.

## Intake Draft Autosave Hotfix — 2026-07-06 (DONE)

**Goal:** Stop the intake dashboard from creating endless draft autosave
errors when the page is opened or focus moves inside the call-capture form.

- [x] Prevent untouched default call cards from syncing to the backend
- [x] Stop form-level blur from flushing while focus remains inside the form
- [x] Make autosave failures quiet and non-self-reinforcing under 429s
- [x] Fix intake page admin rendering issues found during the hotfix
- [x] Validate frontend build and targeted checks
- [x] Update TASKS.md and CHANGELOG.md with the outcome

Summary: fixed the intake draft loop that could flood nginx's general API
rate limit and make unrelated pages appear broken. `useCallDrafts()` now keeps
the toast callback in a ref so hydrate is stable, skips backend sync for blank
untouched draft cards, suppresses 429 retry/toast storms, avoids duplicate
in-flight saves, and no longer replays dirty drafts during hydration. The
intake capture form only flushes on blur when focus leaves the form, not when
tabbing between fields. The rotation admin panel no longer references
out-of-scope dependencies, and migration `079_error_logs_system_policy` fixes
tenantless `error_logs` inserts under RLS. Validation: frontend production
build passed, migration compile passed, Alembic head is
`079_error_logs_system_policy`, and `git diff --check` passed.

## Intake Call Drafts + Action Receipts — 2026-07-05 (DONE)

**Goal:** Implement the approved intake call draft persistence and action
receipt design from
`docs/superpowers/specs/2026-07-05-intake-call-drafts-design.md`, preserving
the current intake dashboard layout while making in-progress call capture
durable across refresh/navigation and surfacing mutation feedback clearly.

- [x] Add backend draft persistence table, RLS, router endpoints, and tests
- [x] Add frontend draft hook, tab strip, receipts, toasts, and async button
- [x] Wire the active call-capture form to durable drafts without re-laying out
      the dashboard
- [x] Validate keyboard shortcuts, autosave, retry, and failure behavior
- [x] Update TASKS.md and CHANGELOG.md with the outcome

Summary: implemented the approved intake call draft persistence and action
receipt design. Added migration `078_intake_call_drafts`, the
`IntakeCallDraft` model, `/api/intake/drafts` list/upsert/delete endpoints
with server-authored timestamps and current-user tenant scoping, plus focused
CRUD/idempotency/isolation tests. Frontend call capture now runs from
`useCallDrafts()` with immediate localStorage persistence, backend autosave,
draft card switching (`Alt+1..9`) and new draft shortcut (`Alt+Shift+N`),
shared `ToastProvider`, `AsyncButton`, and per-draft `ReceiptTrail` retry
support. The existing dashboard layout is preserved. Validation: backend
compile passed, route-client contract passed with 394 matched frontend call
sites, and frontend production build passed. DB-backed intake tests reached
the local Postgres fixture and then failed with `ConnectionRefusedError`
(`WinError 1225`), so endpoint test execution remains environment-blocked
locally.

## API Reliability Production Deploy — 2026-07-05 (DONE)

**Goal:** Commit, push, deploy, and validate the systemic API/RLS/error
observability fixes on the hypervisor production stack.

- [x] Confirm local and remote Git state are safe for deploy
- [x] Run focused pre-deploy validation
- [x] Commit and push the deployable changes
- [x] Run production env/data guards before restart
- [x] Deploy backend/frontend and validate health/OAuth/data guard
- [x] Update TASKS.md and CHANGELOG.md with deploy result

Summary: committed and deployed `25a9238` (`fix(api): harden tenant context
and error handling`) to the hypervisor production stack at
`https://legalapp.perevagagroup.com`. Local preflight passed: 32 focused
backend tests, route-client contract script, frontend production build, and
staged secret scan. Production env guard passed with `DEV_MODE=false`.
Predeploy backups/counts were created, with the restart-protecting snapshot at
`backups/legalapp-predeploy-20260705T232755Z.dump` and
`backups/legalapp-predeploy-20260705T232755Z.counts.tsv`; postdeploy data
guard passed with `backups/legalapp-postdeploy-20260705T233047Z.counts.tsv`.
Backend/frontend were rebuilt and recreated, Alembic upgraded through
`077_error_logs_nullable_tenant`, local and public health returned ok,
Microsoft/Google OAuth returned 307, cloudflared was active, request IDs were
present on API responses, and docs/dev routes returned 404.

## API Error Observability & UX Hardening — 2026-07-05 (DONE)

**Goal:** Resolve the API/front/backend map findings so production testing
returns supportable errors instead of opaque "Internal server error" messages.

- [x] Add request/error IDs and tenantless/system error logging
- [x] Fail closed on production DB startup failure
- [x] Normalize frontend API errors, including streaming fetch
- [x] Remove or dev-gate localStorage bearer fallback
- [x] Add route/client contract smoke coverage
- [x] Validate focused backend/frontend checks
- [x] Update TASKS.md and CHANGELOG.md

Summary: production API errors now have correlation handles instead of opaque
500s. Added request-id middleware with `X-Request-ID`, included `request_id`
and captured `error_id` in safe HTTP/generic error JSON, persisted request IDs
to `error_logs`, and added migration `077_error_logs_nullable_tenant` so
tenantless/system errors can be logged. Production startup now fails closed on
DB connectivity probe failure. Frontend `api.js` now exports centralized
`normalizeApiError()`, normalizes Axios and streaming fetch failures, preserves
request/error IDs from body/headers, parses validation details, and removes
production localStorage bearer fallback unless explicitly enabled for dev with
`VITE_LEGACY_TOKEN_AUTH=true`. Added `backend/scripts/route_client_contract.py`
and `backend/tests/test_route_client_contract.py`; standalone contract check
currently matches 391 frontend API call sites to backend routes. Validation:
32 focused backend tests passed, route-client script passed, backend compile
passed, and frontend production build passed.

## Assistant Chat Reference Ledger Wrap Bug — 2026-07-05 (DONE)

**Goal:** Fix chat source/reference ledger and message text overflow when
citations or excerpts contain long unbroken strings.

- [x] Patch chat message wrapping styles
- [x] Validate frontend build
- [x] Update TASKS.md and CHANGELOG.md

Summary: chat message bodies, reference status strips, and source ledger
excerpts now break long unspaced strings inside their containers instead of
bleeding across the page. Validation passed with the frontend production
build.

## API / Frontend / Backend Error-Readiness Map — 2026-07-05 (DONE)

**Goal:** Map the frontend API callers, backend routes, tenant/RLS/error
handling, and production observability paths to identify why customer testing
still surfaces generic "Internal server error" messages and what to harden
next.

- [x] Inventory frontend API client + backend route surface
- [x] Review error propagation and user-facing API UX
- [x] Run focused static/test probes for route/auth/migration/API drift
- [x] Write findings and priority remediation plan
- [x] Update TASKS.md and CHANGELOG.md

Summary: wrote `docs/api-front-backend-map-eval-2026-07-05.md`. The app has
511 backend routes and 339 frontend API call sites; route auth coverage,
Alembic head check, and frontend production build passed. Main remaining
production-testing pain is API observability/UX, not another single endpoint:
generic 500 responses have no request/error ID, tenantless/system failures can
skip durable error logging, production startup does not fail closed on DB
connection failure, frontend error handling is page-by-page instead of
normalized, localStorage bearer fallback remains, and there is no route/client
contract gate. Priority plan: request IDs + safe error IDs, tenantless/system
error logging, DB fail-closed startup, shared frontend API error normalizer,
remove/dev-gate bearer fallback, and add route/client/top-workflow smoke tests.

## Assistant Chat Source/Reference Tracking UX — 2026-07-05 (DONE)

**Goal:** Preserve visible source/reference context across assistant chat
initiation, first response, follow-up prompts, and rendered results so users
can track which sources informed each answer.

- [x] Inspect assistant chat prompt/result data flow
- [x] Refactor UI state/rendering for per-turn sources and references
- [x] Validate with focused frontend checks
- [x] Update TASKS.md and CHANGELOG.md

Summary: chat now keeps a per-turn reference context through streaming,
refresh, and conversation reloads. User prompts and assistant answers both
show a compact References strip with matter, upload, firm/cloud, and
CourtListener counts, while the final answer retains a source-neutral
"Sources & References" ledger with responsive columns. Validation passed with
the frontend production build.

## Backend/API 500 Review & Root-Cause Documentation — 2026-07-05 (DONE)

**Goal:** Systematic review of all API routers for the recurring "internal
server error" class (post-commit tenant-context loss → RLS empty reads),
enumerate every vulnerable endpoint, document root cause + systemic fix.

- [x] Confirm root cause mechanism (transaction-local GUC dropped on commit)
- [x] Enumerate all vulnerable endpoints across 48 routers + services (subagents)
- [x] Identify any other distinct 500 classes
- [x] Write review document with systemic fix recommendation
- [x] Update TASKS.md and CHANGELOG.md

Summary: every production 500 in the last 7 days traces to one root cause —
`set_tenant_context` binds a transaction-local GUC that every `db.commit()`
silently drops; post-commit DB work then runs unscoped under RLS. Census via
four parallel review agents found **~122 vulnerable commit sites** across 30+
routers and 3 services. Verified against live `error_logs` and `pg_policies`.
Also confirmed three **silent** failures: Stripe payment-reconciliation
webhook (no tenant context, payments never marked paid), recurring-invoice
job (generates nothing), and cloud sync (providers 2-5 unscoped). Full report
with per-endpoint census and the recommended systemic fix (auto re-bind via
SQLAlchemy `after_begin` in `get_db`, strict-policy hardening migration,
regression test): `docs/backend-500-review-2026-07-05.md`. Fix work is a
follow-up task — no code changed in this review.

## Systemic Tenant-Context Fix (DONE)

Priority order from `docs/backend-500-review-2026-07-05.md` §7:

- [x] `get_db` auto re-bind (`after_begin` listener) + auth register/signup bypass re-invoke
- [x] Stripe payment webhook tenant resolution (revenue)
- [x] `recurring_billing.py` per-tenant loop (revenue)
- [x] Migration hardening 4 strict RLS policies (contacts/tasks/communication_logs/leads)
- [x] `cloud_sync.py` rebind per provider
- [x] Regression test pinning post-commit GUC survival
- [x] Chat attachment Pydantic UUID→str fix

Summary: fixed the systemic post-commit RLS context loss by adding a
per-session SQLAlchemy `after_begin` listener in `get_db` that re-binds both
tenant GUCs on every new transaction, plus explicit auth bypass re-enable for
the two signup routes after their first commit. Added migration
`076_harden_strict_tenant_rls` to harden strict contacts/tasks/
communication_logs/leads policies with `current_setting(..., true)` +
`NULLIF`, converting missing tenant context from hard 500s to fail-closed
empty reads. Stripe webhook reconciliation now resolves/binds tenant context
before forced-RLS invoice/payment queries, recurring billing loops active
tenants with per-tenant context, and cloud sync re-binds around each provider
so internal commits cannot un-scope later providers. The chat attachment UUID
serialization 500 is fixed. Focused validation passed for migrations,
chat-attachment serialization, cloud sync, and revenue tenant-context tests;
the DB-backed RLS contract test is present but skipped locally because
Postgres refused the test connection.

## Matter Create API 500 — 2026-07-05 (DONE)

**Goal:** Fix `POST /api/matters` returning 500 in production after the latest
merges, while preserving existing production matter/customer data.

- [x] Confirm local/prod Git state and inspect current production logs
- [x] Reproduce or cover the failing create-matter path with a focused test
- [x] Patch the matter-create path and validate locally
- [x] Deploy through production data guard if code changes are needed
- [x] Update TASKS.md and CHANGELOG.md with the outcome

Summary: current production logs after the previous restart did not retain a
fresh `POST /api/matters` traceback, but the create route had the same
post-commit RLS failure shape as the Call Intake fix: it committed, then
refreshed/reloaded the created `Matter` after transaction-local tenant context
was cleared. Patched `POST /api/matters` to explicitly bind tenant context at
the start of create and re-bind it after commit before refreshing/reloading the
new matter. Added `backend\tests\test_matters.py` to cover matter creation,
primary assignment/event creation, and the post-commit tenant-context
requirement. Local validation passed: `backend\tests\test_matters.py`,
`backend\tests\test_module_guard.py`, backend compile, and frontend production
build. Deployed commit `d2e7851` after predeploy backup
`backups/legalapp-predeploy-20260705T203919Z.dump` and count snapshot
`backups/legalapp-predeploy-20260705T203919Z.counts.tsv`; rebuilt
backend/frontend, recreated containers, and postdeploy data guard passed with
`backups/legalapp-postdeploy-20260705T204231Z.counts.tsv`. Health, public
health, Microsoft/Google OAuth 307 redirects, cloudflared, closed docs/dev
routes, fresh backend logs, and Alembic head `075_billing_timer_and_qbo_dedupe`
all checked clean.

---

## Call Intake Create Lead Staff Task 500 — 2026-07-05 (DONE)

**Goal:** Fix the Call Intake "create lead + staff task" action returning a
500 after the latest merges, without disturbing production data or unrelated
workflow branches.

- [x] Confirm local/prod Git state and capture the production traceback
- [x] Reproduce or cover the failing action with a focused test
- [x] Patch the intake/task path and validate locally
- [x] Deploy through production data guard if code changes are needed
- [x] Update TASKS.md and CHANGELOG.md with the outcome

Summary: production traceback showed
`sqlalchemy.exc.InvalidRequestError: Could not refresh instance
'<CommunicationLog ...>'` at `create_dashboard_call` after the combined
`create_lead` + `specific_staff` workflow committed. Fixed the route to build
the response from flushed IDs before commit, avoid the unsafe post-commit
`CommunicationLog` refresh, and re-bind tenant context before task
notifications. Added a regression that fails on post-commit communication-log
refreshes and verifies tenant context is present for notifications. Local
validation passed: targeted regression, full
`backend\tests\test_intake_dashboard.py` (24 tests), backend compile, and
frontend production build. Deployed commit `acbbe64` to the hypervisor after
predeploy backup `backups/legalapp-predeploy-20260705T202925Z.dump` and count
snapshot `backups/legalapp-predeploy-20260705T202925Z.counts.tsv`; rebuilt
backend/frontend, recreated containers, and postdeploy data guard passed with
`backups/legalapp-postdeploy-20260705T203245Z.counts.tsv`. Health, public
health, Microsoft/Google OAuth 307 redirects, cloudflared, closed docs/dev
routes, backend logs, and Alembic head `075_billing_timer_and_qbo_dedupe` all
checked clean.

---

## Post-Merge Production Deploy — 2026-07-05 (DONE)

**Goal:** Pull the newly merged changes to local `main`, validate them, and
deploy to the hypervisor only through the data-preserving production path.

- [x] Pull latest `origin/main` into local clean `main`
- [x] Run focused build/test/preflight validations for the merged changes
- [x] Run production env guard and data guard before any container restart
- [x] Deploy to hypervisor if preflight is safe
- [x] Run post-deploy health, OAuth, and data-guard validation
- [x] Update TASKS.md and CHANGELOG.md with deploy result

Summary: pulled `origin/main` from `65dc4ba` to merge commit `4e70405`
(`claude/time-tracking-invoicing-overhaul-snuau0`). Local validation passed:
38 focused backend billing/migration tests, backend compile, and frontend
production build. Hardened the two production env blockers before restart:
local and hypervisor `.env` now have a non-placeholder `SECRET_KEY` and
`DEV_MODE=false`; backups were written as `.env.backup.pre-prod-*` locally and
on the hypervisor. Predeploy data guard created
`backups/legalapp-predeploy-20260705T150448Z.dump` plus count snapshot.
Rebuilt backend/frontend and started the stack without `--remove-orphans`;
postdeploy data guard passed. Production is at `4e70405`, Alembic is at
`075_billing_timer_and_qbo_dedupe`, and the new billing timer/QBO columns plus
partial running-timer index exist. Health checks passed locally and publicly,
Microsoft/Google OAuth returned 307, cloudflared is active, and `/docs`,
`/redoc`, `/openapi.json`, and `/api/dev/users` are closed.

---

## Production-Safe Dev Workflow And Git Cleanup — 2026-07-04 (DONE)

**Goal:** Keep local development/debugging productive while making production
deploys data-preserving by default, then clean Git state so work resumes from a
clean `main`.

- [x] Verify local and hypervisor Git state are on clean `main`
- [x] Prune stale remote-tracking refs and remove safely merged local branches
- [x] Preserve active/unmerged branch state without deleting in-flight work
- [x] Validate the production data guard and health checks still pass
- [x] Update task/changelog records and commit cleanup

Summary: local and hypervisor checkouts are on `main` at
`ae3184d`/`origin/main` before the ledger commit, with clean worktrees except
for this task record. Removed stale clean worktrees
`I:/deepseek/legalapp/legalapp-deploy-main` and
`I:/deepseek/legalapp/legalapp-review-fixes`, deleted merged local branches
`codex/fix-zoom-intake-feed-500`, `codex/recent-merge-fixes`,
`codex/sprint-15-integration-health`, `feat/call-inbox-redesign`, and
`feat/call-intake-standalone`, and deleted merged remote branches
`claude/call-intake-prod-ready-kn4y20`,
`claude/call-intake-task-tracking-j595dh`,
`codex/finish-ux-for-e-sign-tool`, `codex/recent-merge-fixes`, and
`feat/call-inbox-redesign`. Preserved unmerged local branch
`codex/integration-remediation-reconcile` and unmerged remote branches for
future review. Production guard snapshot returned 164 count rows and public
health returned 200.

### Before Prod

- [ ] Rotate remaining production/provider secrets that were exposed during
      incident inspection before treating the install as customer-safe.
- [x] Replace placeholder-shaped `SECRET_KEY` in local `.env` and production
      `.env`; keep local `.env` as the source of truth before copying to the
      hypervisor.
- [x] Set production `DEV_MODE=false`, restart backend, and verify `/api/dev/*`,
      `/docs`, `/redoc`, and `/openapi.json` are not served.
- [x] Keep `scripts/prod_data_guard.sh pre` + `post` in every deploy path; do
      not build/restart production unless a fresh DB backup and row-count
      snapshot exist.
- [ ] Continue feature/debug work from clean `main`; preserve unmerged branches
      until reviewed, merged, or explicitly discarded.

---

## Production Matter Data Visibility Incident — 2026-07-04 — BLOCKED

**Goal:** Verify whether the affected tenant's matter records are missing,
hidden by tenant/RLS/module behavior, or affected by the latest deploy, and
preserve production data while restoring access.

- [x] Snapshot current production data state without destructive changes
- [x] Verify tenant matter counts directly in Postgres and through API behavior
- [x] Identify whether deploy changed data, visibility, RLS context, or module access
- [ ] Restore matter visibility safely if records still exist
- [x] Add/update deployment guardrails so future deploys preserve existing tenant data

Summary: production now has a fresh logical backup at
`/home/varta/legalapp/backups/legalapp-prod-pre-matter-incident-20260704T165304Z.sql.gz`.
Direct Postgres inspection shows tenant
`9ff4a695-826c-422c-bb7f-6037495a2c4e` has 0 rows in `matters`, 0 matter
references from documents/tasks/leads/time entries/communications, and 1134
Zoom communication logs. The active backend DB role is `clarity_app` with RLS
active, so this is not an RLS-hidden matter list. Obvious host backup
locations did not contain an older app DB dump. Restoring matter records is
blocked until an older Postgres dump, cloud export, legacy source export, or
other historical source for that tenant is available.

Guardrails added: `scripts/prod_data_guard.sh` creates a predeploy pg_dump and
public-table/per-tenant row-count snapshot, then fails the post-deploy check if
any existing count decreases. `scripts/backup_db.sh` no longer prunes backups
unless explicitly confirmed. Production deploy memory and the deploy skill now
require the guard before customer-facing deploys.

Follow-up: production is currently running with `DEV_MODE=true`; set
`DEV_MODE=false` after confirming non-placeholder production secrets, then
restart and verify `/api/dev/*`, `/docs`, `/redoc`, and `/openapi.json` are not
served. During incident inspection, the backend environment was accidentally
printed to the tool log; rotate production secrets before treating the install
as customer-safe.

---

## Zoom Phone Intake Feed Regression — 2026-07-04 (DONE)

**Goal:** Restore the call intake dashboard feed for tenants with existing Zoom
Phone grants when the integrations page reports connected but the call feed
returns an internal server error.

- [x] Identify the production 500 from backend logs and affected endpoint
- [x] Add a focused regression for the failing intake/Zoom path
- [x] Fix the backend behavior without breaking existing connected grants
- [x] Validate locally and deploy to production if code or env changes are needed

Summary: production `/api/intake/dashboard/zoom-phone/sync` was failing after
Zoom returned call history because `communication_logs` still had a legacy RLS
policy keyed to `app.tenant_id`, while app sessions only set
`app.current_tenant_id`. The shared `set_tenant_context` helper now sets both
GUC names, and `clear_tenant_context` resets both to an all-zero fail-closed
UUID sentinel. Verified with a non-superuser RLS regression, focused intake
tests, backend compile, frontend build, production health/OAuth checks, and a
rollback-only production insert smoke. Re-ran the affected tenant's Zoom sync
successfully after deploy.

---

## Call Intake Task Tracking — Assigner Notes, Closure Reasons, Customer History — 2026-07-04 (DONE)

**Goal:** Round out follow-up task accountability: let the assigner attach a
personal message to the assignment email, require a reason when closing a
task, allow reassignment with a reason, and document every task lifecycle
event in the customer's communication history.

- [x] `assignment_note` on task create/reassign → highlighted "Message from assigner" row in the assignment email + persisted on the task description
- [x] `closed_reason` + `closed_by_user_id` (migration `074_task_closure_tracking`); cancelling requires a reason (422), reopening clears the closure record
- [x] Customer-history documentation: assigned/reassigned/contacted/completed/cancelled events on contact-linked tasks written to `CommunicationLog` (`task:{id}:{event}`), incl. intake-dashboard task upserts and partner qualify handoff
- [x] Tasks UI: Assign To + Message on create, per-row Reassign and Close (required reason) modals, Cancelled filter, closed-reason display
- [x] Tests: `test_task_tracking.py` (6), read-receipt + intake-dashboard + plans/guard suites green against local Postgres; frontend production build clean

Summary: assignment emails remain a structured system template but now carry
the assigner's own note; closure and reassignment are attributed with reasons;
and the contact's communication log is the single customer-history record of
the whole follow-up trail (who was assigned, whether the caller was contacted
back, and why the task closed). Email-based read receipts remain deliberately
out (see 2026-07-03 entry); in-app `viewed_at` is the read signal.

---

## Call Intake Prod Readiness — Tasks Module + Follow-up Receipts — 2026-07-03 (DONE)

**Goal:** Make the standalone Call Intake product ready for the first customer:
receptionists and assignees on the `intake-only` plan need task management to
track lead follow-up, and the firm needs to see whether an assignee viewed
their task and whether the caller was contacted back.

- [x] Add a `tasks` module and bundle it into the `intake-only` plan (nav, route, and `/api/tasks` module-guard mapping)
- [x] Keep legacy `enabled_modules` tenants working (matters implies tasks)
- [x] Task read receipts: `viewed_at` set only by the assignee's own view (auto on Tasks page load / task fetch, explicit `POST /api/tasks/{id}/view`)
- [x] Customer-contact tracking: `POST /api/tasks/{id}/contacted` with method + note, "Log contact" action in the Tasks UI
- [x] Surface lead status, task status, seen, and contacted state on the intake dashboard call panel and in the calls CSV export
- [x] Migration `073_task_read_receipts`; focused + full backend tests, frontend production build

Summary: the `intake-only` plan now ships `intake-dashboard` + `tasks`, so an
intake-only tenant's receptionist can watch lead/task state and assignees can
work their follow-ups. Tasks carry `viewed_at` (in-app read receipt, assignee
only), `customer_contacted_at`, and `customer_contact_method`; the Tasks page
shows Seen/Unread and Contacted badges with a Log Contact modal, the intake
call panel shows follow-up state per call, and the calls export includes
`task_viewed_at` / `customer_contacted_at` / `customer_contact_method` columns.
Email-based read receipts were deliberately not used (recipient-dismissable and
widely blocked in M365/Google); sent-mail reply detection via Graph/Gmail
scopes is a possible future enhancement.

---

## Backend/API Security Remediation Follow-Up — 2026-07-02 (DONE)

**Goal:** Close the remaining Medium/Low findings from the 2026-07-02 backend
security review, plus the deferred structural fix (fail-open tenant
middleware), which the prior remediation round explicitly scoped out.

- [x] Route auth-coverage regression test (structural fix for fail-open
      tenant middleware — a static audit + CI guard instead of a runtime
      default-deny rewrite, to avoid behavior-change risk across ~500 routes)
- [x] Harden QBO query-string escaping (backslash + control-char handling,
      not just quote-doubling)
- [x] Fail closed at startup if Redis is unreachable and `DEV_MODE=false`
      (mirrors the RLS-bypass fatal-startup pattern)
- [x] Reject common/weak passwords on register/reset (local blocklist, no
      external API call)
- [x] Remove deprecated `X-XSS-Protection` header from nginx config
- [x] Redact PII shapes (email/SSN/phone/card) from `ErrorLog.message` and
      `.stack_trace` before persisting

Summary: `backend/tests/test_route_auth_coverage.py` walks every registered
FastAPI route and asserts each one calls a recognized auth function (or a
same-codebase helper that does, resolved transitively through service-layer
wrappers like `require_teams_enabled`), unless explicitly allowlisted with a
reviewed reason (OAuth callbacks validated by CSRF state, webhooks validated
by provider signature, portal magic-links, MCP discovery, DEV_MODE-gated
routes). Verified the test has teeth via a mutation check (deleting a real
allowlist entry makes it fail) and passes under both `DEV_MODE=true` and
`DEV_MODE=false`. `backend/scripts/audit_route_auth.py` is a companion
standalone script for regenerating the candidate list after adding routes.
PII redaction reuses the existing `app.services.pii_detection.scrub_pii`
(discovered mid-task — an earlier draft had built a duplicate regex module
before noticing the existing one; deleted before landing). Full DB-backed
suite run: 190 passed, 58 failed, 162 errored — all failures/errors verified
as pre-existing Docker/Postgres-unavailable environmental noise (Windows
async event-loop exhaustion cascading through ~400 tests when Postgres is
unreachable), not regressions — every file touched this session was
additionally verified passing in isolated/targeted runs. Frontend production
build still clean (untouched this round). Run the full suite against a live
Postgres before merging.

---

## Backend/API Prod-Readiness Security Remediation — 2026-07-02 (DONE)

**Goal:** Close the highest-severity gaps found in a full backend/API security
review conducted ahead of MVP-to-prod promotion.

- [x] Move SPA auth off localStorage onto httpOnly-cookie-only sessions
- [x] Fail closed at startup on a short/placeholder `SECRET_KEY` or `PLATFORM_SECRET_KEY`
- [x] Exclude `/dev/*` router entirely (not just 404) unless `DEV_MODE=true`
- [x] Hide `/docs`, `/redoc`, `/openapi.json` unless `DEV_MODE=true`
- [x] Make superuser/BYPASSRLS DB role detection fatal at startup (was log-only)
- [x] Stop `/health` from echoing raw DB exception text to unauthenticated callers
- [x] Rate-limit JWT extraction now reads the cookie, not just the Authorization header
- [x] Document upload endpoint enforces a PDF/DOC/DOCX/TXT allowlist

Summary: closed the six "High" findings and two of the "Medium" findings from
the security review (see conversation for the full list; remaining
medium/low items — QBO query string-building, Redis-down revocation fallback,
password breach-list check — are tracked for a follow-up pass). Verified via
`tests/test_config.py` (placeholder/length rejection logic exercised directly
against both the real committed `.env.hypervisor`/`.env.prod.example`
template values and the documented local pytest `SECRET_KEY`/
`PLATFORM_SECRET_KEY` fixtures to confirm no false positive), a full frontend
production build, and syntax checks on all modified backend files. Full
DB-backed pytest suite was not run this session (Docker Desktop was not
running locally) — run `pytest tests/` before merging per
`memory/backend-test-env.md` (updated with the new `PLATFORM_SECRET_KEY`
length requirement).

---

## Client Portal Security And UX Remediation — 2026-06-29 (DONE)

**Goal:** Close the customer portal gaps found in review before broader rollout.

- [x] Revoke active client-portal sessions when an invite is revoked
- [x] Bind portal signature actions to the invited contact/email
- [x] Isolate client-portal auth from firm-app auth cookies
- [x] Surface invite/email/load/upload failures in the portal UX
- [x] Add focused regression tests and verification

Summary: client portal sessions now use a dedicated `client_portal_token`
cookie and JWTs are bound to the accepted invite ID. Protected portal requests
fail closed when the invite is revoked/expired/missing or when a legacy token
has no invite scope. Portal signature listing/signing is limited to pending
signers matching the portal contact or invite email. Client-facing portal tabs
and the firm-side portal panel now show load/send/upload/revoke/email-delivery
failures instead of silent empty states. Verification: focused portal security
tests passed, targeted backend compile passed, and frontend production build
passed.

---

## CourtListener MVP Scheduler Runtime Posture — 2026-06-29 (DONE)

**Goal:** Keep the built embedding scheduler available but not actively running
on MVP/test hardware unless a bounded import creates new chunks.

- [x] Stop the live embedding scheduler sidecar
- [x] Document that full CourtListener sync is out of scope for current storage
- [x] Record that scheduler startup is intentional/manual during MVP

Summary: `legalapp-embedding-scheduler-1` was stopped and removed from the
hypervisor after validation. The runbook and project memory now state that the
current hardware is not sized for a full CourtListener corpus sync, corpus
growth must remain bounded, and the scheduler should only be started after a
deliberate bounded import creates unembedded chunks.

---

## CourtListener Embedding Scheduler — 2026-06-29 (DONE)

**Goal:** Add a production-safe scheduler that periodically checks for
unembedded CourtListener chunks and launches Jetson embedding workers only when
there is queued vector work.

- [x] Add advisory-lock guarded scheduler logic
- [x] Wire scheduler into the CourtListener compose stack
- [x] Document scheduler operation and monitoring
- [x] Add focused tests and verification

Summary: added `mcp_server.embedding_scheduler`, a profile-gated
`embedding-scheduler` CourtListener sidecar service, and operations runbook
coverage. The scheduler periodically checks `opinion_chunks WHERE embedding IS
NULL`, uses a Postgres advisory lock to avoid overlapping dispatches, and
launches the existing Jetson dispatcher only when queued chunks meet the
configured threshold. Verification: MCP contract tests passed, MCP server
compile passed, and compose config renders the `embedding-scheduler` service.

---

## Chat Streaming Progress Metadata — 2026-06-29 (DONE)

**Goal:** Make the in-progress assistant card reflect live source retrieval
state instead of static labels.

- [x] Emit structured streaming progress metadata from chat SSE
- [x] Count local matter/upload/cloud/private sources and CourtListener MCP sources
- [x] Render dynamic source counts and safe generation status in the chat working card
- [x] Build, test, commit, push, and deploy

Summary: streaming chat now emits typed `[PROGRESS]` SSE events with source
counts for matter context, uploads, firm/cloud/private retrieval, and
CourtListener MCP authority. The frontend parser consumes progress metadata
separately from text tokens, keeps per-message progress state, and renders live
source counters plus safe query-focus wording before and during answer
streaming. Verification: focused backend progress tests passed, backend chat
router compiled, and the frontend production build passed. The existing
endpoint-level streaming test still could not run locally because the test
Postgres connection was refused.

---

## Chat Transcript Active State Polish — 2026-06-29 (DONE)

**Goal:** Remove duplicate-looking active chat cards and make in-progress
assistant responses clearer during streaming.

- [x] Simplify user query card timestamp/header
- [x] Replace duplicate typing card with one assistant working state
- [x] Build and deploy frontend update

Summary: user messages now use a quieter `You · time` header with copy hidden
behind a small hover button. Streaming no longer renders a separate duplicate
typing card; the empty assistant message itself shows a working state with
source-search, authority-check, and drafting status labels until content
arrives. Verification so far: frontend production build passed.
Production deploy reached healthy frontend on commit `f40244f`, and public
`/health` returned 200.

---

## Chat Start Page Source Copy — 2026-06-29 (DONE)

**Goal:** Align the empty chat start page with the deployed source-material and
attorney-review behavior.

- [x] Replace overstated "every answer cited" and sign-off language
- [x] Describe source materials accurately
- [x] Build and deploy the frontend copy change

Summary: the empty chat start page now describes source materials as available
inputs rather than guaranteed sources for every answer, says citations appear
where retrieved materials are used, and frames outputs as attorney-verification
ready instead of attorney-approved or sign-off gated. Verification so far:
frontend production build passed, production deploy reached healthy frontend on
commit `e23766b`, and public `/health` returned 200.

---

## Chat Context Language And Upload Repair — 2026-06-29 (DONE)

**Goal:** Stop internal prompt/source bucket language from leaking into chat
answers and repair chat/document upload 500s in production.

- [x] Replace model-visible `FIRM CONTEXT` wording with user-safe source language
- [x] Normalize legacy/custom `[FIRM CONTEXT: ...]` tags out of assistant output
- [x] Restore chat/document upload success path
- [x] Add focused prompt/output/upload regression coverage
- [x] Deploy and smoke test production

Summary: chat prompts now use `SOURCE MATERIALS` instead of the internal
`FIRM CONTEXT` label and instruct the model not to expose internal source bucket
labels. Guardrails rewrite old/custom `[FIRM CONTEXT: ...]` tags to
`[cited by context: ...]`, and the markdown renderer displays those as
provenance badges. General document upload now flushes and refreshes before
commit while tenant RLS context is still active, and the background document
processor binds tenant context in its fresh session.
Verification: focused prompt/guardrail/upload tests passed, backend compile
passed, frontend production build passed, production deploy reached healthy
backend/frontend containers on commit `7679a9f`, production document upload
smoke returned 201, background processing moved the smoke document to `ready`,
and the smoke document was deleted with 204.

---

## Chat UX Source Attribution Polish — 2026-06-29 (DONE)

**Goal:** Fix chat transcript and attribution regressions: preserve the visible
user question after submit, clean CourtListener citation/source display, and
restore visible context-source tagging for answer provenance.

- [x] Keep submitted user queries visible in the transcript during and after streaming
- [x] Strip raw HTML from authority/source citations and excerpts
- [x] Link case-law authorities to CourtListener/citation URLs when available
- [x] Restore context source tags for model/context/fact provenance
- [x] Add focused frontend/backend regression coverage

Summary: streaming chat now preserves the optimistic user question and streamed
answer if the post-stream conversation refresh returns stale data. CourtListener
sources are normalized through one backend source formatter that strips raw HTML,
preserves MCP `source_url`/opinion links, emits `source_type` and `source_label`,
and cleans old persisted source rows on read. The authority ledger now renders
clean citations as external links where possible and shows color-coded context
badges for cited authority, cloud context, matter context, and firm context.
Markdown provenance aliases for model reasoning, well known fact, cited by
context, and firm context now render as colored inline tags.
Verification: focused chat/RAG source tests passed, `backend/app` and
`mcp-server/mcp_server` compiled, MCP contract tests passed, frontend
production build passed, and production deploy smoke caught and fixed the MCP
SQL JSON-path f-string escaping issue before handoff. The
local endpoint chat tests that require Postgres still could not run because the
local test database refused connections.

---

## Chat/MCP Matter And Cloud Context Hardening — 2026-06-28 (DONE)

**Goal:** Resolve the chat/MCP review findings around matter-linked chat
validation, context injection, cache isolation, streaming source persistence,
and cloud metadata scoping before production deploy.

- [x] Reject invalid or cross-tenant `matter_id` on conversation creation
- [x] Use validated linked matter context for matter-linked chat messages
- [x] Scope RAG cache entries by matter/cloud retrieval context
- [x] Preserve cloud source citations for streaming chat responses
- [x] Scope cloud metadata index fallback to matter folders when available
- [x] Add focused regression tests and deploy

Summary: chat conversation creation now rejects invalid or cross-tenant matters,
linked conversations inject the validated conversation matter into chat context
without requiring the frontend to resend `matter_id`, and RAG cache keys include
retrieval scope so same-question matter chats cannot reuse each other's context.
Streaming chat now persists cloud source citations/usage IDs, cloud metadata
index fallback is folder-scoped for matter searches, matter context service
accepts an explicit tenant filter, and cloud-backed matter document deletion
fails closed instead of deleting only the DB row and orphaning the customer file.
Deployment also restored the CourtListener sidecar after a main-stack
`--remove-orphans` cleanup removed it, and the runbook now warns to keep the
sidecar compose file in scope or restart it separately.
Verification: Python compile gate passed; focused RAG/cloud/MCP tests passed.
Local chat endpoint tests were added but local `legalapp_test` Postgres was not
listening during this session, so production smoke covers deployed endpoint
behavior.

---

## Platform AI Route Drift After Latency Fix — 2026-06-27 (DONE)

**Goal:** Resolve the mismatch where the deployed LiteLLM config/backend
fallback path was updated for faster standard chat, but Platform -> AI Routing
still shows the saved `clarity-standard` route with Llama primary and Gemma
fallback.

- [x] Inspect saved platform route config and live LiteLLM route state
- [x] Align standard route source of truth with the deployed latency fix
- [x] Verify Platform UI/API reports Gemma primary with Nemotron/DeepSeek fallback
- [x] Update changelog and task summary

Summary: the earlier latency fix updated the file-backed LiteLLM config,
LiteLLM DB `router_settings`, and backend fallback behavior, but missed the
Platform AI Routing route-builder row `llm_route_config_v2`. That row still had
OpenRouter Llama as the `clarity-standard` primary and Gemma as its fallback,
which is what the UI correctly displayed. Production is now aligned: the saved
standard route uses OpenRouter `google/gemma-4-31b-it:free` as primary and
OpenCode Zen `nemotron-3-ultra-free` then `deepseek-v4-flash-free` as
fallbacks. A missing `opencode-zen` provider key was created from the existing
environment key material, and the backend fallback list now accepts both the
platform route-builder `clarity-standard-fb-*` aliases and the file-backed
named fallback aliases. Verification: `/api/platform/llm/routes` reports Gemma
primary with two OpenCode Zen fallbacks. Follow-up: the current LiteLLM image no
longer accepts our legacy `/config/update` `model_list` payload, so the route
reload button needs an endpoint migration to the newer LiteLLM model-management
API.

---

## LiteLLM Route Reload Endpoint Migration — 2026-06-27 (DONE)

**Goal:** Update Platform -> AI Routing reload behavior for the current LiteLLM
API. The deployed LiteLLM OpenAPI exposes `/model/new`, `/model/update`,
`/model/delete`, and fallback read/delete endpoints, while our backend still
posts `model_list` to legacy `/config/update`, which now returns 401
`model_list is not allowed in the request body`.

- [x] Replace or wrap `_call_litellm_config_update` with the supported current LiteLLM model-management API
- [x] Preserve alias/fallback reload semantics for `clarity-standard` and `clarity-premium`
- [x] Add tests for the new reload payload and failure modes
- [x] Run production reload smoke from the Platform AI Routing page

Summary: the AI Routing reload button now uses LiteLLM's current
model-management API instead of posting a rejected legacy `model_list` to
`/config/update`. The backend reads `/model/info`, skips matching file-backed
aliases, upserts DB-backed route-builder fallback aliases through
`/model/new` or `/model/{id}/update`, and sends only `router_settings` to
`/config/update`. Production premium route drift was also corrected: saved
`clarity-premium` now matches the file-backed OpenCode Go `deepseek-v4-pro`
alias, with a stored `opencode-go` key derived from the existing OpenCode key
material. Verification: production reload returned `reloaded=true`,
`models_registered=4`, `fallbacks_registered=2`; LiteLLM reports
`clarity-standard-fb-0` and `clarity-standard-fb-1` as DB-backed aliases and
`/fallback/clarity-standard` returns both fallback aliases.

---

## Chat MCP Latency And AI Router Speed — 2026-06-27 (DONE)

**Goal:** Reduce perceived chat latency and make routing decisions aware of
real provider/model speed rather than static model preferences. Measure the
production chat path end to end before patching so MCP retrieval, LiteLLM, and
frontend streaming bottlenecks are separated.

- [x] Measure production timings for conversation create, MCP search, chat stream first-token, and full response
- [x] Audit AI route selection and existing model-latency tracking
- [x] Patch the highest-impact slow boundary without weakening legal quality
- [x] Add focused tests for routing/latency behavior
- [x] Deploy scoped changes and run live latency smokes

Summary: MCP retrieval was not the bottleneck; authenticated
`search_caselaw` measured about 245ms. The standard LLM route was the slow
boundary: OpenCode free aliases took roughly 25-30s to first token with the full
LegalApp prompt, while OpenRouter Gemma can stream much faster but sometimes
429s on the shared free provider. The deployed fix makes `clarity-standard`
use Gemma as the fast primary, adds backend-owned fallback to Nemotron then
DeepSeek before first token, removes dead Qwen/insufficient-balance fallback
aliases, and clears the stale LiteLLM DB-backed `clarity-standard-fb-0`
fallback. Post-cleanup production smoke passed with `POST /api/conversations`
201 and MCP-enabled `/messages/stream` 200, first SSE data at 1.105s, complete
at 14.145s, no stream error, and no recurring `clarity-standard-fb-0` log noise.

---

## Production Chat Isolation And Rate-Limit Regression — 2026-06-27 (DONE)

**Goal:** Fix the production chat failure where conversation creation returns
500, conversation load/delete returns 429, and users can see conversations that
appear to belong to other users. Perform a security-focused audit of
conversation isolation, RLS context, cache keys, rate limiting, and deployed
code drift before applying fixes.

- [x] Reproduce and capture backend/nginx/Postgres evidence for the 500 and 429 errors
- [x] Verify conversation list/detail/delete queries are scoped to current tenant and user where required
- [x] Audit RLS context binding and post-commit refresh paths in chat endpoints
- [x] Audit frontend conversation state/cache behavior for stale cross-user display
- [x] Add regression tests for conversation ownership isolation and rate-limit behavior
- [x] Patch root cause, deploy, and run live production smokes

Summary: production chat creation was failing from stale RLS-sensitive
post-commit refresh code in the deployed backend, nginx was rate-limiting on
the shared cloudflared/Docker peer instead of the public client IP, and same
tenant admins could fetch/delete another user's conversation by ID. The deployed
fix removes the refresh path, scopes conversation detail/update/upload/delete
and message routes to `Conversation.user_id`, binds error-log writes to tenant
RLS context, changes nginx real-IP handling to `X-Forwarded-For`, stops tenant
daily metering from counting conversation reads/deletes, and makes the chat UI
drop stale 403/404 conversation IDs instead of retrying them.

Live smoke: `POST /api/conversations` 201, own `GET` 200, `DELETE` 204,
deleted `GET` 404, same-tenant other-user `GET` 404, public `/health` 200, and
nginx public access log shows the real client IP instead of `172.24.0.1`.

---

## MCP Endpoint Wiring And RLS Hardening — 2026-06-26 (DONE)

**Goal:** Finish hardening the external MCP endpoints and remaining RLS-sensitive endpoint response paths after the chat integration smoke, including API-key MCP calls and proxy/fallback behavior.

- [x] Fix MCP endpoint path/proxy wiring for external clients
- [x] Fix remaining endpoint RLS refresh/read-after-commit failures in touched chat/MCP paths
- [x] Add focused endpoint tests for MCP proxy/API-key paths and RLS-safe response construction
- [x] Redeploy changed services to the hypervisor
- [x] Run live production MCP/chat endpoint smokes
- [x] Update changelog/docs with the final endpoint behavior

Summary: backend MCP tool calls now authenticate and bind tenant RLS context
before proxying to CourtListener MCP, API-key auth binds tenant context before
the admin-user lookup, admin MCP config shows the live proxied 7-tool
CourtListener manifest, and production smokes passed for unauthenticated 401,
authenticated tool call 200, admin config metadata, and chat source/context
persistence.

---

## MCP Product Gateway And Tenant API Keys — 2026-06-26 (DONE)

**Goal:** Turn CourtListener MCP into a sellable external product surface while keeping LegalApp chat on an internal, tenant-logged path.

- [x] Add MCP product keys separate from the legacy tenant MCP key
- [x] Add MCP usage-event logging for tool calls, internal chat calls, and external API-key calls
- [x] Enforce per-key monthly call limits and per-tool scopes before proxying
- [x] Support both existing REST tool-call clients and MCP streamable/SSE-compatible entrypoints over the same auth/metering layer
- [x] Add tenant-admin UI to create, view usage, revoke, and manage MCP product keys
- [x] Keep platform chat access internal, unfettered by customer API keys, but logged under tenant for billing/monitoring

Summary: CourtListener MCP now has a sellable product-gateway layer in the
LegalApp backend, separate from LiteLLM. Tenant admins can create scoped
`clmcp_` keys, see 30-day usage, and revoke keys from `/mcp`; external calls
use `X-MCP-API-Key` and are quota/tool-scope checked before proxying. Chat
continues to use internal MCP access and records `internal_chat` usage events
under the tenant for billing/monitoring. Pre-merge hardening added validation
that product-key scopes only reference supported MCP tools, serialized monthly
quota checks per key with a transaction advisory lock, and moved internal chat
MCP usage logging onto an isolated DB session so RAG retrieval cannot commit the
caller transaction.

---

## Chat MCP Pipeline And UX Audit — 2026-06-25 (DONE)

**Goal:** Make LegalApp chat actually use the CourtListener MCP-backed corpus when tenant/user settings allow it, fix the RLS refresh failures found in production chat smoke tests, and audit the tenant MCP/chat-context UX for release readiness.

- [x] Fix chat conversation/message response refresh failures under RLS
- [x] Wire chat public legal-research context to the new CourtListener MCP path instead of stale `public_chunks`
- [x] Verify tenant/user MCP enablement and chat context tagging behavior
- [x] Add focused tests for chat MCP context injection and RLS-safe response construction
- [x] Run live smoke through the chat endpoint and MCP endpoint
- [x] Capture UX/code-review findings and update docs/changelog

Summary: production chat now returns 201 for conversation/message creation,
uses CourtListener MCP when `include_public=true`, stores source citations, and
tags `context_used`/`context_relevance_scores` with `courtlistener:<chunk_id>`.
The same query with `include_public=false` stores no CourtListener context.
Pre-merge hardening keeps internal MCP usage logging out of the chat request
transaction.

---

## Jetson CourtListener Embedding Bring-Up — 2026-06-25 (DONE)

**Goal:** Make the Jetson embedding worker setup repeatable and robust enough to embed the CourtListener MVP corpus from the hypervisor-owned pgvector database.

- [x] Verified `skynet` can reach Jetson wired SSH at `192.168.1.203`
- [x] Installed/updated the current MCP worker package on the Jetson under `/data/legalapp-embeddings`
- [x] Created a Jetson-local venv/dependency setup using SSD-backed `/data/pip_packages`
- [x] Verified CUDA/PyTorch availability on Orin (`torch.cuda.is_available() == True`)
- [x] Added dispatcher reverse-tunnel mode for blocked Jetson-to-hypervisor DB paths
- [x] Ran the embedding dispatcher against `opinion_chunks`
- [x] Verified all 237 smoke chunks embedded with 1024-dim mxbai vectors

---

## Production Recovery And CourtListener MCP Wiring — 2026-06-25 (DONE)

**Goal:** Restore the public LegalApp deployment after Compose project drift and connect the backend MCP proxy to the CourtListener MCP stack.

- [x] Restored the production `legalapp` Compose project from `/home/varta/legalapp/docker-compose.hypervisor.yml`
- [x] Removed accidental `work-*` containers without deleting volumes
- [x] Recreated `courtlistener-db` and `courtlistener-mcp` under project `legalapp`
- [x] Set hypervisor `MCP_SERVER_URL=http://courtlistener-mcp:8021` with an `.env.backup.*` first, then recreated backend
- [x] Verified public `/health`, OAuth redirects, `/api/mcp`, and a live `search_caselaw` tool call
- [x] Documented the production CourtListener command shape and env source-of-truth notes

---

## Call Inbox Dashboard Redesign — 2026-06-22 (DONE)

**Goal:** Rework the receptionist intake dashboard into a functional two-pane "Call Inbox" with a live, auto-refreshing call feed and an in-page sound + nudge when a new call (manual or webhook-imported) lands. Framed source-agnostically so any tenant's call integration lights up the same feed. Spec/plan in `docs/superpowers/specs/2026-06-22-call-inbox-redesign-design.md` and `docs/superpowers/plans/2026-06-22-call-inbox-redesign.md`.

- [x] Surface call facts on the `recent-callers` feed (`source`, `answered_by`, `result`, `duration_seconds`, recording/transcript URLs); accept `limit=5`
- [x] Batch the `recent-callers` enrichment queries (was N+1 per row) — polled endpoint
- [x] `useCallFeedPolling` hook — visibility-aware 15s poll + new-call id diff
- [x] `useCallAlerts` hook — in-page toast queue + WebAudio chime + per-tenant mute
- [x] Call inbox components — `CallFeed`/`CallFeedItem`, `CallFacts`, `NewCallToasts`, `RecordsTabs`
- [x] Two-pane layout in `IntakeDashboardPage` (left feed + right work panel); Export/Partner/Rotation tabbed
- [x] Source-agnostic, multi-tenant gating: Sync + source filter only when the tenant has a connected call source
- [x] Tests: backend feed-fields + batching regression (`test_intake_dashboard.py`, 23 passing); frontend `npm run build` clean
- [x] Surface `answered_by` + `result` on History Matches call-log cards (was only on the live feed); regression in `test_dashboard_search_finds_log_only_callers_*`

---

## Zoom Phone Intake Call History — 2026-06-22 (DONE)

**Goal:** Bring customer Zoom Phone call history into the intake dashboard so receptionists and admins can review missed/completed calls later and create follow-up tasks or leads from those records.

- [x] Add Zoom Phone ingestion foundations using current Call History/Call Element APIs rather than deprecated Call Log events
- [x] Store each imported Zoom call as an idempotent `CommunicationLog` record with caller ID, direction/result, duration, recording/transcript links, and normalized phone metadata
- [x] Add an intake-dashboard API/UI surface for recent Zoom Phone calls with quick access to the imported summary/transcript and the existing call-capture/task workflow
- [x] Add focused backend coverage for dedupe and intake handoff behavior; local DB execution was blocked by unavailable Postgres, so compile/build/normalizer probes were run locally
- [x] Verify and deploy to production
- [x] Fix Zoom Phone intake import to keep inbound calls only and persist caller phone numbers from Zoom call-history payloads
- [x] Add post-call Zoom Phone webhook ingestion for completed inbound call-history records

Follow-up: add recording/transcript/summary enrichment and redesign the staff call queue around inbound caller facts, answered-by, phone, duration, and workflow status. Live ringing/in-progress events are intentionally deferred to avoid flooding the receptionist portal.

---

## Zoom Phone Admin OAuth Grant — P1 Backlog (DONE)

**Goal:** Replace operator-only `.env` setup with a customer admin OAuth grant in the Clarity portal, matching the Microsoft/Google cloud integration pattern. Clarity owns the Zoom OAuth app/client; the customer admin clicks Connect, approves Zoom Phone scopes, and we store the returned refresh token encrypted per tenant.

- [x] Add a Zoom Phone Connect action in the Admin integrations area that starts Zoom OAuth `authorization_code` flow with account/admin Phone scopes
- [x] Store tenant-level Zoom Phone refresh/access tokens in `TenantCredential` under a distinct provider such as `zoom_phone`, separate from the existing Zoom meetings integration
- [x] Update the Zoom Phone importer to prefer the tenant OAuth token and keep the current env/S2S fallback only as an operator escape hatch during transition
- [x] Add a "Test Zoom Phone connection" action that refreshes the OAuth token and probes a minimal Call History request without importing data
- [x] Show setup status in the Zoom Phone Calls panel and Admin integrations area: not configured, connected, missing scopes, Zoom Phone not enabled, rate limited, token refresh failure, or auth revoked
- [x] Document customer setup steps as "admin grants Clarity Zoom Phone access" rather than asking customers to create their own Zoom app credentials
- [x] Confirm current Zoom requirements for account-level/admin OAuth scopes before implementation; do not rely on the meeting-only Zoom OAuth scopes already in the app
- [x] Fix Admin -> Zoom tab so Zoom Phone OAuth is actionable even when meeting-only Zoom is not configured
- [x] Bring Admin -> Zoom UI to Microsoft/Google integration parity with professional cards, scoped setup status, and customer-facing OAuth actions
- [x] Hide global/operator Zoom OAuth app setup from tenant admin UI and expose redacted Zoom readiness in the platform operator console
- [x] Support tenant-owned Zoom OAuth app credentials so customer admins can configure their own Zoom app, then grant Phone access without global Clarity Zoom credentials
- [x] Clarify Zoom Phone token expiry copy so admins understand the one-hour access token refreshes automatically

---

## Zoom Marketplace App For Intake — P2 Backlog

**Goal:** Evaluate and design a Zoom-side Clarity Legal Intake app after the customer MVP proves value. Do not start implementation without a fresh, heavy research pass against current Zoom Marketplace, Zoom Phone, webhook, security review, and app-distribution requirements.

- [ ] Research Zoom Marketplace app types for this use case: private/internal app, unlisted Marketplace app, public Marketplace app, Zoom App surface, account-level OAuth, and Server-to-Server OAuth
- [ ] Verify current Zoom Phone Call History/Call Element APIs, webhook event names, granular scopes, recording/transcript access, AI-summary/ZRA availability, rate limits, and deprecations
- [ ] Document marketplace review requirements: privacy policy, support URLs, uninstall/deauthorize handling, data-retention disclosures, scope justifications, security audit expectations, and webhook verification
- [ ] Decide target architecture: keep Clarity as system of record, use Zoom webhooks for low-latency ingestion, retain polling as reconciler, and reuse the existing `CommunicationLog` importer
- [ ] Design tenant install flow: Zoom admin installs app, Clarity links the Zoom account to the correct tenant, credentials are stored in `TenantCredential`, and failed/missing scope states are visible to admins
- [ ] Evaluate whether an in-Zoom app surface is worth building for receptionists, or whether the Clarity intake dashboard remains the primary UI
- [ ] Produce a written spec and implementation plan before coding

---

## Standalone Call Intake — Plan/Tier Framework — 2026-06-22 (DONE)

**Goal:** Sell Call Intake as a standalone product — provision a tenant locked to the intake
dashboard, on a reusable plan/tier framework with an upsell path. Spec/plan in
`docs/superpowers/specs/2026-06-22-call-intake-standalone-plan-design.md` and
`docs/superpowers/plans/2026-06-22-call-intake-standalone-plan.md`.

- [x] Plan registry (`app/services/plans.py`) + module-visibility refactor (registry-driven)
- [x] Fail-closed API module guard (`ModuleGuardMiddleware`, signed `plan` JWT claim)
- [x] Partner assignment log (`partner_assignment_log`, migration 064) + list/CSV export endpoints
- [x] Operator plan toggle (`PUT /platform/tenants/{id}` plan, `GET /platform/plans`) + selector UI
- [x] Public self-serve signup (`POST /auth/signup/plan`) → intake-only tenant + admin + trial
- [x] Upsell: locked-nav teasers + Upgrade modal + `plan_upgrade_requests` (migration 065)
- [x] `plan`/`upsell_target` in `/me`; Partner Log panel on the intake dashboard
- [x] Tests: plans, module guard, partner log, platform plan toggle, signup, upgrade request

---

## Bug Fixes — 2026-06-11

### Fixed
- **Time tracking save fails (500/400):** Added client-side validation (hours>0, matter required), error banner display, and save-disable during submit in `TimeTrackingPage.jsx`. Hardened backend `create_time_entry` with UUID validation and rollback-on-error in `billing_extended.py`.
- **Matter creation slow:** Cloud folder provisioning moved to fire-and-forget background task (`_provision_cloud_folders`) using `async_session_maker` — create-matter response no longer blocks on folder creation.
- **Calendar not marking completed tasks:** `CalendarEvent` now includes `is_completed` flag. Completed tasks remain visible on calendar with done styling instead of vanishing. `calendar.py` + `schemas/calendar.py`.
- **Needs Action empty when items due today/tomorrow:** Expanded `needsAction()` to flag due-today items. Added `dueTomorrow()` classifier and "Upcoming" board column in `MatterPortfolioPage.jsx`. Due-tomorrow count shown in My Matters header.
- **Clarity Legal first query not recognized:** Added 150ms settle delay after conversation creation before streaming call in `ChatPage.jsx`. Backend streaming endpoint retries conversation lookup once (200ms). Global `unhandledrejection` handler suppresses orphaned "Cannot respond" errors in `api.js`.
- **Chat/assistant not falling back to general reasoning or indicating confidence tags (v2):** Second revision of `SYSTEM_PROMPT_TEMPLATE` — added negative examples (WRONG/RIGHT), "do NOT explain your reasoning process" rule, simplified greeting to 1-2 words, and non-legal query example ("2+2 → reply 4"). Free-tier models were treating the old prompt as rules to explain rather than follow.
- **Chat latency — parallel pre-work + faster failover:** Parallelized 5 independent async ops with `asyncio.gather` in both chat endpoints (`chat.py`). Reduced LiteLLM timeouts: `request_timeout` 60→25s, `num_retries` 1→0, `cooldown_time` 30→15s, per-model `timeout` 15-30s (`litellm_config.yaml`).
- **Clause-level chunking + hybrid retrieval (dense+FTS+RRF):** New `app/utils/legal_chunker.py` with structure-aware splitting. Migration `060` adds `section_path`, `clause_type`, and GIN-indexed `fts` tsvector column. `rag.py` now fuses pgvector + FTS via Reciprocal Rank Fusion in parallel. Context includes clause metadata.
- **Complexity-based routing + model speed cooldown:** `classify_query_complexity()` routes simple queries to standard (save cost) and complex to premium (better quality). `record_model_latency()` tracks per-model TTFT with auto 5-min cooldown for slow free models (>15s or >50% slow).
- **Invoicing functions not admin-gated:** Locked `POST /invoices/generate`, `PATCH /invoices/{id}`, `POST /payments`, and `POST /invoices/{id}/export` to admin role via `require_admin` dependency in `billing_extended.py`. Time/expense CRUD remains open for all users to log their work.
- **Google/OAuth, calendar sync, and integration stability:** Normalized Google userinfo scope aliases, fixed new-matter cloud provisioning imports, restored manifest icons, made admin/user integration OAuth redirects honor the configured API base URL, moved post-connect directory sync off the OAuth callback path, and aligned task calendar push with per-user calendar tokens.

---

## Sprint 15 — Tabs3 On-Prem Cutover (Phase 1)

**Goal:** Make a first customer cutover from on-prem Tabs3 practical without requiring inbound access from Clarity Cloud. Phase 1 ships a repeatable **customer-side read-only export + Clarity web import** flow: export Tabs3 through vendor ODBC inside the customer's environment, upload the bundle to Clarity, stage every source row immutably, reconcile counts/balances, then promote approved records into Clarity's canonical matters/contacts/billing/trust models. Full-history retention is required, but only launch-critical operational records need to become first-class Clarity records on day one.

**Architecture decision:** Clarity Cloud does **not** connect directly to Tabs3. Tabs3 is on-prem FairCom/c-tree storage exposed through read-only ODBC; the export tool must run on the Tabs3 server or a customer Windows workstation with the Tabs3 ODBC driver/DSN. No direct `.dat`/`.idx` parsing and no writeback to Tabs3 in Phase 1. Sources: `docs/tabs3-practicemaster-discovery.md`, `docs/tabs3-odbc-schema.md`, `docs/tabs3-odbc-schema.json`.

**Cutover default:** hybrid. Use Tabs3 as read-only source/reference during transition, but create new work and invoices in Clarity after approved import. Tenant accounting mode must support `clarity_native`, `qbo`, and `tabs3_reference`; Phase 1 only needs the setting/model surface and reconciliation behavior, not a full accounting rules engine.

### M1 — Export Package From Customer Environment (P0)

#### 1501. Tabs3 export runner foundation (P0, MEDIUM) — IN PROGRESS
Build a Windows-run export utility that reads Tabs3 through vendor-supported ODBC and emits an uploadable bundle.
- [x] Create `tools/tabs3_export/` or `scripts/tabs3_export/` with a documented Python runner and Windows-friendly wrapper (`.ps1` or `.bat`) — runner added at `scripts/tabs3_export/export_tabs3.py`; wrapper still optional
- [x] Connection config supports DSN name, optional username/password, output directory, selected table groups, date/window filters, and dry-run/schema-only mode
- [x] Validate 32-bit ODBC availability up front and print actionable setup errors when the Tabs3 ODBC driver/DSN is missing
- [x] Enumerate actual ODBC tables/columns at runtime and compare against `docs/tabs3-odbc-schema.json`; export should warn on missing/new columns but continue when safe
- [x] Output encrypted or at least password-protected ZIP bundle containing NDJSON/CSV table files plus `manifest.json`
- [x] Manifest includes source system, export timestamp, table list, row counts, per-table checksums, schema hash, export version, and redaction flag
- [x] Never write to Tabs3; all SQL is `SELECT` only; no direct reads from `.dat`/`.idx`
- [ ] Acceptance: schema-only dry run succeeds without exporting client rows; sample lookup-table export creates a manifest and checksums

#### 1502. Phase 1 Tabs3 table groups and batching (P0, MEDIUM) — IN PROGRESS
Define the exact first export surface and make large tables safe to export incrementally.
- [x] Table group `core`: `CLIENT`, `CONTACT`, `BILLTO`, `EMPLOYEE`, `CLIENTNOTE`, `CLIENTCUSTOM`
- [x] Table group `billing`: `FEE`, `COST`, `PAYMENT`, `FUND`, `LEDGER`, `LEDGALLOC`, `ARCHIVE`, `STMTDET`, `STMTDETALLOC`, `STMTTRAK`
- [x] Table group `rates_codes`: `CLIENTRATE`, `COSTRATE`, `TCODE`, `TASKBILLCODE`, `TASKBUDGET`, `BILLFREQ`, `CATEGORY`
- [x] Table group `trust`: `TRUSTREQUEST` plus Trust Accounting `CLIENT`, `BANK`, `COMBINEDTRANS`, `CONTACT`, `RECON` when available/licensed
- [x] Table group `practicemaster_optional`: `CMCLIENT`, `CMRELATE`, `CMRELLNK`, `CMFEE`, `CMCOST`, `CMJRNL`, `CMCAL`, `CMDOCMGT`, `CMDOCVSN`, `CMAUDIT`, `CMXREF`
- [x] Implement streaming export for large tables, especially `ARCHIVE` and `LEDGER`; avoid loading full tables into memory
- [x] Include per-table row limit and `where` clause/date-window options for rehearsal exports
- [ ] Acceptance: a rehearsal can export only `core` and first N rows per high-volume table; full export can resume or rerun cleanly

#### 1503. Customer operator runbook (P0, SMALL) — COMPLETED
Create the instructions a non-developer can follow on the customer server/workstation.
- [x] Document prerequisites: Windows host, Tabs3 ODBC license/driver/DSN, read-only credentials if required, disk space, low-activity export window
- [x] Include ODBC setup checks and how to run schema-only, rehearsal, and full exports
- [x] Explain what data is exported, where the bundle lands, how to securely transfer/upload it, and how to delete local export files after import
- [x] Add risk warnings: ODBC may expose secure/restricted clients; export should be handled as confidential legal/accounting data
- [x] Acceptance: runbook maps exactly to the runner CLI flags and expected output files

### M2 — Cloud Import Staging And Canonical Mapping (P0)

#### 1504. Legacy import spine: raw staging and idempotent links (P0, LARGE) — IN PROGRESS
Add provider-neutral import infrastructure so Tabs3 is not a one-off migration hack.
- [x] Migration: `external_system_connections` with provider (`tabs3`), display name, status, source metadata, accounting mode, last import timestamps, and tenant RLS
- [x] Migration: `external_import_runs` with connection ID, run status, manifest JSON, row counts, checksum summary, errors, approval/promoted timestamps, created_by, and tenant RLS
- [x] Migration: `external_raw_rows` with run ID, provider table, source primary key/hash, row JSON, row checksum, source timestamps where present, and tenant RLS
- [x] Migration: `external_record_links` mapping provider/table/source key to Clarity model/table/record ID, import run ID, confidence/status, and tenant RLS
- [x] Model registration in `models/__init__.py`; schemas use Pydantic v2 `from_attributes`
- [x] Ingestion is idempotent at staging; canonical promotion idempotency is ready through `external_record_links` but promotion is not yet implemented
- [ ] Acceptance: unit tests verify RLS scoping, row checksum uniqueness, and source-key-to-record links

#### 1505. Tabs3 bundle upload and validation API (P0, MEDIUM) — IN PROGRESS
Let admins upload an export bundle safely before any canonical import happens.
- [x] Admin-only endpoint `POST /api/imports/tabs3/upload` accepts ZIP bundle, validates manifest/checksums/schema version, and creates a pending import run
- [x] Store raw rows in `external_raw_rows` before any mapping or promotion
- [x] Reject malformed bundles, checksum mismatches, unsupported export versions, and unexpected table names with actionable error messages
- [x] Provide `GET /api/imports/{run_id}` and `GET /api/imports/{run_id}/tables` for status, counts, validation errors, and staged-table previews
- [ ] Add file-size, row-count, and timeout guardrails; large imports should process asynchronously with resumable run status — file-size guardrail exists, async processing still pending
- [ ] Acceptance: corrupted bundle fails without writing canonical data; valid rehearsal bundle stages rows and exposes table counts

#### 1506. Tabs3 canonical mapping and promotion rules (P0, LARGE) — PENDING
Promote approved staged rows into Clarity records with conservative defaults and full source provenance.
- [ ] Map Tabs3 `CONTACT`/PracticeMaster `CMRELATE` to `contacts`, preserving all extra phones/addresses/category data in tags/notes or source snapshot rather than dropping it
- [ ] Map Tabs3 `CLIENT`/`CMCLIENT` to `matters` plus linked client contact; preserve `CLIENT_ID` as external reference and matter slug seed
- [ ] Map responsible/timekeeper data from `EMPLOYEE`/`CMEMPL` to existing users when email/name/initials match; otherwise create unmapped-timekeeper warnings, not fake users
- [ ] Map `FEE`/`CMFEE` to `time_entries`; map `COST`/`CMCOST` to `expenses`; preserve transaction code, phase/task, source, billed status, QB fields, and original sequence numbers
- [ ] Map `PAYMENT`, `LEDGER`, `ARCHIVE`, `FUND`, and trust tables into payments/trust/opening-balance records only where semantics are clear; ambiguous rows remain staged with reconciliation warnings
- [ ] Map `CLIENTNOTE`/`CMJRNL` notes to `matter_notes` or `matter_events` based on record type; preserve original author/date where available
- [ ] PracticeMaster calendar/task rows map to `tasks` or `scheduled_events` only when dates and matter links are unambiguous
- [ ] Promotion must be previewable and reversible at the run level before go-live; do not hard-delete promoted records on rollback, mark/import-link them for cleanup
- [ ] Acceptance: fixture import creates contacts, matters, time, expenses, notes, and links without duplicates on rerun

### M3 — Reconciliation, Admin UX, And Go-Live Readiness (P0)

#### 1507. Import reconciliation reports (P0, MEDIUM) — PENDING
Before promotion, show whether the staged Tabs3 data balances against Clarity targets.
- [ ] Reconcile active/open client and matter counts
- [ ] Reconcile unbilled fees/time, unbilled costs, payments, client funds/trust balances, AR/open ledger balances, and archived transaction totals
- [ ] Show unmapped clients, contacts, bill-to records, timekeepers, billing codes, trust rows, and ambiguous ledger/archive rows
- [ ] Export reconciliation results as CSV/PDF for customer sign-off
- [ ] Add pass/fail thresholds configurable per run; default is no promotion if core counts/checksums fail
- [ ] Acceptance: seeded fixture totals match expected reports; intentionally bad fixture surfaces mismatches and blocks approval

#### 1508. Admin import UI for upload, review, approval, and promotion (P0, MEDIUM) — IN PROGRESS
Add a web workflow so the import is operable by an admin without developer intervention.
- [x] New Admin/Integrations panel section: "Tabs3 Import"
- [x] Upload bundle, see validation progress, table counts, warnings, and reconciliation summary
- [ ] Preview staged rows by table and sample mapped Clarity records before approval
- [ ] Require explicit admin approval with confirmation text before promotion
- [ ] Show promotion progress, created/updated/skipped counts, and downloadable error report
- [x] Surface accounting mode choice: `clarity_native`, `qbo`, or `tabs3_reference`
- [ ] Acceptance: admin can upload a valid rehearsal bundle, inspect counts/warnings, approve promotion, and see linked created records

#### 1509. Phase 1 tests and rehearsal checklist (P0, MEDIUM) — IN PROGRESS
Make the first customer migration repeatable instead of bespoke.
- [ ] Export runner tests for manifest creation, checksum generation, schema drift handling, row limits, and large-table streaming
- [x] Backend tests for upload validation, staging, encrypted bundles, and checksum failure added in `tests/test_external_imports.py`; execution blocked locally by unavailable Postgres test DB
- [ ] Mapping fixture tests covering at least one row each for `CLIENT`, `CONTACT`, `BILLTO`, `EMPLOYEE`, `FEE`, `COST`, `PAYMENT`, `FUND`, `LEDGER`, `ARCHIVE`, `CLIENTNOTE`
- [ ] Manual rehearsal checklist: schema-only export, redacted sample export, full export, upload, validation, reconciliation, promotion, post-import spot checks
- [ ] Post-import spot checks: random 10 active matters, 10 contacts, 10 ledger/payment histories, 5 trust/client fund balances, 5 timekeeper mappings
- [ ] Acceptance: documented rehearsal can be run end-to-end in staging before touching production tenant data

**Out of scope for Phase 1:** always-on Windows sync service, writeback to Tabs3, direct Clarity Cloud connection into the customer LAN, full QuickBooks Desktop migration, and complete normalization of every historical archive/ledger field into first-class Clarity billing models. Full history is retained in raw/staged rows even when not normalized.

---

## Sprint 14 — Microsoft Teams Integration & Custom Apps

**Goal:** Let firms manage and collaborate with their users via Microsoft Teams, gated to tenants with an active Microsoft 365 integration. Build incrementally from matter↔channel linking + outbound notifications (shipped) up to a full Teams app — one **shared published** Clarity Legal Teams app (single Azure AD registration + single manifest) that each tenant admin-consents/installs into their own M365 tenant; all per-tenant state stays in our DB keyed by `tenant_id`. Design rationale + auth split in the plan; new migrations start at **053**. Cross-cutting: reuse the existing Microsoft `TenantCredential` token vault (`get_fresh_token`, `refresh_microsoft_token`), tenant RLS on all new tables, scope-gated admin endpoints, best-effort dispatch that never blocks requests/jobs.

**Auth split (recommended & in use):** delegated Microsoft Graph (existing `TenantCredential`, provider="microsoft", widened with Teams scopes on opt-in `&teams=1`) powers Phases 1–2 (linking, messaging, tab SSO). A **dedicated Azure Bot** (Bot Framework app id/password) is introduced only in Phase 3 for proactive 1:1/channel messaging and inbound activities — a bot cannot be delegated-only.

**Teams scopes (delegated):** `Channel.ReadBasic.All ChannelMessage.Send Chat.ReadWrite Team.ReadBasic.All TeamsActivity.Send` — kept separate from the base `MICROSOFT_ADMIN_SCOPES` so existing cloud-only tenants are never marked scope-deficient; admins reconsent with `&teams=1`.

### M1 — Channel linking + outbound notifications (P0) — DONE (Phase 1)

#### 1401. Teams Phase 1 — Gating, linking, Adaptive Card notifications — DONE
Delegated Graph only. Shipped on `claude/teams-integrations-custom-apps-9xdbr5`.
- [x] Migration `053_teams_integration`: `teams_channel_links` + `teams_notification_settings` (tenant RLS, FKs CASCADE, unique constraints) — applies + downgrades cleanly through full chain
- [x] Models `TeamsChannelLink` / `TeamsNotificationSetting` registered in `models/__init__.py`
- [x] `services/teams.py` Graph client: `list_joined_teams`, `list_channels`, `send_channel_message` (HTML or Adaptive Card), `build_matter_card`; `_graph_request` wrapper with 429/`Retry-After` backoff
- [x] `services/teams_gate.py`: `require_teams_enabled` dependency (409 not_connected / 403 scopes_missing) + `get_teams_status`
- [x] `services/teams_notify.py`: best-effort dispatcher (own session, never raises) resolving matter links + event routing → posts cards; wired into the docket-watcher deadline alerts in `scheduler.py`
- [x] `routers/teams.py` under `/api/integrations/teams/*`: list teams/channels, link CRUD, notification-settings CRUD, test-message — all gated
- [x] `integrations.py`: `_admin_scopes(teams)` + `&teams=1` opt-in on connect/callback (authorize + token-exchange scope strings kept identical); `teams_connected` / `teams_missing_scopes` added to `IntegrationStatus`
- [x] Frontend: gated `Teams` admin tab + `TeamsPanel.jsx` (connect/reconsent prompt, team→channel pickers, matter link CRUD, send-test); `api.js` Teams group
- [x] Tests `tests/test_teams.py` (12 passing): gating, scope detection, link CRUD + idempotency, dispatch payload, card builder, 429 retry
- [ ] Follow-up: full notification-rules editor UI (backend CRUD exists; panel ships linking + test only)
- [ ] Follow-up: dispatch hooks at additional MatterEvent sites (`matters.py`, `tasks.py`, `calendar.py`, `estates.py`, `domestic.py`) — currently wired at docket-watcher only

### M2 — Shared published Teams app + embedded tab w/ SSO (P1) — Phase 2

#### 1402. Teams app manifest & "custom app" packaging (P1, MEDIUM) — PENDING
One shared, published Clarity Legal Teams app. No per-tenant manifests.
- [ ] New repo dir `teams-app/`: `manifest.json` (Teams schema v1.16+) — single app `id`, `developer`, `name`/`description`, `color.png` + `outline.png` icons, `validDomains` (exact frontend tab host), `webApplicationInfo` `{ id: MICROSOFT_CLIENT_ID, resource: "api://{frontend-host}/{client-id}" }`
- [ ] Configurable/personal tab entry → `{FRONTEND_URL}/teams/tab`; (Phase 3) `bot` block added later
- [ ] Zip build script → `clarity-legal-teams.zip` (sideloadable); **do not commit the zip**
- [ ] Config: consume `TEAMS_APP_ID` (already added) for channel/tab deep links
- [ ] Operator doc: Azure AD `Expose an API` (`api://…/access_as_user`, pre-authorize Teams desktop/web client IDs); import in Teams Developer Portal; install via org app catalog / Teams Admin Center
- [ ] **Risk:** `validDomains` must list the exact tab host and `webApplicationInfo.resource` must match the AAD Application ID URI — most common silent tab/SSO load failure

#### 1403. Embedded Teams tab with SSO (P1, MEDIUM) — PENDING
Render the existing React app as a Teams tab authenticated via Teams JS SSO.
- [ ] Backend `POST /api/teams/sso/exchange` in `routers/teams.py`: validate the Teams JS AAD token (audience = our app, issuer = AAD); map `oid`→`User` via directory-synced identity (`user_sync.py`), fallback `email`/`preferred_username`; match `tid` against the tenant's M365 directory to prevent cross-tenant binding; mint the normal Clarity JWT (`sub`/`tenant_id`/`jti` shape the middleware expects) so the tab reuses existing auth
- [ ] AAD token-validation helper (JWKS fetch/cache, audience/issuer/exp checks)
- [ ] Frontend `pages/TeamsTab.jsx` at `/teams/tab`: load `@microsoft/teams-js`, `app.initialize()` + `getAuthToken()`, POST to SSO exchange, store JWT, render a Teams-themed subset (matter events/tasks)
- [ ] Frontend `pages/TeamsTabConfig.jsx` at `/teams/config`: configurable-tab settings to bind a matter; register both routes
- [ ] Gate tab entry/UI on `microsoft.teams_connected`
- [ ] Tests: SSO exchange happy path + wrong-audience/expired token rejected; `tid` cross-tenant binding refused; identity-mapping fallbacks
- [ ] Verify E2E: upload `clarity-legal-teams.zip`, open tab, confirm SSO maps to correct user/tenant
- [ ] **Risk:** Clarity JWT minting must not widen claims; reuse existing `SECRET_KEY`/`ALGORITHM` + `jti` blacklist semantics

### M3 — Two-way bot: proactive messaging + inbound commands (P2) — Phase 3

#### 1404. Azure Bot registration & conversation references (P2, MEDIUM) — PENDING
Branded, bidirectional bot. Requires app-level Bot Framework credentials.
- [ ] Migration `054_teams_conversation_refs`: `TeamsConversationRef` (`tenant_id`, `user_id` nullable, `aad_object_id`, `conversation_id`, `service_url`, `channel_id`/`team_id`, `conversation_type`, `bot_id`, `raw_reference` JSON; unique `(tenant_id, conversation_id)`; tenant RLS) — register in `models/__init__.py`
- [ ] Config: `TEAMS_BOT_APP_ID`, `TEAMS_BOT_APP_PASSWORD` (+ optional `TEAMS_BOT_TENANT_ID`)
- [ ] `services/teams_bot.py`: Bot Framework adapter — JWT validation, capture/persist `ConversationReference` on first install/message, proactive `continue_conversation`
- [ ] Operator: Azure Bot Service registration; messaging endpoint `{BACKEND_URL}/api/teams/bot/messages`; enable Teams channel; generate app password; add `bot` block to manifest (scopes personal/team/groupchat)
- [ ] **Risk:** proactive messaging can't DM a user the bot has never heard from — persist conversation refs on first contact; degrade to channel post / email when none exists

#### 1405. Bot activities webhook + proactive routing (P2, MEDIUM) — PENDING
- [ ] `POST /api/teams/bot/messages` webhook in `routers/teams.py`
- [ ] Add `/api/teams/bot/messages` to `SKIP_PATHS` in `middleware/tenant.py` (no Clarity JWT inbound); handler validates the **Bot Framework JWT** itself, resolves `tenant_id` from the activity's AAD `tid` → `TenantCredential`/synced user, then **explicitly `set_tenant_context`** before any tenant-scoped query
- [ ] Inbound command handlers (e.g. matter status, log time, deadlines) returning Adaptive Cards
- [ ] `teams_notify.py`: route 1:1 user notifications to bot proactive using stored conversation refs, falling back to channel post / email
- [ ] Tests: webhook auth (invalid/missing Bot Framework JWT → 401; valid → 200 + stores `TeamsConversationRef`); tenant resolution from activity; proactive-send uses stored ref
- [ ] Verify E2E: install app, DM the bot, confirm conversation ref stored + proactive reply
- [ ] **Risk (RLS on webhook):** path has no JWT — blank tenant context makes RLS return zero rows; tenant must be resolved from the activity and set before querying

---

## Sprint 13 — Core Standard Bolster (Practice-Management Parity)

**Goal:** Reach table-stakes parity with Clio / MyCase / PracticePanther on the practice-management core so the AI moat wins deals instead of being disqualified on a feature checklist. All work lands in the **standard** (flat-seat) tier. Full design + data models in [`docs/core-bolster-implementation-plan.md`](docs/core-bolster-implementation-plan.md); rationale in [`docs/competitive-gap-analysis.md`](docs/competitive-gap-analysis.md). New migrations start at **044**. Cross-cutting: RLS on all new tables, audit logging, tier gating via `TenantSettings.features`, Pydantic v2 schemas + `models/__init__.py` registration.

### M1 — Client-facing core (P0)

#### 1301. Client Portal (P0, LARGE) — IN PROGRESS (spike landed)
Generalize the mediation portal (`mediation_portal.py`, `MediationInvite`, `PortalAcceptPage`/`PortalCasePage`) from `MediationCase` to `Matter`.
- [x] Migration `044_client_portal`: `client_portal_invites` (tokenized, sha256 hash, RLS); `portal_visible` on `MatterDocument`, `portal_enabled` on `Matter`; messages reuse `communication_logs` with `channel='portal'`
- [x] `routers/client_portal.py`: `/accept`, `/matter`, `/messages` (get/post), `/documents` (list/upload/download via `matter_file_store`), `/invoices` (surfaces Stripe pay link)
- [x] Firm-side invite create/list/revoke (`firm_router` on `/api/matters/{id}/portal/...`); document portal-visibility toggle via `matter_documents` PATCH
- [x] Frontend: `ClientPortalAcceptPage`, `ClientPortalMatterPage` (Overview/Messages/Documents/Invoices) + routes; Client Portal tab on `MatterDetailPage`; `api.js` group
- [x] Firm UI: portal-visibility toggle control in `MatterDocumentsTab` (Shared/Private badge toggle)
- [ ] Dedicated `/invoices/{id}/pay` portal endpoint (currently links out to existing Stripe payment link)
- [ ] Firm-client login path (role="client") in addition to magic-link (spike is magic-link only)
- [ ] Integration tests: tenant + matter isolation, expired/revoked invite, cross-matter access

#### 1302. Native E-Signature (P0, MEDIUM) — IN PROGRESS (spike landed)
- [x] Migration `045_esignature`: `signature_requests` + `signature_signers` (RLS)
- [x] `services/esign/`: `ESignProvider` interface + `get_provider` factory; `internal` adapter; `dropbox_sign` stub; reportlab certificate generator (HTML fallback)
- [x] `routers/esignature.py`: firm create/list/get/send/void from a `MatterDocument`; client-portal `GET /signatures` + `POST /signatures/{id}/sign`
- [x] On complete → executed-copy/audit PDF stored as portal-visible `MatterDocument` + matter timeline event; request status partially_signed→completed
- [x] Frontend: firm "Request signature" panel in MatterDetail Client Portal tab; Signatures tab + sign action in client portal
- [ ] Real provider wiring (Dropbox Sign/DocuSign) + webhook reconciliation (stub raises NotImplementedError)
- [ ] Portal signer-identity binding (spike signs the next pending signer; bind to the portal contact/email)
- [ ] Decline flow + per-signer email dispatch on send

#### 1303. Trust Accounting — Pooled Ledger & Reconciliation Persistence (Backend) (P0, MEDIUM) — DELIVERED

**RECONCILED (2026-06-13):** The frontend portion of this task was delivered by **[1314](#1314-trust-accounting-frontend-p0-medium--completed)** (portfolio/detail/reconcile UI against the existing per-matter `trust_accounting.py` backend). What remains here is the **backend deepening** that 1314 did not touch — pooled bank accounts, persisted reconciliation snapshots, per-client ledgers, overdraft guardrail, and exports. Re-scoped to backend-only; frontend bullet closed.

- [x] Migration `054_trust_ledger` (renumbered from 046 — chained after Teams `053`): `trust_bank_accounts` (pooled, RLS), `trust_accounts.bank_account_id` FK, `trust_reconciliations` (saved snapshots, RLS). Applied to head on deploy 2026-06-13.
- [x] Backend (in `routers/trust_accounting.py`): pooled bank-account CRUD (`/api/trust/bank-accounts`); pooled three-way reconcile (`bank == book == Σ client ledgers`) with **persisted snapshots**; reconciliation history; per-client ledger statement (`/accounts/{id}/statement`) + **CSV export**; both reconcile endpoints now persist `TrustReconciliation` rows. Models registered in `models/__init__.py` (closed BK05 gap).
- [x] Overdraft guardrail: pre-existing per-account debit block confirmed + regression test added.
- [x] **Firm branding + branded PDF export (2026-06-13):** migration `055_firm_branding` adds branding columns to `tenant_settings` (firm name/logo/address/phone/email/website/PDF-footer); `GET/PUT /api/firm/branding` (`routers/firm.py`, PUT admin-gated, name/address fall back to the tenant record); `services/trust_statement_pdf.py` renders a branded statement (logo fetched best-effort, skipped on failure); `?format=pdf` wired into the statement endpoint. CSV export also shipped. Verified: `test_firm_branding.py` (branding GET/PUT + PDF export) + full trust suite = **16/16 pass** on deploy.
- [x] **Frontend (2026-06-13):** `FirmBrandingPanel` in the Admin Settings tab (admin-gated via the existing `adminOnly` route) edits all 7 branding fields; "Download PDF" button on the `TrustAccountDetail` ledger view streams the branded statement. `api.js`: `getFirmBranding`/`updateFirmBranding`/`downloadTrustStatementPdf`. Frontend build passes; deployed live.
- [x] Tests: `tests/test_trust_ledger.py` — **9/9 pass** against real Postgres on deploy (bank CRUD, ledger linking/book-balance, pooled reconcile balanced+unbalanced, statement + CSV, overdraft, per-account snapshot persistence, tenant isolation).
- [x] Frontend: `TrustAccountingPage` + route + sidebar nav; ledger views; Reconcile screen; trust balance card on `MatterDetailPage`; `api.js` group — **delivered by 1314 (2026-06-13)**

**Side-effect fix (2026-06-13):** added a `UUID→str` coercion mixin to the trust response schemas, fixing a **pre-existing latent bug** — `TrustAccountResponse`/`TrustTransactionResponse` declared UUID fields as `str` without coercion, so the 1314 create/list/transaction flows would have 500'd in production (never caught: trust was headless until 1314 and had no E2E test). Now verified by the new test suite.

**Follow-on for the UI (after backend lands):** surface pooled-account grouping, saved-snapshot history, per-client ledger statement view, and CSV/PDF export buttons on the 1314 pages.

### M2 — Intake & litigation (P1)

#### 1304. Public Intake Forms + Online Scheduling (P1, LARGE) — PENDING (REFINED)

**AUDIT RESULT (2026-06-12):** Spec conflates two features (form builder + scheduling). Scope split into Phase 1 (small–medium, competitive gap) and Phase 2 (scheduling + conditional logic, deferred). Phase 1 unblocks lead capture from public web.

**Phase 1 (P1, SMALL–MEDIUM) — Public Intake Form → Auto-Lead**
- [ ] Migration `047_intake_forms`: `intake_forms` (public slug, schema JSON), `intake_form_submissions`
- [ ] `routers/intake_forms.py`: firm CRUD + public `GET/POST /public/intake/{slug}` → create Contact+Lead + notify assigned attorney; rate-limit/spam protection
- [ ] Frontend: `IntakeFormsPage` builder (JSON schema editor); public form renderer; submissions surface in `IntakePage`
- [ ] No conditional logic (v1), no scheduling

**Phase 2 (P1, MEDIUM, DEFERRED) — Conditional Logic + Online Scheduling**
- [ ] Conditional field visibility (show/hide fields based on selections)
- [ ] Public scheduling from synced calendars (consult slots → calendar event creation)
- [ ] Scheduled post-sprint 13; design deferred to reduce scope creep

#### 1305. Court-Rules Deadline / Docketing Engine (P1, MEDIUM) — DROPPED

**DROPPED (2026-06-13):** LawToolBox path abandoned. No customer demand pull; not pursuing commercial deadline-engine integration at this time. Revisit only if litigation firms explicitly cite court-rules deadlines as a blocker. Research artifacts retained in `docs/research/1305-*.md` for reference.

#### 1306. Two-Way SMS / Text (P1, SMALL–MEDIUM) — PENDING
- [ ] Migration `049_sms`: SMS fields on `communication_log` (`external_id`, `direction`, `from_number`, `to_number`); tenant Twilio config
- [ ] `services/sms.py` (Twilio) send + inbound webhook → `CommunicationLog` matched by phone; `routers/communications.py` send + webhook
- [ ] Frontend: SMS thread + composer on `CommunicationsPage` + `MatterDetailPage`

### M3 — Efficiency & depth (P1/P2)

#### 1307. No-Code Workflow Automation (P1, LARGE) — PARKED

**AUDIT RESULT (2026-06-12):** Spec is vague (events/actions/UI undefined). User demand indirect (competitive gap analysis, no customer requests). P1 competing tasks (1304, 1305, 1306) on critical path first. **PARKED** pending 1301–1306 stabilization + user interviews.

**Next steps (Sprint 14 or later):**
- Gather attorney interviews: "What manual repetitive workflows slow you down most?"
- If intake (1304) or deadlines (1305) unlock new firms, they'll ask about automation
- Refine spec: list top 5 events (matter_created, task_completed, invoice_sent, payment_received, document_signed) + 5 actions (create_task, send_email, update_matter_status, notify_client, create_event)
- Propose MICRO scope for Phase 1: manual-only triggering, task auto-creation, no scheduling, JSON editor UI

**Design (DEFERRED):**
- [ ] Migration `050_workflows`: `workflows`, `workflow_actions`, `workflow_runs`
- [ ] `services/workflow_engine.py` (domain events → actions); `routers/workflows.py` CRUD + manual run + history
- [ ] Frontend: `WorkflowsPage` builder + run log

#### 1308a. Document Automation — Native DOCX/PDF Assembly (P1, MEDIUM) — REFINED

**AUDIT RESULT (2026-06-12):** User demand exists (Tabs3/Smokeball parity). Existing text templates (v0.8.0) as foundation. Scope clear: python-docx for Phase 1, Jinja2+pandoc for Phase 2. **PROMOTED TO P1** (was P2).

**Phase 1 (P1, MEDIUM, 2 weeks)** — DOCX Upload → Field Mapping → Render
- [ ] Migration: reuse existing `DocumentTemplate` table; add `template_type` (text/docx) + `docx_file_path`
- [ ] Backend: `services/document_automation.py` — read DOCX with python-docx, extract form fields, render with variable substitution, save to matter
- [ ] `routers/document_templates.py` extension: `POST /templates/{id}/render` endpoint (maps field_name → {{variable}} → substitutes from matter/contact data)
- [ ] Frontend: `TemplatesPage` enhancement — DOCX uploader, field mapping UI (highlight placeholders in preview), render + download
- [ ] Acceptance: Upload sample engagement letter DOCX, map 5 fields (client_name, attorney_name, effective_date, scope, fee_rate), render → download works

**Phase 2 (P2, LARGE, DEFERRED)** — Conditional Logic + PDF
- [ ] Conditional rendering: {{#if}} blocks, {{#each}} loops (Jinja2)
- [ ] PDF generation: DOCX → HTML → WeasyPrint or pandoc
- [ ] Field types: signature blocks, date pickers, dropdowns
- [ ] Scheduled post-Phase 1

#### 1308b. Reporting/BI — Accounting Reports (P1, MEDIUM) — REFINED

**AUDIT RESULT (2026-06-12):** Data foundation complete (TimeEntry, Invoice, Payment models indexed). Zero billing reports frontend. User demand implicit (accounting parity, P2 backlog). **PROMOTED TO P1** (was P2 sub-item).

**Phase 1 (P1, 3–4 days) — DELIVERED 2026-06-13** — 3 Core Reports
- [x] Backend: per-matter SQL aggregations added to `routers/reports.py` (helpers `_realization_report`, `_wip_report`, `_aging_report`):
  1. **Realization Report:** billable hours/amount (TimeEntry WHERE is_billable=TRUE) vs collected (Payment→Invoice), realization %
  2. **WIP (Work-in-Progress):** uninvoiced billable time by matter (TimeEntry WHERE is_billable=TRUE AND invoice_id IS NULL)
  3. **A/R Aging:** outstanding invoice balances bucketed by days overdue (0–30 / 31–60 / 61–90 / 90+)
- [x] `routers/reports.py` endpoints: `GET /api/reports/billing/{realization,wip,aging}`, each tenant-scoped + `?format=csv` export (commit `199823f`)
- [x] Frontend: `ReportsPage` tab bar (Overview / Realization / WIP / A/R Aging), sortable tables, per-report CSV download (commit `042a0b4`)
- [x] Acceptance: deployed to hypervisor 2026-06-13 (commit `b77deab`); **6/6 backend report tests pass** against real Postgres; frontend build passes; live endpoints return 401 unauth (wired). Date-range filters deferred to Phase 2.

**Phase 2 (P2, LATER)** — Advanced Reports
- [ ] Profitability ranking, custom filters, pivot tables, trend charts, realization %, blended rates
- [ ] Real-time dashboard (no longer static reports)
- [ ] Scheduled post-Phase 1

#### 1308c. Contact/Matter Custom Fields + Relationships (P2, LARGE) — PENDING
- [ ] Contact↔contact relationships (e.g., co-counsel, opposing counsel)
- [ ] Matter custom fields (firm-defined fields)
- [ ] Scheduled post-Core Bolster

#### 1308d. Depth & polish — Future items (P2) — PENDING
- [ ] Email-to-matter auto-filing (+ include in conflict search)
- [ ] Conflict-check hardening: indexed partial/phonetic search (rival Tabs3)

**External dependencies to line up early:** e-sign provider (Dropbox Sign/DocuSign, 1302), Twilio account+number (1306).

### M4 — Platform hardening (P1)

#### 1309. Role-Based Access Control & Module Visibility (P1, LARGE)
- [ ] Migration `051_rbac`: `roles` table (user|admin|accounting|partner|attorney|secretary|paralegal + custom subroles), `role_permissions` (module→action mapping), `user_roles` (many-to-many), tenant-level role definitions
- [ ] Backend: `require_role("invoicing")` / `require_role("admin")` dependency replacing ad-hoc checks; per-role middleware; `routers/admin/roles.py` CRUD for role definitions + permission matrix
- [ ] Admin page: role editor UI — define roles, assign permissions per module (billing, matters, documents, calendar, chat, plugins, admin), assign users to roles
- [ ] Custom subroles: firm can clone a base role and toggle individual permissions → saved as tenant-scoped custom role
- [ ] Module visibility gating: admin can disable unpurchased addon modules per tenant; sidebar/route hide disabled modules; `TenantSettings.modules` JSON list of enabled modules; dev override flag to see all modules regardless
- [x] Licensing model refactor: standard license is the required full-platform seat; premium AI is a separate per-user PAYG add-on billed from monthly LLM usage and must not replace the standard seat
- [x] Directory sync licensing policy: Microsoft 365/Google sync imports all active cloud users, defaults them to licensed/seat-billable when tenant policy says so, and lets admins unlicense service accounts or other excluded users
- [x] Unlicensed-user experience: active but unlicensed users can authenticate only into a restricted basic portal with add-on/demo visibility and no matter/chat/core workspace access
- [x] Add-on module purchasing: admin purchase/trial/disable actions must update tenant entitlements, refresh visible modules/routes, and surface billing/subscription state instead of appearing inert
- [ ] Per-user add-on assignment: tenant-purchased add-ons can be assigned to licensed users; assigned users see add-on functions, while all users can still view the add-on modules sales/demo page
- [x] Premium AI assignment: admins can enable premium model access per licensed user; premium usage is metered separately from standard seats with margin-ready cost reporting
- [x] Accountant role: can view and manage billing, invoicing, subscription/licensing, time entry, and financial reporting without full admin access to all tenant configuration
- [x] Intake-only plan: support tenants subscribed only to call intake at $5/user/month; users land on the intake dashboard/widget plus add-on modules page, with chat/matters/core practice pages hidden
- [x] Users admin polish: restore the friendly Active toggle for enabling/disabling users instead of exposing deactivate/reactivate as the primary table action
- [x] Integration admin polish: move optional Zoom meeting setup into its own Admin tab so core Microsoft/Google readiness stays clean when Zoom is not configured
- [ ] Invoicing lock (DONE — hotfix): `generate_invoice`, `update_invoice`, `create_payment`, `export_invoice` gated to `require_admin` in `billing_extended.py`

---

## Sprint 14 — Trust & Accounting Frontends (v0.15.0)

**Goal:** Ship frontend UIs for fully-built backend systems (trust accounting, reporting). Backlog refinement from 2026-06-12 audit completed 1307 (parked) + 1308 split (promote 1308a & 1308b to P1).

### M1 — Accounting & Finance (P1)

#### 1314. Trust Accounting Frontend (P0, MEDIUM) — COMPLETED

**AUDIT PROMOTED (2026-06-12):** Backend 100% complete (9 endpoints in `trust_accounting.py`, migrations 017 + reconciliation logic). Modest frontend scope (~1 week) unblocks accounting workflows. No reason to defer.

**SCOPE NOTE:** This task delivered the frontend against the **existing per-matter** trust backend. Pooled bank accounts, persisted reconciliation snapshots, per-client ledgers, overdraft guardrail, and CSV/PDF export are **out of scope here** and tracked under **[1303](#1303-trust-accounting--pooled-ledger--reconciliation-persistence-backend-p0-medium--pending)**.

- [x] `TrustAccountingPage` + sidebar nav route
- [x] Trust Account Portfolio view: list (name, balance, bank, status), create/close modals, filters
- [x] Trust Account Detail page: balance ledger, transaction history, auto-replenish config, edit/close actions
- [x] Reconciliation screen: bank balance input, outstanding deposits/disbursements, three-way reconcile calc, mark-as-reconciled action
- [x] Matter Detail integration: trust balance card (quick-link to account)
- [x] API client (`api.js`): 9 endpoint wrappers (CRUD accounts, CRUD transactions, reconcile)
- [x] Wiring verified on deploy 2026-06-13 (frontend served 200; `/api/trust/*` return 401 unauth). **Full authed E2E (create account → post transaction → reconcile) not yet run** — needs a logged-in session; no frontend test runner in this project.

**Files:** `frontend/src/pages/TrustAccountingPage.jsx`, `frontend/src/components/TrustAccountDetail.jsx`, `frontend/src/components/TrustAccountReconcile.jsx`, `frontend/src/api.js` (trust group), `frontend/src/App.jsx` (routes), `frontend/src/components/Sidebar.jsx` (nav), `frontend/src/pages/MatterDetailPage.jsx` (trust balance card)

### M2 — Intake & Docketing (P1)

#### 1305. Court-Rules Deadline / Docketing Engine (P1, MEDIUM) — DROPPED

**DROPPED (2026-06-13):** LawToolBox path abandoned (see M2 backlog entry above). Not pursuing commercial deadline-engine integration without explicit customer demand.

---

## Sprint 12 — LiteLLM Gateway & AI Operations Control Plane (v0.14.0)

**Goal:** Make LiteLLM the primary LLM execution gateway for chat, plugin skills, retrieval planning, memory summaries, email drafting, and operator-managed model routing. The LegalApp backend remains the business control plane for tenant policy, RAG/context assembly, legal guardrails, usage records, and support workflows; LiteLLM owns provider abstraction, model aliases, fallback chains, provider health, and gateway telemetry.

**Architecture:** App resolves a logical route (`standard` / `premium` / tenant override) to a LiteLLM model alias such as `clarity-standard` or `clarity-premium`. App sends legal context and metadata to the LiteLLM OpenAI-compatible proxy. LiteLLM executes provider routing/fallbacks and returns provider telemetry where available. App records requested route, resolved route, actual model, tenant/user/matter/plugin metadata, tokens, cost, error class, and gateway request IDs.

### 1201. LiteLLM Gateway Foundation (P0, MEDIUM) — COMPLETED
- [x] Add app config: `LITELLM_ENABLED`, `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `LITELLM_STANDARD_MODEL`, `LITELLM_PREMIUM_MODEL`
- [x] Add `litellm` as a valid LLM provider in app routing and operator console provider metadata
- [x] Add LiteLLM OpenAI-compatible chat + streaming execution path in `LLMService`
- [x] Add `/models` fetch for LiteLLM in platform provider list, with configured alias fallback
- [x] Add LiteLLM service to Docker Compose with config mount and Postgres dependency
- [x] Add starter `litellm_config.yaml` with `clarity-standard` and `clarity-premium` aliases
- [x] Add dedicated `litellm-postgres` service and local inspection port
- [x] Define standard/premium fallback profiles in `litellm_config.yaml`
- [x] Add production secret docs for LiteLLM master key, DB password, and provider keys

### 1202. LiteLLM-Only Routing Refactor (P0, LARGE) — CLAIMED

**Claimed:** 2026-06-05 — Codex. Replanned after LiteLLM deployment: LegalApp must stop acting as a provider router. All LLM execution goes through LiteLLM aliases; the app only resolves logical routes, assembles legal context, applies guardrails, and records audit/billing metadata.

- [x] Refactor `LLMService` to a concise LiteLLM OpenAI-compatible client only; remove direct DeepSeek/OpenCode/OpenRouter/Anthropic/Azure/Gemini execution paths from backend code
- [x] Replace provider-first route resolution with logical route resolution: `standard`, `premium`, `tenant-standard`, `tenant-premium` → LiteLLM aliases (`clarity-standard`, `clarity-premium`, tenant override aliases)
- [x] Persist requested route, resolved route, gateway alias, gateway request ID/fallback metadata when available, and model used on `usage_records`
- [x] Include resolved LiteLLM alias in cache keys for all LLM-backed flows
- [x] Route chat, stream chat, plugin skills, cold-start interviews, memory summaries, retrieval planner, prompt tests, and email agent through one resolver/gateway path
- [x] Update platform/admin UI and API language from provider selection to gateway alias override
- [x] Remove direct-provider fallback from backend app; provider failover belongs inside LiteLLM config

### 1206. Provider Route Builder — Intuitive AI Routing Console (P0, LARGE) — COMPLETED

**Goal:** Replace manual `litellm_config.yaml` edits with a UI-driven provider console. Operator picks a provider endpoint (OpenCode zen, OpenCode go, OpenRouter, DeepSeek direct, etc.), selects an API key from the stored key vault, fetches the provider's live model list, picks a model, and saves it as the standard/premium route. LiteLLM config is regenerated and hot-reloaded automatically.

- [x] Backend: `GET /api/platform/llm/providers` — returns list of known provider presets (name, base_url, models_endpoint, auth_scheme)
- [x] Backend: `POST /api/platform/llm/provider-keys/{id}/fetch-models` — proxies `GET {base_url}/models` using the specified key_id; returns model list
- [x] Backend: `GET /api/platform/llm/routes` — returns current standard/premium route config (provider, key_id, model, fallbacks)
- [x] Backend: `PUT /api/platform/llm/routes` — saves route config, hot-reloads LiteLLM via POST /config/update
- [x] Backend: `POST /api/platform/llm/routes/test` — fires a synthetic prompt against the configured route, returns latency + model used
- [x] Backend: Key vault CRUD (`GET/POST/DELETE /api/platform/llm/provider-keys`) with Fernet encryption
- [x] Backend: `POST /api/platform/llm/provider-keys/sync-env` — imports DEEPSEEK_API_KEY/OPENROUTER_API_KEY from env into vault
- [x] Backend: migration 045 for `llm_provider_keys` table
- [x] Frontend: AI Routing tab with KeyVaultPanel (key CRUD + sync-env) and RouteCard (provider/key/model select + fallback chain + test)
- [x] Provider presets: opencode-zen, opencode-go, openrouter, deepseek, anthropic
- [x] Follow-up hardening: route readiness/validation UI, provider/key mismatch rejection, malformed key ID 400s, blank fallback pruning, LiteLLM fallback mapping update payload, and Anthropic native prefix/test support
- [x] Follow-up UX cleanup: remove legacy global provider picker, show app alias → LiteLLM alias → upstream provider/model/key flow, simplify tenant overrides to aliases, surface 403/model-fetch errors, and sync app aliases on route save
- [x] Model catalog/load balancing: live provider model refresh across stored keys, free/new model tags, direct model-to-route actions, and additional balanced primary deployments under the same LiteLLM alias
- [x] Regression fix: AI Routing load no longer auto-probes provider `/models` endpoints for saved route keys; provider model fetches now happen only from explicit Models/Refresh actions, while route editors use the cached catalog by default
- [x] Regression fix: chat footer guidance now appends the "Prepared for..." attorney-review line only for legal-analysis/legal-drafting responses, not every ordinary chat response
- [x] Regression fix: LiteLLM route save/reload now runs with `STORE_MODEL_IN_DB=True` in compose so `/config/update` can persist UI-managed model changes

Files: `backend/app/routers/platform_llm.py`, `backend/app/models/llm_provider_key.py`, `backend/migrations/versions/045_llm_provider_keys.py`, `frontend/src/pages/PlatformPage.jsx`, `frontend/src/api.js`

### 1203. Operator Console — AI Operations (P0, LARGE) — PENDING
- [ ] Add AI Operations tab with global standard/premium aliases and per-tenant override table
- [ ] Add model/provider disable switch with immediate route validation
- [ ] Add model test action using synthetic prompt and no tenant data
- [ ] Show recent LLM failures by tenant, route, provider, model, status code, and latency
- [ ] Instrument LiteLLM response metadata/headers so `UsageRecord.gateway_fallback_count`, `final_model`, and gateway request id reflect actual fallback/load-balanced deployment used
- [ ] Show fallback activity and provider health summary from LiteLLM telemetry
- [ ] Add short-retention debug mode toggle per tenant/conversation with explicit audit entry
- [ ] Add CI-safe DB-backed regression for platform LLM route saves covering `llm_route_config_v2`, app alias sync, and LiteLLM hot-reload payload shape

### 1204. Gateway Audit, Privacy, and Retention (P0, MEDIUM) — DONE
- [x] Disable raw prompt/response logging in LiteLLM by default
- [x] Send metadata only: tenant_id, user_id, conversation_id, operation_type, matter_id, plugin, skill, premium flag
- [x] Define retention windows for gateway logs, spend logs, and debug logs
- [x] Add operator audit entries for route changes, provider disables, tenant debug mode, and model tests
- [x] Document legal-data handling rules for LiteLLM logs and callbacks

Summary: LiteLLM callbacks are disabled by default, app-side gateway usage/debug records suppress raw query text unless `GATEWAY_RAW_TEXT_RETENTION_ENABLED=true`, chat sends metadata-only LiteLLM request metadata, and `docs/litellm_gateway.md` documents handling and retention defaults. Operator audit logs now capture metadata-only `llm.routes_saved`, `llm.provider_disabled`, and `llm.model_tested` entries. The tenant debug-mode UI remains part of task 1203, but the metadata-only `operator_debug_mode_audit_payload()` contract is in place for its `llm.debug_mode_changed` event.

### 1205. Cutover and Rollback (P1, MEDIUM) — PENDING
- [ ] Add shadow route logging to compare current direct-provider path vs LiteLLM alias
- [ ] Canary selected tenants through LiteLLM standard route
- [ ] Canary premium route through LiteLLM
- [ ] Add rollback playbook: switch global route to direct provider or emergency alias
- [ ] Remove direct-provider default once LiteLLM stability is proven

## Backlog — Legal MCP Database & CourtListener Ingest Pipeline — IN PROGRESS

**Status:** Implementation scaffold complete. Full architecture spec in `docs/legal_rag.md`; operational runbook in `docs/courtlistener_mcp_jetson.md`. A standalone `mcp-server/` package now provides MCP-owned schema, seven domain-scoped tools, S3 bulk snapshot staging/loading, opinion chunking, a low-volume sync placeholder, Jetson embedding dispatcher, and an mxbai-1024 `opinion_chunks` worker. `docker-compose.courtlistener-mcp.yml` defines the separate `courtlistener-db`, `courtlistener-mcp`, loader, sync, and embedding dispatcher stack for `skynet` Docker storage. The main backend MCP router proxies to `MCP_SERVER_URL` when configured and keeps local API-key management/fallback behavior.

- [x] Standalone MCP/vector stack scaffolded separately from the main LegalApp compose
- [x] MCP-owned schema: `courts`, `dockets`, `opinion_clusters`, `opinions`, `opinion_citations`, `opinion_chunks`, `ingest_runs`, `embedding_jobs`
- [x] Seven tool manifest/handlers: search, details, citation lookup/network, jurisdiction, recent authority, court info
- [x] CourtListener S3 staging + core CSV load + opinion chunk creation commands
- [x] MVP corpus filter for release: ND/MT/MN/SD state authority, SCOTUS, U.S. Tax Court, BIA, and regional bankruptcy/BAP courts; `--load-mvp` keeps published/precedential clusters by default
- [x] Production kickoff: `courtlistener-db` schema initialized on `skynet`, `courtlistener-mcp` started healthy, S3 snapshot staged into the Docker bulk volume, and the loader image rebuilt with partial-download validation
- [x] Production smoke import: loaded 1,000 MVP dockets, 130 clusters, 20 real opinions, and 237 chunks from the staged S3 corpus; live MCP `search_caselaw` returns regional case-law chunks
- [x] MVP corpus expansion: loaded 50,000 dockets, 2,103 clusters, 500 opinions, and 5,024 chunks; live search now returns ND/MT/MN/SD, SCOTUS, and Tax Court hits
- [x] Loader fixes from production smoke: CourtListener bulk CSV parsing now handles backslash-escaped multiline fields, uses Harvard XML/html fallback text, supports table-specific smoke limits, and prefers `lbzip2` for faster `.bz2` streams
- [x] Loader citation-map filter fixed so trimmed citation imports require both local opinion endpoints before inserting FK-constrained citation edges
- [x] Jetson worker refactored to mxbai-1024 and `opinion_chunks`; legacy launcher kept as compatibility wrapper
- [x] Jetson 3 embedding dispatcher relaunched against the expanded chunk set through reverse-tunnel mode
- [x] Operator handoff documented in `docs/courtlistener_mcp_operations.md` with live topology, load/embedding commands, counts, recovery steps, and known CourtListener pitfalls
- [x] Backend proxy setting `MCP_SERVER_URL`
- [x] Env/memory hygiene: concrete hypervisor, Jetson, and legacy-source connection details moved behind env-variable names; memory docs scrubbed
- [x] Hardware smoke: Jetson 3 SSH, CUDA/PyTorch, SSD-backed worker directory, reverse-tunnel DB path, and LAN query-embedding service verified from the hypervisor.
- [x] Production embedding completion: Jetson 3 embedded all 5,024 expanded chunks with 1024-dim mxbai vectors; live MCP and chat retrieval smokes passed after embeddings reached 100%.
- [x] Vector search wiring: `search_caselaw` now uses Jetson-backed query embeddings plus pgvector/FTS hybrid ranking when `MCP_QUERY_EMBEDDING_URL` is configured, with FTS fallback when unavailable.
- [x] Pre-merge compose hardening: CourtListener DB password is required and the default bind address is local-only; LAN exposure must be explicit via env.
- [x] MCP tool expansion: expose full opinion retrieval, similar-case search, citation validation/normalization, authority treatment, court coverage, docket search, research bundle export, and sync/corpus status tools.
- [x] MCP contract fix: require exactly one of `opinion_id` or `cluster_id` for `get_case_details`.
- [ ] Multi-Jetson expansion: add/confirm Jetson 1/2 env, SSH keys, CUDA/PyTorch, SSD/cache paths, and LAN reachability, then relaunch dispatcher with all workers.

---

## Recently Completed

### 1111. Operator Console — Error Diagnostics & API Traffic Logs (P0, LARGE) — COMPLETED
- [x] Fixed `LIMIT is not defined` ReferenceError
- [x] Masked user emails in tenant detail view
- [x] Platform error log endpoints: `GET /api/platform/logs` (cross-tenant, paginated, filterable), `/logs/summary`, `/logs/tenant/{id}`, `/logs/tenant/{id}/summary`
- [x] `ApiAccessLog` model (migration 038) + middleware logging every request (metadata only)
- [x] Platform access log endpoints: `GET /api/platform/access-logs`, `/access-logs/summary`
- [x] Logs tab in operator console: System Errors, Tenant Logs, API Traffic sub-tabs

### Cloud Drive Integration Fix — COMPLETED
- [x] Fixed Google Drive scope: `drive.readonly` → `drive` (write ops were 403 silently)
- [x] Fixed Microsoft scope: `Files.Read.All` → `Files.ReadWrite.All`
- [x] `_ensure_cloud_root()` auto-backfill on admin re-auth
- [x] `POST /api/integrations/cloud-init/retry` endpoint with matter folder backfill
- [x] `cloud_init.py`: matter folders store `url` for both providers
- [x] `MatterDetailPage.jsx`: Cloud Storage links in Case Details

### 1109. Calendar Sync — Multi-User Sync Fix (P0, MEDIUM) — COMPLETED
- [x] Per-user sync error logging in `token_vault.py`; `RuntimeError` → `ValueError` in `calendar_sync.py`
- [x] Return 401 with readable message in `email_agent.py`
- [x] `CalendarPage` shows sync button + success/error banner

### 1110. Mobile Responsive UI Overhaul (P1, LARGE) — COMPLETED
- [x] Sidebar: hamburger button + overlay on mobile
- [x] ChatPage, MatterDetailPage, AdminPage, MatterPortfolioPage: responsive padding, scrollable tabs
- [x] ChatInput: iOS safe-area bottom padding; model selector hidden on small screens

### 1112. AppShell Layout — Restore Consistent UI (P0, LARGE) — COMPLETED
- [x] Create `AppShell.jsx` with shared sidebar (always-on desktop, overlay mobile) + top header bar
- [x] Prominent Admin button (Shield icon) in top-right for admin users across all pages
- [x] `AppShellContext` for shared conversations/documents state
- [x] `ShellRoute` wrapper composing `ProtectedRoute` + `AppShell` in App.jsx
- [x] Refactor ChatPage to use shared context; remove direct sidebar rendering
- [x] Collapsible admin tab bar with toggle + dropdown picker
- [x] Remove redundant `min-h-screen` wrappers from key pages

### 1113. Bug Fixes — Calendar, Estate, Time Tracking (P0, MEDIUM) — COMPLETED
- [x] Calendar page: single "Sync Calendar" button auto-detects connected provider (microsoft/google)
- [x] Estate creation: map human-readable types (Probate → probate) to match backend schema, fix 422
- [x] Time tracking: hide hourly_rate from non-admin users; use `user.default_billing_rate` as default
- [x] Time tracking: add `Rate` column (inline-edit) to admin Users tab for `default_billing_rate`
- [x] Reports: make `budget_currency` Optional in schema with "USD" default (fix potential 500)

### 808. Skills Expansion — Legal Prompt Recovery (P1, LARGE) — COMPLETED
- [x] Confirmed recovered prompt constants exist in `backend/app/services/plugins/prompts.py`
- [x] Added detailed workflow-based prompt templates with output formats for new skills
- [x] Expanded `ALL_DEFAULT_PROMPTS` across practice-area plugins for executor auto-build
- [x] Added Family/Domestic Law, Criminal Defense, and Real Estate practice areas
- [x] Left stale stash copy of `frontend/src/api.js` out because it predates current provider/admin API helpers

---

## Future

### Archived (2026-06-12)

**Native Mobile Apps (XL)** — ARCHIVED with conditional revisit trigger
- ✗ No customer demand signal; responsive-web ship (Sprint 12, task 1110) covers table-stakes mobile UX
- ✗ Competitive pressure rated P2 (competitive-gap-analysis.md) — not a switching blocker
- ✗ P0/P1 roadmap (client portal, e-sig, trust accounting, docketing, intake, SMS, workflows) takes priority
- **Revisit Q4 2026 IF:** (1) ≥3 customer requests cite iOS/Android as decision blocker, (2) major competitor mobile-exclusive feature ships, (3) analytics show >15% iOS/Android traffic, or (4) team has post-Sprint-14 capacity

### Deferred (revisit later)

- [ ] **Time tracking advanced:** allow rate override on invoice creation screen for admin
- [ ] ~~**Templates overhaul:** support PDF/DOCX native templates~~ — MOVED TO 1308a (Document Automation, P1)

---

## Backlog — Integration Health & Module Restoration

### Integration Remediation Phase 1 — Reliability Core (P1, LARGE)
- [x] Add shared provider HTTP clients with default timeout, Retry-After/429 handling, transient retry, and typed provider exceptions
- [x] Consolidate token refresh behavior with row locking, transient retry, rotated refresh-token persistence, and health updates
- [x] Migrate representative provider service callers onto the shared HTTP layer with focused tests
- [x] Keep remaining raw provider-call migrations explicitly tracked in the remediation plan
- [x] Run focused integration reliability tests and backend compile

Summary: added `provider_http.py`, `graph_client.py`, and `google_client.py`
with bounded provider HTTP timeout/retry behavior, `Retry-After` handling for
429s, transient 5xx/transport retry, and typed provider exceptions. Migrated
Microsoft/Gmail mail readers off raw no-timeout `httpx` calls and added focused
provider-client/mail tests. `token_vault.py` now uses one refresh path for
Microsoft, Google, and Zoom, wraps stale-token check/refresh in `SELECT ... FOR
UPDATE` with a bounded Postgres lock timeout, retries transient token endpoint
failures, persists rotated refresh tokens, and records token health. Remaining
raw-provider migrations for storage/calendar/directory/search stay tracked in
`docs/integrations-remediation-plan.md` Phase 3+ because they need broader
storage semantics and typed router mapping. Verification: backend compile
passed; focused reliability/observability tests passed with 22 passed and one
Postgres-only concurrency regression skipped locally unless `TEST_DATABASE_URL`
is set.

### Integration Remediation Phase 2 — Observability Spine (P1, LARGE)
- [x] Add `integration_sync_runs` model/migration and helpers for provider/job sync reporting
- [x] Persist OAuth callback scope audit results and expose missing scopes
- [x] Wire integration scheduler failures into admin-visible `ErrorLog`
- [x] Add admin integration health data/cards backed by token health and recent sync runs
- [x] Add focused backend/frontend tests and update the remediation plan status

Summary: added migration `082_integration_observability` (renumbered from `075` after reconciling with `main`'s migration chain) with token-health/scope-audit columns on tenant and user OAuth credential tables plus tenant-scoped `integration_sync_runs`. Microsoft/Google OAuth callbacks now persist missing-scope audit results. Token refresh failures record `last_refresh_error`, and `invalid_grant` marks credentials revoked/inactive. Cloud/user/correspondence integration scheduler branches now write sync-run rows and admin-visible `ErrorLog` entries for per-tenant failures. Admin integration responses and cards show token health, refresh errors, reconnect state, and recent sync runs. Verification: backend compile passed, focused observability/token tests passed, frontend production build passed; DB-backed readiness tests remain blocked locally by refused Postgres connection.

### Integration Remediation Phase 3 — Storage Correctness (P1, LARGE)
- [x] Add durable cloud storage metadata to matter documents
- [x] Return structured provider storage results from matter file uploads
- [x] Persist provider object IDs/drive IDs/parent IDs for new uploads
- [x] Implement cloud document delete for Google Drive, OneDrive, and SharePoint
- [x] Add focused storage/delete regression tests and verification

Summary: added migration `083_matter_doc_storage_meta` (renumbered from `076` and shortened from `083_matter_document_storage_metadata`, which at 36 chars exceeded the `alembic_version.version_num` VARCHAR(32) column and failed at deploy time, after reconciling with `main`'s migration chain) and explicit
matter-document storage metadata for provider/backend, provider object ID,
drive ID, parent ID, and storage errors while preserving legacy URL-derived
behavior. Matter file uploads now return a structured `StorageResult` for new
callers and keep the old string wrapper for portal/correspondence compatibility.
New matter document uploads persist provider IDs for OneDrive, SharePoint, and
Google Drive. Cloud-backed delete now deletes by durable provider metadata,
tolerates provider 404s before deleting the DB row, and fails closed for legacy
URL-only rows or provider auth/throttle/errors. Remaining Phase 3 items for
delta sync, recursive indexing, provisioning repair, and backfill stay tracked
in `docs/integrations-remediation-plan.md`. Verification: backend compile
passed and focused storage/reliability/observability tests passed with 36
passed and one Postgres-only concurrency regression skipped locally unless
`TEST_DATABASE_URL` is set.

### Integration Remediation Phase 3.9 — Matter-Scoped Cloud Folder Sync (P1, MEDIUM)
- [x] Add a matter-scoped cloud metadata sync path
- [x] Wire the per-matter sync endpoint/button to the scoped path
- [x] Preserve tenant-wide sync for scheduler/admin jobs
- [x] Add focused regression tests and verification

Summary: added `CloudSyncService.sync_matter_folders()` plus scoped Google
Drive, OneDrive, and SharePoint folder sync helpers that enumerate only the
matter's recorded primary, subfolder, and context folder IDs. The per-matter
`/cloud-folder/sync` endpoint now calls the scoped path instead of tenant-wide
`sync_all`, while scheduler/admin jobs continue using `sync_all`. Verification:
focused cloud integration tests passed with 17 passed, and touched modules
compiled with a test-only `SECRET_KEY`.

### Integration Remediation Phase 3.6a — Google Folder Provisioning Recovery (P1, MEDIUM)
- [x] Harden Google Drive folder ensure against 409/duplicate races
- [x] Preserve existing folder selection semantics
- [x] Add focused regression tests and verification

Summary: `_ensure_gdrive_folder` now mirrors the Microsoft folder recovery
pattern: if folder creation returns 409 after an empty pre-list, it re-lists
the parent and reuses the best matching existing folder instead of surfacing a
hard provisioning failure or inviting duplicate/manual retries. Verification:
focused cloud integration tests passed with 18 passed, and touched modules
compiled with a test-only `SECRET_KEY`.

### Mediation Platform Module Follow-Ups
- [ ] Portal document delete endpoint (backend has no DELETE for portal docs)
- [ ] Proposal accept/reject UI in portal case page
- [ ] End-to-end smoke test: invite → accept → asset submission → attorney approve → send → opposing decision → proposal exchange
- [ ] Wire delete for portal documents after backend DELETE exists

### BK01. Google Workspace — Scope Audit & Directory Sync Fix (P0, MEDIUM)
- [x] Fixed scope audit mismatch: `SCOPES_REQUIRED_GOOGLE` in `admin.py` changed `drive.readonly` → `drive` to match OAuth request
- [x] Added error logging to `refresh_google_token()` in `token_vault.py` (was silent `return None`, now logs status+body like Microsoft)
- [ ] Full scope audit against Google's required OAuth scopes for: directory user read, calendar read/write, Gmail read, Drive read/write
- [ ] Fix 403 on directory sync — likely requires Admin SDK API enabled in GCP Console + OAuth app verification OR test user setup
- [ ] Ensure `https://www.googleapis.com/auth/admin.directory.user.readonly` is requested during admin consent
- [ ] Verify service account / domain-wide delegation if using service-account-based directory access
- [ ] Re-auth flow should request all currently-configured scopes so missing scopes are added on re-authorize
- [x] Add scope diff display in Integrations panel: granted vs required vs missing

### BK02. QuickBooks Online — OAuth Fix (P0, MEDIUM)
- [x] Investigated Content-Type header — `application/x-www-form-urlencoded` IS correct (no typo)
- [ ] Primary issue: `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REDIRECT_URI` all empty in `.env` — endpoint returns HTTP 501
- [ ] Register app at https://developer.intuit.com/ and populate `QBO_*` env vars
- [ ] Validate CSRF state handling (Redis vs in-memory fallback) for QBO connect flow
- [ ] Check QBO environment toggle (sandbox vs production) and token refresh logic
- [ ] Audit `qbo_sync.py` token storage (Fernet encryption) for silent failures

### BK03. Microsoft 365 — Scope Audit & User Sync Fix (P0, MEDIUM)
- [x] Fixed scope audit mismatch: `SCOPES_REQUIRED_MS` in `admin.py` changed `Files.Read.All` → `Files.ReadWrite.All` to match OAuth request
- [ ] 0 users synced likely due to: (a) `MICROSOFT_TENANT_ID` hardcoded to single tenant — verify correctness; (b) `User.Read.All` requires admin consent in Azure AD app registration; (c) users without `mail` or `userPrincipalName` are silently skipped
- [ ] Verify `last_user_sync_*` columns are populated on sync completion
- [x] Add scope diff display in Integrations panel: granted vs required vs missing

### BK04. Mediation Module — Feature Parity Audit (P1, LARGE)
- [x] Audit complete. Module is 97% production-ready. 24 firm + 12 portal endpoints (all real code, zero stubs). 7 models, 4 frontend pages, 34 API functions.
- [x] Backend routers: `mediation.py` (832 lines, 24 endpoints) + `mediation_portal.py` (504 lines, 12 endpoints) — ALL real DB/logic code
- [x] Models: `mediation_cases`, `mediation_case_events`, `mediation_parties`, `mediation_invites`, `mediation_assets`, `mediation_documents`, `mediation_proposals` — all 7 defined with FKs, indexes, relationships
- [x] Frontend: 4 pages built (Portfolio 267L, Detail 503L, PortalAccept 76L, PortalCase 388L) — all tabs functional
- [x] API: 34 frontend functions covering all firm-side + portal-side endpoints
- [ ] **Gap: No alembic migration** — 7 mediation tables have no migration file in `alembic/versions/`. Tables may rely on `Base.metadata.create_all()` or manual creation.
- [ ] **Gap: No sidebar nav link** — users must navigate `/plugins` → PluginsPage card to access mediation
- [ ] **Gap: ProposalStatusUpdate schema unused** — no accept/reject endpoint for proposals despite schema existing
- [ ] **Gap: No document delete on portal side** — firm-side can delete but portal users cannot

### BK05. Trust & Estates Module — Feature Parity Audit (P1, LARGE)
- [x] Audit complete. Backend fully production-ready for both modules. Estate frontend fully built. Trust Accounting has zero frontend.
- [x] Trust Accounting: `trust_accounting.py` (545 lines, 9 endpoints) — all real DB code. Models: TrustAccount + TrustTransaction. Migration 017.
- [x] Estates: `estates.py` (1420 lines, 44 endpoints) — all real DB code. 9 models (Estate + EstateEvent + 7 sub-entities). Migrations 008, 030, 032.
- [x] Estate frontend: PortfolioPage (335L) + DetailPage (565L, 9 tabs) + EstateSubTable (212L) — fully built.
- [x] **RESOLVED (2026-06-12 → DELIVERED 2026-06-13): Trust Accounting frontend shipped as task 1314** — portfolio/detail/reconcile UI deployed against the existing per-matter backend. Pooled-ledger backend deepening tracked under 1303.
- [ ] **Gap: Trust models not in `models/__init__.py`** — TrustAccount/TrustTransaction imported directly by router (cosmetic, not blocking)
- [ ] **Gap: Estate schemas use Pydantic v1-style config** — `class Config: from_attributes = True` vs v2-style `model_config` (cosmetic)

### BK06. Billing — TimeEntryResponse UUID validation crash (P0, SMALL)
- [x] Backend: `billing_extended.py` — `model_validate()` on ORM objects without `from_attributes=True` causes 500 on UUID→str coercion
- [x] Fixed all 10 `model_validate()` calls in `billing_extended.py` (TimeEntry, Expense, InvoiceLineItem, Payment)

### BK07. Time Tracking — Auto-select matter from context (P1, SMALL)
- [x] `MatterDetailPage.jsx` — three "Log Time" / "Go to Time Tracking" buttons now pass `?matter_id=` query param
- [x] `TimeTrackingPage.jsx` — reads `matter_id` from URL, pre-selects in dropdown, auto-opens the Add Entry form

### BK08. Time Tracking — Matters list missing / sort order (P1, SMALL)
- [x] `TimeTrackingPage.jsx` — matters load independently of entries (entries 500 no longer blocks matters)
- [x] Explicit `sort_by=updated_at&sort_dir=desc` on `getMattersV2()` call sorts by recent activity

### BK09. Chat — LiteLLM Gateway Connection Error (P0, SMALL) — COMPLETED
- [x] Root cause: `docker-compose.hypervisor.yml` had no `litellm` or `litellm-postgres` services, but backend routes all LLM calls through LiteLLM
- [x] Added `litellm-postgres`, `litellm` services + `litellm_postgres_data` volume to `docker-compose.hypervisor.yml`
- [x] Added `litellm: service_healthy` to backend's `depends_on` in hypervisor compose
- [x] Redeployed hypervisor: `docker compose -f docker-compose.hypervisor.yml up -d --build`
- [x] Verified production health, LiteLLM/backend/frontend container health, OAuth redirects, cloudflared, and data guard

### BK10. Chat — Matter-linked conversation workflow (P1, SMALL)
- [x] Backend: allow conversation title updates and matter link/unlink updates in one PATCH with tenant validation
- [x] Chat UI: show matter link status, searchable matter picker, open/change/unlink actions
- [x] Matter UI: start and open linked chats through `/chat?conv=...`
- [x] Tests: link, unlink, and cross-tenant matter rejection

### BK11. Platform AI Routing — Free legal model eligibility (P1, SMALL)
- [x] Backend: score free models for legal usability, document/RAG support, and latency eligibility
- [x] Backend: return `legal_eligible`, `legal_tier`, `eligibility_badges`, and `exclusion_reasons` in catalog rows
- [x] Platform UI: add Recommended, Free Legal, All Free, and Excluded catalog tabs
- [x] Tests: recommended legal model, document-capable model, coding-only exclusion, and slow-latency exclusion

### BK12. Shared estate workflow foundation (P0, LARGE)
Planning docs: `docs/probate-estate-workflow-plan.md`, `docs/module-template-index-plan.md`, and `docs/competitive-template-automation-review.md`.
- [ ] Generalize portal checklists so matters can require fee-agreement signature before unlocking optional document uploads, asset inventory, final delivery, and module-specific tasks
- [ ] Add estate source-document records for wills, codicils, trusts, death certificates, asset statements, and miscellaneous uploads
- [ ] Track document-intelligence runs across PDF text extraction, PDF-to-Markdown, OCR, LLM vision, structured extraction, field-level citations, confidence, and attorney verification state
- [ ] Add shared estate party graph records for decedents, heirs, beneficiaries, fiduciaries, next of kin, creditors, court contacts, and relationship edges
- [ ] Add shared asset inventory records with values, ownership/title details, beneficiary designations, debts/liens, date-of-death valuation, and artifact uploads
- [ ] Extend tenant-scoped document templates into global/core and module-specific visibility layers so all matter users keep access to fee agreements, contracts, and correspondence templates without add-on licenses

### BK13. Probate opening packet automation (P0, LARGE)
- [ ] Build portal flow for access link, portal account creation, fee-agreement execution, and optional will/testament and death-certificate upload
- [ ] Define extraction schema for decedent facts, domicile, date of death, fiduciary nominations, heirs, beneficiaries, distributions, bond waivers, witnesses, notary details, and source contradictions
- [ ] Build attorney workbench for cited facts, missing jurisdiction facts, duplicate/uncertain parties, responsible-party proposals, distribution summaries, and filing readiness
- [ ] Generate ready-to-review probate order packets from master generic templates and jurisdiction variants; store as attorney-work-product matter documents until approval
- [ ] Create tenant/module template index with multiple base templates, jurisdiction variants, tenant overrides, default rankings, approval status, and version history
- [ ] Add upload-to-template conversion for DOCX/PDF sources with structure extraction, suggested placeholders, canonical field mapping, preview/diff, confidence labels, and attorney/admin approval before activation

### BK14. Probate inventory, closing, and delivery (P1, LARGE)
- [ ] Unlock client asset-inventory portal after opening order/appointment is documented
- [ ] Let clients list asset category, description, value, ownership, beneficiary designation, liens/debts, valuation method, and statement/artifact uploads
- [ ] Migrate verified inventory into court inventory, accounting, and final-submission templates
- [ ] Close probate matter with final packet lock, attorney print/export, password-protected document ZIP, shared-folder link, separate password delivery event, and audit log

### BK15. Will and testament lifecycle module (P1, LARGE)
- [ ] Add attorney-configurable review cadence with yearly default plus quarterly, semiannual, every-two-years, custom date, and disabled options
- [ ] Let living clients log into the portal to view wills, estate documents, asset lists, account details, approximate values, beneficiary designations, important contacts, and last-confirmed timestamps
- [ ] Generate recurring portal review tasks and attorney dashboard reminders
- [ ] Convert or link a planning profile into probate/trust administration after death, carrying forward verified documents, asset records, contacts, beneficiary hints, and review history for attorney verification

### BK16. Cross-module template index rollout (P1, LARGE)
- [ ] Build a shared template-index platform for module/stage/jurisdiction/template-kind metadata, visibility/entitlement, defaults, approval state, and versioning
- [ ] Preserve `global_core` and `tenant_global` templates for all matter users while gating only specialized module seed packs, module overrides, extraction schemas, and packet automations
- [ ] Define module template families for commercial, privacy, litigation, corporate, employment, product, IP, AI governance, regulatory, family/domestic, criminal defense, real estate, trust/estate, and mediation workflows
- [ ] Implement shared upload-to-template conversion, variable mapping, repeatable sections, preview/diff, approval, activation, rollback, and packet assembly once for reuse across modules
- [ ] Prioritize rollout after probate foundation: litigation, family/domestic, mediation, and real estate first; transactional/compliance modules after the core conversion/index platform is stable

### BK17. Competitive document automation layer (P0, LARGE)
- [ ] Add smart-fill from existing matter, contact, billing, party, asset, deadline, portal-answer, and verified extraction records before asking users to type duplicate data
- [ ] Add AI-fill suggestions with source citations, confidence/review states, stale-value warnings, and attorney approval before finalization
- [ ] Connect generated global/core templates, especially fee agreements and engagement letters, to the e-signature workflow with signer roles, countersignatures, reminders, expiration, decline/void status, and executed-copy/audit PDF storage
- [ ] Add tenant branding for templates, packet cover pages, e-sign emails, client portal screens, delivery notices, logos, colors, letterhead, and disclaimers
- [ ] Add document tracking and lifecycle events for generated, edited, approved, sent, viewed, signed, declined, expired, filed, delivered, and closed states
- [ ] Add immutable template versions, generated-output snapshots, variable provenance, compare/clone/rollback flows, and locks for signed/filed/final documents

Research note (2026-07-08): added
`docs/research/2026-07-08-document-automation-esign-enhancements.md`, comparing
Gavel, Clio Draft, Docassemble, Dropbox Sign, DocuSign, PandaDoc, and Documenso
against Clarity's current Markdown template renderer and internal portal typed
signature flow. Recommended first release: office-ready Template Studio,
smart-fill/provenance, engagement-letter/fee-agreement generation, and Dropbox
Sign provider wiring around the existing e-sign provider interface.

Implementation slice (2026-07-08): added the first Document Automation
workspace pass by reframing the Templates page as a tabbed Document Automation
tool, preserving `/templates` while adding template/generate/e-sign/approval/
branding panes. Backend templates now have additive lifecycle/metadata fields
and `POST /api/templates/{id}/smart-fill-preview` for deterministic suggestions
from matter, linked client contact, attorney, and current user context. Native
e-sign now supports multiple signers, signer roles, sequential signing,
expiration, reminder metadata, decline/void reasons, and portal decline.
