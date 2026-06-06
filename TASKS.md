# TASKS.md

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

Files: `backend/app/routers/platform_llm.py`, `backend/app/models/llm_provider_key.py`, `backend/migrations/versions/045_llm_provider_keys.py`, `frontend/src/pages/PlatformPage.jsx`, `frontend/src/api.js`

### 1203. Operator Console — AI Operations (P0, LARGE) — PENDING
- [ ] Add AI Operations tab with global standard/premium aliases and per-tenant override table
- [ ] Add model/provider disable switch with immediate route validation
- [ ] Add model test action using synthetic prompt and no tenant data
- [ ] Show recent LLM failures by tenant, route, provider, model, status code, and latency
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

---

## Future

- [ ] **Time tracking advanced:** allow rate override on invoice creation screen for admin
- [ ] **Templates overhaul:** support PDF/DOCX native templates with field mapping (currently text-only)

---

## Backlog — Integration Health & Module Restoration

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
