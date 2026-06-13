# TASKS.md

## Bug Fixes — 2026-06-11

### Fixed
- **Time tracking save fails (500/400):** Added client-side validation (hours>0, matter required), error banner display, and save-disable during submit in `TimeTrackingPage.jsx`. Hardened backend `create_time_entry` with UUID validation and rollback-on-error in `billing_extended.py`.
- **Matter creation slow:** Cloud folder provisioning moved to fire-and-forget background task (`_provision_cloud_folders`) using `async_session_maker` — create-matter response no longer blocks on folder creation.
- **Calendar not marking completed tasks:** `CalendarEvent` now includes `is_completed` flag. Completed tasks remain visible on calendar with done styling instead of vanishing. `calendar.py` + `schemas/calendar.py`.
- **Needs Action empty when items due today/tomorrow:** Expanded `needsAction()` to flag due-today items. Added `dueTomorrow()` classifier and "Upcoming" board column in `MatterPortfolioPage.jsx`. Due-tomorrow count shown in My Matters header.
- **Clarity Legal first query not recognized:** Added 150ms settle delay after conversation creation before streaming call in `ChatPage.jsx`. Backend streaming endpoint retries conversation lookup once (200ms). Global `unhandledrejection` handler suppresses orphaned "Cannot respond" errors in `api.js`.
- **Invoicing functions not admin-gated:** Locked `POST /invoices/generate`, `PATCH /invoices/{id}`, `POST /payments`, and `POST /invoices/{id}/export` to admin role via `require_admin` dependency in `billing_extended.py`. Time/expense CRUD remains open for all users to log their work.
- **Google/OAuth, calendar sync, and integration stability:** Normalized Google userinfo scope aliases, fixed new-matter cloud provisioning imports, restored manifest icons, made admin/user integration OAuth redirects honor the configured API base URL, moved post-connect directory sync off the OAuth callback path, and aligned task calendar push with per-user calendar tokens.

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

#### 1303. Trust Accounting Frontend + Reconciliation (P0, MEDIUM) — PENDING
Three-way reconciliation logic already exists in `trust_accounting.py` but is headless (TASKS BK05).
- [ ] Migration `046_trust_ledger`: `trust_bank_accounts` (pooled), `trust_accounts.bank_account_id` (→ client ledgers), `trust_reconciliations` (saved snapshots)
- [ ] Backend: pooled-account CRUD; persist reconciliation snapshots; per-client ledger statement; overdraft guardrail (block negative client ledger); CSV/PDF export; extend reconcile to assert sum-of-client-ledgers == book == bank
- [ ] Frontend: `TrustAccountingPage` + route + sidebar nav; ledger views; Reconcile screen; trust balance card on `MatterDetailPage`; `api.js` group

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

**Phase 1 (P1, 3–4 days)** — 3 Core Reports
- [ ] Backend: `services/reporting.py` — SQL aggregations for:
  1. **Realization Report:** Billable hours + billed amount by matter/period (TimeEntry WHERE is_billable = TRUE)
  2. **WIP (Work-in-Progress):** Unbilled time entries by matter (TimeEntry WHERE invoice_id IS NULL)
  3. **A/R Aging:** Invoices by status + days overdue (Invoice WHERE due_date < TODAY, sorted by age)
- [ ] `routers/reports.py` endpoints: `GET /reports/billing/realization`, `/billing/wip`, `/billing/aging` (with optional ?matter_id=, ?period_start=, ?period_end= filters)
- [ ] Frontend: `ReportsPage` tabs (one per report), date-range filter, CSV download button; query via `api.js`
- [ ] Acceptance: Pull realization report for Q2, see hours vs revenue by matter; export to CSV

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

#### 1309. Role-Based Access Control & Module Visibility (P1, LARGE) — PENDING
- [ ] Migration `051_rbac`: `roles` table (user|admin|accounting|partner|attorney|secretary|paralegal + custom subroles), `role_permissions` (module→action mapping), `user_roles` (many-to-many), tenant-level role definitions
- [ ] Backend: `require_role("invoicing")` / `require_role("admin")` dependency replacing ad-hoc checks; per-role middleware; `routers/admin/roles.py` CRUD for role definitions + permission matrix
- [ ] Admin page: role editor UI — define roles, assign permissions per module (billing, matters, documents, calendar, chat, plugins, admin), assign users to roles
- [ ] Custom subroles: firm can clone a base role and toggle individual permissions → saved as tenant-scoped custom role
- [ ] Module visibility gating: admin can disable unpurchased addon modules per tenant; sidebar/route hide disabled modules; `TenantSettings.modules` JSON list of enabled modules; dev override flag to see all modules regardless
- [ ] Invoicing lock (DONE — hotfix): `generate_invoice`, `update_invoice`, `create_payment`, `export_invoice` gated to `require_admin` in `billing_extended.py`

