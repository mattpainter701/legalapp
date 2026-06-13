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

#### 1303. Trust Accounting Frontend + Reconciliation (P0, MEDIUM) — PENDING
Three-way reconciliation logic already exists in `trust_accounting.py` but is headless (TASKS BK05).
- [ ] Migration `046_trust_ledger`: `trust_bank_accounts` (pooled), `trust_accounts.bank_account_id` (→ client ledgers), `trust_reconciliations` (saved snapshots)
- [ ] Backend: pooled-account CRUD; persist reconciliation snapshots; per-client ledger statement; overdraft guardrail (block negative client ledger); CSV/PDF export; extend reconcile to assert sum-of-client-ledgers == book == bank
- [ ] Frontend: `TrustAccountingPage` + route + sidebar nav; ledger views; Reconcile screen; trust balance card on `MatterDetailPage`; `api.js` group

### M2 — Intake & litigation (P1)

#### 1304. Public Intake Forms + Online Scheduling (P1, LARGE) — PENDING
- [ ] Migration `047_intake_forms`: `intake_forms` (public slug, schema JSON, conditional logic), `intake_form_submissions`
- [ ] `routers/intake_forms.py`: firm CRUD + public `GET/POST /public/intake/{slug}` → create Contact+Lead, notify attorney, optional conflict pre-check; public scheduling from synced calendars; rate-limit/spam protection
- [ ] Frontend: `IntakeFormsPage` builder; public form + booking render; surface submissions in `IntakePage`

#### 1305. Court-Rules Deadline / Docketing Engine (P1, MEDIUM) — PENDING
- [ ] Migration `048_deadlines`: `deadline_rulesets`, `matter_deadlines`; migrate `Matter.key_dates` JSON → rows
- [ ] Phase 1: `services/docketing.py` LawToolBox client (trigger + jurisdiction + date → deadline chain); `routers/deadlines.py` CRUD + calculate-from-trigger; hook task-reminder scheduler
- [ ] Frontend: Deadlines section on `MatterDetailPage`; surface on `CalendarPage`
- [ ] Phase 2 (later): evaluate native engine seeded from CourtListener

#### 1306. Two-Way SMS / Text (P1, SMALL–MEDIUM) — PENDING
- [ ] Migration `049_sms`: SMS fields on `communication_log` (`external_id`, `direction`, `from_number`, `to_number`); tenant Twilio config
- [ ] `services/sms.py` (Twilio) send + inbound webhook → `CommunicationLog` matched by phone; `routers/communications.py` send + webhook
- [ ] Frontend: SMS thread + composer on `CommunicationsPage` + `MatterDetailPage`

### M3 — Efficiency & depth (P1/P2)

#### 1307. No-Code Workflow Automation (P1, LARGE) — PENDING
- [ ] Migration `050_workflows`: `workflows`, `workflow_actions`, `workflow_runs`
- [ ] `services/workflow_engine.py` (domain events → actions via APScheduler); `routers/workflows.py` CRUD + manual run + history
- [ ] Frontend: `WorkflowsPage` trigger→action builder + run log

#### 1308. Depth & polish (P2) — PENDING
- [ ] Document automation overhaul: native DOCX/PDF assembly with field mapping (supersedes text-only templates)
- [ ] Contact/matter custom fields + contact↔contact relationships
- [ ] Email-to-matter auto-filing (+ include in conflict search)
- [ ] Conflict-check hardening: indexed partial/phonetic search (rival Tabs3)
- [ ] Reporting/BI: realization/collection, WIP, A/R aging, matter profitability
- [ ] Native mobile apps (XL) — deferred

**External dependencies to line up early:** e-sign provider (Dropbox Sign/DocuSign, 1302), LawToolBox commercial API (1305), Twilio account+number (1306).

### M4 — Platform hardening (P1)

#### 1309. Role-Based Access Control & Module Visibility (P1, LARGE) — PENDING
- [ ] Migration `051_rbac`: `roles` table (user|admin|accounting|partner|attorney|secretary|paralegal + custom subroles), `role_permissions` (module→action mapping), `user_roles` (many-to-many), tenant-level role definitions
- [ ] Backend: `require_role("invoicing")` / `require_role("admin")` dependency replacing ad-hoc checks; per-role middleware; `routers/admin/roles.py` CRUD for role definitions + permission matrix
- [ ] Admin page: role editor UI — define roles, assign permissions per module (billing, matters, documents, calendar, chat, plugins, admin), assign users to roles
- [ ] Custom subroles: firm can clone a base role and toggle individual permissions → saved as tenant-scoped custom role
- [ ] Module visibility gating: admin can disable unpurchased addon modules per tenant; sidebar/route hide disabled modules; `TenantSettings.modules` JSON list of enabled modules; dev override flag to see all modules regardless
- [ ] Invoicing lock (DONE — hotfix): `generate_invoice`, `update_invoice`, `create_payment`, `export_invoice` gated to `require_admin` in `billing_extended.py`

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

- [ ] **Time tracking advanced:** allow rate override on invoice creation screen for admin
- [ ] **Templates overhaul:** support PDF/DOCX native templates with field mapping (currently text-only)

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
- [ ] **Gap: Trust Accounting has NO frontend** — no pages, no API functions in `api.js`, no routes in `App.jsx`. Backend is headless.
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