---

## Sprint 14 — Trust & Accounting Frontends (v0.15.0)

**Goal:** Ship frontend UIs for fully-built backend systems (trust accounting, reporting). Backlog refinement from 2026-06-12 audit completed 1307 (parked) + 1308 split (promote 1308a & 1308b to P1).

### M1 — Accounting & Finance (P1)

#### 1314. Trust Accounting Frontend (P0, MEDIUM) — PENDING

**AUDIT PROMOTED (2026-06-12):** Backend 100% complete (9 endpoints in `trust_accounting.py`, migrations 017 + reconciliation logic). Modest frontend scope (~1 week) unblocks accounting workflows. No reason to defer.

- [ ] `TrustAccountingPage` + sidebar nav route
- [ ] Trust Account Portfolio view: list (name, balance, bank, status), create/close modals, filters
- [ ] Trust Account Detail page: balance ledger, transaction history, auto-replenish config, edit/close actions
- [ ] Reconciliation screen: bank balance input, outstanding deposits/disbursements, three-way reconcile calc, mark-as-reconciled action
- [ ] Matter Detail integration: trust balance card (quick-link to account)
- [ ] API client (`api.js`): 9 endpoint wrappers (CRUD accounts, CRUD transactions, reconcile)
- [ ] E2E smoke test: create account → post transaction → reconcile

**Files:** `frontend/src/pages/TrustAccountingPage.jsx`, `frontend/src/components/TrustAccount{Portfolio,Detail,Reconcile}`, `frontend/src/api.js` (trust group)

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

### 1204. Gateway Audit, Privacy, and Retention (P0, MEDIUM) — PENDING
- [ ] Disable raw prompt/response logging in LiteLLM by default
- [ ] Send metadata only: tenant_id, user_id, conversation_id, operation_type, matter_id, plugin, skill, premium flag
- [ ] Define retention windows for gateway logs, spend logs, and debug logs
- [ ] Add operator audit entries for route changes, provider disables, tenant debug mode, and model tests
- [ ] Document legal-data handling rules for LiteLLM logs and callbacks

### 1205. Cutover and Rollback (P1, MEDIUM) — PENDING
- [ ] Add shadow route logging to compare current direct-provider path vs LiteLLM alias
- [ ] Canary selected tenants through LiteLLM standard route
- [ ] Canary premium route through LiteLLM
- [ ] Add rollback playbook: switch global route to direct provider or emergency alias
- [ ] Remove direct-provider default once LiteLLM stability is proven

## Backlog — Legal MCP Database & CourtListener Ingest Pipeline

**Status:** Design-only. Full architecture spec in `docs/legal_rag.md`. A minimal 2-tool MCP REST endpoint exists in `backend/app/routers/mcp.py` (search_caselaw, get_chunk). The mcp-server/ package, 7 domain-scoped tools, vectordb schema, CourtListener ingest, mxbai-1024 embeddings, nightly scheduler, usage metering, SSE transport, and admin dashboard are not yet built. Tasks tabled until post-Sprint-12.

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
- [ ] Add scope diff display in Integrations panel: granted vs required vs missing

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
- [ ] Add scope diff display in Integrations panel: granted vs required vs missing

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
- [x] **RESOLVED (2026-06-12): Trust Accounting frontend promoted to Sprint 14 as task 1314** — Backend is ready for consumption. Frontend effort ~1 week.
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

### BK09. Chat — LiteLLM Gateway Connection Error (P0, SMALL)
- [x] Root cause: `docker-compose.hypervisor.yml` had no `litellm` or `litellm-postgres` services, but backend routes all LLM calls through LiteLLM
- [x] Added `litellm-postgres`, `litellm` services + `litellm_postgres_data` volume to `docker-compose.hypervisor.yml`
- [x] Added `litellm: service_healthy` to backend's `depends_on` in hypervisor compose
- [ ] Redeploy hypervisor: `docker compose -f docker-compose.hypervisor.yml up -d --build`
