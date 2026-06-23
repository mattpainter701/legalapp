# Changelog

## [Unreleased]

### Fixed
- **Self-inflicted 429s on the admin tab (and empty call feed):** the intake dashboard call feed polled every 15s (240 req/hr) while the per-user hourly cap was only 200, so leaving the dashboard open exhausted the budget mid-hour and 429'd every other request — including `/api/admin/tenant` — until the clock hour rolled over. Raised the per-user cap to 600/hr, exempted the polled `/api/intake/dashboard/recent-callers` read from the per-user counter (nginx still IP-limits it), and slowed the poll to 30s.
- **Intake dashboard mobile + action feedback:** the call-logging/lead result banner is now sticky and auto-scrolls into view, so tapping "Create Lead"/"Log Call" at the bottom of a long mobile page shows the "Lead created…" confirmation instead of appearing to do nothing. Tightened mobile padding/heading sizes and removed the dev-only "MVP Boundary" panel to declutter the screen.
- **Call feed time visibility:** each call-feed row now shows the call time prominently plus a relative "12m ago / 3h ago / 2d ago" recency line (feed remains ordered newest-first), so reception sees when each call came in without clicking into it.
- **Intake history matches show who answered:** call-log results in the History Matches panel now surface `answered_by` (the staff member who answered, from the call's `callee_name`) and the `result` (answered/missed) alongside phone and timestamp — matching what the live call feed already shows — so reception can see who took the call, not just the caller-history name match.
- **Zoom Phone intake call history:** sync now requests inbound Zoom Phone history only, the importer skips non-inbound call-history rows, nested caller/callee payloads are scanned for the actual caller phone number, and the Zoom Phone queue filters out previously imported outbound legs.
- **Zoom Phone token expiry copy:** replaced the confusing one-hour access-token expiry footer with admin-facing copy explaining that Clarity refreshes Zoom access automatically during sync/test and only needs reauthorization if access is revoked, scopes change, or the refresh grant expires unused.
- **Platform AI routing save/reload:** enabled LiteLLM DB-backed model storage in compose (`STORE_MODEL_IN_DB=True`) so operator route changes can hot-reload through `/config/update` instead of returning a 500.
- **Platform AI routing load:** stopped auto-fetching provider model lists for saved routes on page load; the AI Routing page now uses the cached catalog by default and only probes provider `/models` endpoints when an operator explicitly clicks a model refresh button.
- **Chat legal footer:** the "Prepared for ... Attorney review recommended" footer is now conditional for legal analysis/drafting/advice-like responses instead of being required on every chat message.
- **Admin integrations clarity:** moved optional Zoom meeting setup into its own Admin tab so missing Zoom OAuth credentials no longer make regular Microsoft/Google integrations look unhealthy.
- **Zoom Phone OAuth visibility:** Admin → Zoom now always renders the Zoom Phone intake connection card instead of hiding it behind meeting-only Zoom configuration, so admins can see the phone OAuth state and required setup separately.
- **Zoom integration admin polish:** redesigned Admin → Zoom around first-class Phone intake and Meetings cards with customer-facing connect actions, friendly scope checklist labels, and a separated operator setup section for OAuth app credentials and redirect URIs.
- **Zoom operator setup visibility:** hid global Zoom OAuth app credentials and redirect URI readiness from tenant Admin → Zoom and moved redacted Zoom setup readiness into the platform operator console.
- **Admin users active toggle:** restored the friendlier Active switch in the Users table for enabling/disabling user accounts while preserving the OAuth-grantor safety confirmation.
- **Admin licensing and add-on controls:** license toggles now allow unlicensing integration grantors without failing the request, premium AI access is managed separately per licensed user, and add-on purchase/trial/disable actions refresh the current module list and show confirmation feedback.
- **Intake dashboard search coverage:** history search now finds log-only callers, split first/last names, partial name fragments, and partial phone digits so receptionist-only calls such as "Jan Patterson" surface before being promoted to leads.
- **Partner-to-attorney intake workflow:** intake follow-up tasks now let a partner qualify a caller, assign the qualified intake to an attorney, complete the partner follow-up, carry receptionist plus partner notes into the attorney’s urgent intake task, and let the attorney open a linked matter in `waiting_fee_agreement` status from that task.
- **Intake dashboard call logging feedback:** prevented successful call logging from immediately triggering an empty dashboard search, which caused a misleading `422` and made the first call log appear to do nothing. Assigned intake leads now also create/update an urgent partner follow-up task and send the standard task assignment alert.
- **Task assignment alerts:** expanded task-assignment notifications into ticket-style alerts with assignee, creator, created/alert time, due time, customer/matter context, source, and reason/description fields.
- **Intake dashboard recent callers:** added a preloaded recent-caller panel with 10/20/50 limits so reception can quickly pick up repeat callbacks without searching from scratch.
- **Intake dashboard callback details:** recent callers now expand into call details including logged-by user, routed partner, lead status, follow-up task status, due/completed time, reason, and notes. History-search `401` failures now show a session-expired message instead of a generic search error.
- **Intake dashboard auto-assignment preflight:** create-lead flow now checks rotation availability before calling `assign-next`, disables auto-assignment when no practice/general rule exists, and keeps successful call logging from being marked failed by a non-critical search refresh.
- **Teams matter linking:** Teams admin now loads canonical matters from `/api/matters`, supports creating a standard Teams channel for the selected matter/team, and requests `Channel.Create` on Teams reconnect for channel creation.
- **Trust account matter picker:** new trust account creation now displays canonical matter names from `/api/matters` instead of falling back to matter UUIDs.
- **Subscription billing placement:** moved Clarity/Stripe subscription billing into the Admin portal as a Subscription tab, removed it from the workspace accounting sidebar, and redirects legacy `/billing` visits to `/admin?tab=billing` for admins.
- **Chat/assistant not falling back to general reasoning or indicating confidence tags (v2):** Second pass on `SYSTEM_PROMPT_TEMPLATE` in `app/services/llm.py`. Added negative examples (WRONG vs RIGHT), explicit "do NOT explain your reasoning process" rule, "greet in 1-2 words then answer" simplification, and a direct "if user types 2+2, reply 4" non-legal-query example. The free-tier models were reading the old prompt as rules to explain rather than follow.
- **Chat latency — parallel pre-work + faster failover:** Parallelized five independent async operations (matter context, attachment context, memory context, LLM route, RAG cache check) with `asyncio.gather` in both `/messages` and `/messages/stream` endpoints — saves ~150-300ms per request. Reduced LiteLLM `request_timeout` 60→25s, `num_retries` 1→0, `cooldown_time` 30→15s, and added per-model `timeout` values (15s free, 20-30s paid) for faster failover to fallback models.

### Added
- **Call Inbox dashboard redesign:** reworked the intake dashboard into a two-pane "Call Inbox" — a left-hand unified call feed that auto-refreshes every 15s (visibility-aware polling) and a right-hand work panel (caller facts → auto-searched history → pre-filled capture/route form). New calls (manual or webhook-imported) surface within ~15s with an in-page toast + WebAudio chime; mute toggle persisted per tenant. The `recent-callers` feed now exposes `source`, `answered_by`, `result`, `duration_seconds`, and recording/transcript URLs, accepts `limit=5`, and batches its enrichment queries (was N+1 per row). Source-agnostic framing: integration controls (Sync, source filter) appear only when the tenant has a connected call source, so a manual-only tenant sees a clean inbox. New `frontend/src/hooks/useCallFeedPolling.js`, `useCallAlerts.js`, and `components/intake/` (CallFeed, CallFeedItem, CallFacts, NewCallToasts, RecordsTabs).
- **Zoom Phone post-call webhooks:** added tenant-specific Zoom Phone webhook URLs with Zoom CRC/signature validation, encrypted tenant webhook secret-token storage, and post-call `phone.callee_call_history_completed` / `phone.caller_call_history_completed` ingestion. Completed inbound webhook records now fetch Zoom call-history detail before idempotent `CommunicationLog` upsert; manual Sync Zoom remains the backfill path. Migration `067` adds webhook secret storage to tenant-owned Zoom apps.
- **Tenant-owned Zoom Phone OAuth apps:** tenant admins can now save encrypted Zoom OAuth client credentials from a firm-owned Zoom app, use that app for the Phone authorization callback, and refresh Zoom Phone tokens without global Clarity Zoom OAuth credentials. The Admin -> Zoom Phone card shows the callback URL, required Phone scopes, masked saved client ID, save/clear actions, and keeps platform/global credentials as fallback only.
- **Zoom Phone admin OAuth grant:** added a customer-facing Admin → Zoom flow for Zoom Phone intake. Tenant admins can connect Zoom Phone through Zoom OAuth, storing encrypted `zoom_phone` tenant credentials separately from the existing Zoom meetings integration. The Zoom tab now shows Zoom Phone status, missing scopes, disconnect, and a connection test that probes Call History without importing. The call-history importer now prefers the tenant OAuth token and keeps the S2S/env path only as an operator fallback. Expected callback: `/api/integrations/zoom-phone/callback`.
- **Zoom Phone intake call history:** added a Zoom Phone Server-to-Server call-history importer that stores each call idempotently as a `CommunicationLog` (`zoom_phone:call:{id}`), preserving caller ID, normalized phone, direction/result, duration, recording/transcript links, and summary/transcript details. The intake dashboard now has a Zoom Phone Calls queue with admin sync, transcript/recording links, and click-to-prefill into the existing lead/task capture flow; saving from a Zoom row updates the imported call record instead of creating an unrelated duplicate. New `ZOOM_PHONE_*` env template keys document the account-credentials setup.
- **Zoom Phone admin OAuth backlog:** corrected the near-term follow-up to use a customer admin OAuth grant in the Clarity portal, matching Microsoft/Google integrations, with encrypted per-tenant `zoom_phone` tokens, connection testing, setup status, and S2S/env kept only as a temporary operator fallback.
- **Zoom Marketplace P2 backlog:** documented the deferred Zoom-side app plan, including the required heavy research pass for Marketplace app types, Zoom Phone APIs/webhooks/scopes, review/security requirements, tenant install flow, and whether an in-Zoom receptionist surface is worth building.
- **Platform free legal model eligibility:** model catalog rows now include legal eligibility, tier, badges, latency eligibility, and exclusion reasons; the AI Routing UI defaults to Recommended models and adds Free Legal, All Free, and Excluded tabs.
- **Matter-linked chat workflow:** general chats can now be linked, changed, or unlinked from matters in Chat, and Matter Detail opens matter-scoped conversations through `/chat?conv=...`.
- **Accountant finance role and restricted licensing modes:** added an `accountant` role for billing/licensing/subscription/reporting access, premium-AI assignment on users, backend enforcement for unlicensed users, and intake-only module resolution so call-intake tenants land on the intake dashboard plus add-on modules only.
- **General intake task routing:** receptionist call capture now supports partner rotation by default, no-task logging, or a general staff task assigned to any active tenant user with preset/custom task labels; recent callers and CSV exports show these staff task assignments.
- **Standalone caller-intake packaging:** intake-only tenants can use the dashboard as a self-contained licensed product, and the intake dashboard now exports tenant-scoped call records to CSV with optional date range filters for finance/Tabs3 partner-association reconciliation without promoting every caller into CRM/matters.
- **Sellable plan/tier framework (Call Intake solo):** new plan registry (`app/services/plans.py`) drives module visibility from a named plan, replacing the hardcoded intake-only branch and laying groundwork for additional public tiers. Tenants can be provisioned intake-only two ways — an operator plan selector on the platform tenant editor (`PUT /api/platform/tenants/{id}` `plan`, `GET /api/platform/plans`) and a public self-serve signup (`POST /api/auth/signup/plan`) that creates an intake-only tenant + admin user on a 14-day trial. Plans expose an `upsell_target` for in-product upgrade prompts.
- **Fail-closed API module enforcement:** new `ModuleGuardMiddleware` rejects API calls to modules outside a tenant's plan (keyed off a signed `plan` JWT claim), so an intake-only tenant is walled at the API, not just the UI. Tokens without the claim default to full platform (backward compatible).
- **Partner assignment log + export:** new append-only `partner_assignment_log` table (migration `064`, RLS-scoped) records every partner/staff assignment (rotation, prior-attorney, specific-staff) with name snapshots. Exposed via `GET /api/intake/dashboard/partner-log` and `/partner-log/export` (CSV), plus a Partner Log panel on the intake dashboard.
- **In-product upsell:** intake-only tenants see locked nav teasers for other modules that open an "Upgrade to the full platform" modal; requests are captured to `plan_upgrade_requests` (migration `065`) via `POST /api/plan/upgrade-request` for sales follow-up.
- **Clause-level legal chunking:** New `app/utils/legal_chunker.py` replaces fixed 500-token chunking with structure-aware splitting that respects legal document anatomy (sections, articles, numbered clauses). Each chunk carries `section_path` (e.g. "Article I > Section 1.01 > (a)") and `clause_type` (definition/obligation/remedy/governing_law/recital/general) metadata for clause-type-aware retrieval. Migration `060_chunk_metadata_fts` adds the columns + a GIN-indexed `tsvector` column for PostgreSQL full-text search.
- **Hybrid retrieval (dense + FTS + RRF fusion):** `app/services/rag.py` now runs pgvector cosine similarity and PostgreSQL FTS in parallel, fusing results via Reciprocal Rank Fusion (0.6 dense / 0.4 FTS weight). FTS matches on exact identifiers (section numbers, defined terms, dates) that dense embeddings miss. Context headers now include `section_path`, `clause_type`, and keyword-match indicators.
- **Complexity-based LLM routing:** `classify_query_complexity()` in `app/services/llm_routing.py` pattern-matches user queries as simple (definitions, math, small talk) or complex (drafting, analysis, multi-hop). `_auto_tier()` in `chat.py` auto-upgrades complex queries to premium and auto-downgrades simple queries to standard — saves cost on lookups, improves quality on hard questions.
- **Free model speed vetting + auto-cooldown:** `record_model_latency()` tracks per-model time-to-first-token in a ring buffer. Models exceeding 15s latency or with >50% slow samples enter a 5-minute cooldown. Wired into `llm.py` `complete()` and `stream_complete()` for both success and error paths.

### Changed
- **Directory user sync licensing default:** new active Microsoft 365/Google directory users are now imported as standard licensed users by default, while existing users keep their current license flag so admins can exclude service accounts manually.
- **LiteLLM gateway timeout tuning:** `request_timeout` 60→25s, `num_retries` 1→0, `cooldown_time` 30→15s, `allowed_fails` 2→1, per-model `timeout` values (15s free, 20-30s paid). Slow free models fail over faster instead of holding connections.
- **Chat endpoint pre-work parallelized:** Both `/messages` and `/messages/stream` now run matter context, attachment context, memory context, LLM route resolution, and RAG cache check via `asyncio.gather` instead of sequentially. Pooled IOLTA bank accounts with three-way reconciliation and saved snapshots.
  - Migration `054_trust_ledger`: `trust_bank_accounts` (pooled, RLS), `trust_accounts.bank_account_id` FK, `trust_reconciliations` (persisted snapshots, RLS).
  - `routers/trust_accounting.py`: pooled bank-account CRUD (`/api/trust/bank-accounts`), pooled three-way reconcile (`bank == book == Σ client ledgers`) that persists a snapshot, reconciliation history, and per-client ledger statement (`/accounts/{id}/statement`) with CSV export. The existing per-account reconcile now also persists a snapshot. Trust models registered in `models/__init__.py`.
  - Fixed a pre-existing latent serialization bug (UUID→str) in trust response schemas that would have 500'd the 1314 create/transaction flows in production; added a shared coercion mixin.
  - **Firm branding + branded PDF statements:** migration `055_firm_branding` adds branding columns to `tenant_settings`; `GET/PUT /api/firm/branding` (`routers/firm.py`, PUT admin-only, firm name/address fall back to the tenant record); `services/trust_statement_pdf.py` renders a firm-branded ledger statement (logo embedded best-effort, skipped on fetch failure); `?format=pdf` on the statement endpoint returns it. Verified by `tests/test_firm_branding.py` + the full trust suite (16/16 passing on deploy).
  - **Branding UI:** `FirmBrandingPanel` in the Admin Settings tab to define all branding fields, and a "Download PDF" button on the trust account ledger view; `api.js` gains `getFirmBranding`/`updateFirmBranding`/`downloadTrustStatementPdf`.
- **Task 1314 — Trust Accounting Frontend:** Full UI for the existing trust accounting backend (9 endpoints in `trust_accounting.py`).
  - `frontend/src/api.js`: 9 wrapper functions (`createTrustAccount`, `listTrustAccounts`, `getTrustAccount`, `updateTrustAccount`, `closeTrustAccount`, `createTrustTransaction`, `listTrustTransactions`, `reconcileTrustAccount`, `getTrustReconciliation`).
  - `TrustAccountingPage` (`/trust`): portfolio view with total-balance summary, active/all filter, accounts table, and a "New Trust Account" modal with matter selector.
  - `TrustAccountDetail` (`/trust/:id`): balance ledger header (current/minimum balance, auto-replenish), transaction history table with deposits/disbursements/net summary, "Post Transaction" modal, inline edit form, and close-account action.
  - `TrustAccountReconcile`: reconciliation tab — bank balance / outstanding deposits & disbursements form, three-way reconciliation result with reconciled vs. out-of-balance banner, and last-reconciliation display on load.
  - Sidebar nav entry ("Trust Accounting", Landmark icon) and routes added to `App.jsx`.
  - `MatterDetailPage`: new "Trust Balance" card next to the Budget card, summing balances across the matter's trust accounts with a quick-link to the detail/reconciliation view (shows "No trust account" when none exist).
- **Task 1308b — Accounting Reports (Phase 1):** Three core billing reports with CSV export.
  - Backend (`routers/reports.py`): tenant-scoped `GET /api/reports/billing/realization` (per-matter billable hours/amount vs collected, realization %), `/billing/wip` (uninvoiced billable time + value), and `/billing/aging` (outstanding invoice balances bucketed 0–30 / 31–60 / 61–90 / 90+ days overdue). Each endpoint supports `?format=csv` for a downloadable CSV.
  - Frontend (`ReportsPage`): tab bar (Overview / Realization / WIP / A/R Aging) with sortable tables and per-report Download CSV buttons; new `api.js` report fetchers + CSV blob helpers.

### Changed
- **Task 1305 — Court-Rules Deadline Engine: dropped.** LawToolBox commercial-API path abandoned (no customer-demand pull); research artifacts retained under `docs/research/1305-*.md`. Revisit only on explicit litigation-firm demand.

### Fixed
- **Outlook mail sync used an invalid Graph path:** `cloud_sync.sync_outlook_mail` requested `/users/me/messages`, which Microsoft Graph rejects with `400 TargetIdShouldNotBeMeOrWhitespace` (literal `me` is not a valid `/users/{id}` segment) — so every Outlook mail sync failed. Now uses the `/me/messages` delegated shortcut, matching the working OneDrive call in the same module.
- **Google Directory sync surfaced a cryptic 400:** a personal (non-Workspace) Google account returns `400 Invalid Input` for the `my_customer` directory. `user_sync` now translates this into an actionable message (connect a Workspace admin account, or disable directory sync) instead of a raw status dump, mirroring the existing 403 handling.
- **Cloud workspace integrations:** Matter documents now expose live OneDrive/Google Drive links for cloud-backed files, matter cloud folders can be force-provisioned and synced from the matter Documents tab, cloud file lists are scoped to the provisioned matter folder, cloud content fetches preserve the requesting user's token context, email inbox scans dedupe provider messages, disabled/suspended directory users are skipped, and Google Directory sync no longer sends the invalid `isSuspended=false` query.

## [0.14.0] — 2026-06-06

### Sprint 12 — LiteLLM Gateway & AI Operations Control Plane

### Added
- **Task 1206 — Provider Route Builder:** Full UI-driven AI routing console in the Platform admin. Operators can now manage provider API keys, fetch live model lists, and configure standard/premium routes with fallback chains — all without touching config files.
  - `llm_provider_keys` table (migration 045): Fernet-encrypted key vault with provider association and masked key hints
  - `GET/POST/DELETE /api/platform/llm/provider-keys`: key vault CRUD
  - `POST /api/platform/llm/provider-keys/sync-env`: imports `DEEPSEEK_API_KEY` and `OPENROUTER_API_KEY` from environment into the vault
  - `POST /api/platform/llm/provider-keys/{id}/fetch-models`: proxies provider `/models` endpoint via stored key
  - `GET/PUT /api/platform/llm/routes`: reads/writes route config and hot-reloads LiteLLM via `POST /config/update`
  - `POST /api/platform/llm/routes/test`: validates a route with a synthetic prompt, returns latency + first response tokens
  - Provider presets: OpenCode Zen, OpenCode Go, OpenRouter, DeepSeek, Anthropic
  - **AI Routing tab** in PlatformPage: KeyVaultPanel (key list, add form, sync-env button) + RouteCard (provider/key/model selection, fallback chain builder, route test)
  - **Live Model Catalog v2:** Derived capability tags (vision, tool_use, reasoning, research, rag, legal, large_context, structured_output) from provider model metadata; legal-specific heuristics flag models mentioning law/litigation/contract/compliance in descriptions; capability filter pills, colored badges, pricing/modality display per row; compact ApplyRouteDropdown replaces six inline routing buttons; show-all toggle removes 60-model cap

### Fixed
- **OAuth/integration stability:** Normalized Google userinfo scope aliases in integration health checks, made admin/user integration OAuth redirects use the configured API base URL, moved post-connect directory sync to a background task, restored missing manifest icons, and improved Google Directory 403 messaging.
- **Calendar/task sync:** Task calendar pushes now prefer the assigned/creator user's connected Google/Microsoft calendar token, remove old events on reassignment, and keep completed/uncompleted task changes reflected in external calendars.
- **Backend bug sweep:** Fixed missing billing admin import, plugin router logger crash, new-matter cloud provisioning imports, and made the backend test database URL configurable with `TEST_DATABASE_URL`.
- **Matter/admin/SMB stability sweep:** Fixed tenant settings UUID response
  validation, hardened matter list/detail serialization for legacy null fields,
  prevented cloud-folder share failures from aborting matter workflows, corrected
  doubled SMB API paths, normalized SMB admin response handling, and restored
  LiteLLM container healthchecks by including `curl`.
- **Matter create fallback:** Blank matter types now persist as `general` across
  matter create paths, with a database default to prevent `matter_type` NOT NULL
  crashes when the UI leaves the optional field empty.
- **Cloud folder provisioning repair:** Cloud integration retry now merges root
  and matter folder records across providers, backfills missing provider folders
  for existing matters, saves plugin-created matter folder IDs, avoids blocking
  matter creation when provider folder setup fails, and fixes cloud metadata
  sync upserts missing `tenant_id`.
- **Chat-system follow-up:** Cached resolved LLM routes with invalidation on
  tenant/platform LLM settings writes, fixed the missing `asyncio` import for
  parallel RAG, removed stray blank context separators, centralized nginx API
  streaming proxy directives, and added no-context regression coverage.
- **Task 1206 follow-up — AI Routing Console hardening:** Route saves now validate
  provider/key pairings, prune blank fallback rows, return 400s for malformed key
  IDs, register LiteLLM fallback mappings alongside model aliases, and handle
  LiteLLM-native Anthropic model prefixes/testing correctly. The Platform AI
  Routing tab now shows alias readiness, primary/fallback ordering, model-fetch
  state, validation feedback, and safer key deletion behavior.
- `admin.py`: add `from_attributes=True` to `TenantSettingsResponse.model_validate()` calls
- `platform.py`: guard `PLATFORM_SECRET_KEY` length < 32, fix `pg_total_relation_size(relid)` column reference
- `.env.hypervisor`: clear leftover placeholder instruction from `PLATFORM_SECRET_KEY`
- `.env.prod.example`: add `openssl rand -hex 32` generation comment for `PLATFORM_SECRET_KEY`

## [0.13.9] — 2026-06-06

### Fixed
- **BK13:** Chat refused to answer general legal questions without context — system prompt lacked explicit instruction to answer from general knowledge when FIRM CONTEXT is empty. Added rule: answer directly, tag all claims [model knowledge], never gate on context availability.
- **BK14:** Premium model 404 — all three route legs broken: (1) primary `openai/deepseek-chat` at `opencode.ai/go/v1` returns HTML 404 (wrong path); correct endpoint is `opencode.ai/zen/go/v1` with model `deepseek-v4-pro`. (2) standard `opencode.ai/go/v1` similarly broken; fixed to `opencode.ai/zen/v1` with `deepseek-v4-flash-free`. (3) `llama-4-maverick:free` removed from OpenRouter; replaced with `gemma-4-31b-it:free` (confirmed working) as premium OpenRouter fallback.

## [0.13.8] — 2026-06-06

### Fixed
- **BK10:** LiteLLM 401 on chat — `LITELLM_API_KEY` was missing from `.env.hypervisor` template. LiteLLM container defaulted to master key `sk-local-litellm` (from docker-compose default) while backend sent `"not-needed"` as auth. Fixed: added full LiteLLM section to `.env.hypervisor`; changed fallback in `LLMService` and `EmbeddingService` from `"not-needed"` to `"sk-local-litellm"` to match docker-compose default.
- **BK11:** OAuth 429 on back-to-back SSO logins — nginx `auth` zone (10r/m, burst=5) was applied to all `/api/auth/` paths. A complete OAuth flow uses 3+ requests, so 2 logins = 6 requests → burst exhausted. Fixed: added dedicated `oauth` zone (30r/m, burst=15) applied to `/api/auth/(google|microsoft)/` paths before the catch-all `auth` block.
- **BK12:** LiteLLM slow/failed responses — three root causes: (1) `clarity-standard` primary route pointed to `zen.opencode.ai` which is unreachable; switched to `DEEPSEEK_BASE_URL` (working OpenCode endpoint). (2) `deepseek/deepseek-r1:free` removed from OpenRouter → 404 on fallback; replaced with `qwen/qwen3-235b-a22b:free` and `meta-llama/llama-4-maverick:free`. (3) `clarity-embeddings` model registered against `OPENAI_API_KEY` which is unset → LiteLLM rejected model → 400 on every embed call; removed `clarity-embeddings` from config. `EmbeddingService` now only routes through LiteLLM when `LITELLM_EMBEDDING_MODEL` is explicitly set; otherwise falls back to direct provider (or disables embeddings gracefully). Reduced `request_timeout` 120→60s, `num_retries` 2→1, `cooldown_time` 60→30s.

## [0.13.7] — 2026-06-05

### Fixed
- **BK06:** TimeEntryResponse UUID validation crash (`billing_extended.py`) — `model_validate()` on ORM objects without `from_attributes=True` caused 500 on UUID→str coercion. Fixed all 10 calls (TimeEntry, Expense, InvoiceLineItem, Payment).
- **BK07:** Time Tracking now auto-selects matter from context — MatterDetailPage passes `?matter_id=` query param to TimeTrackingPage.
- **BK08:** Time Tracking matters list now loads independently and sorts by `updated_at` desc (recent activity).
- **BK09:** Hypervisor chat broken — `docker-compose.hypervisor.yml` was missing `litellm` + `litellm-postgres` services. Added both services, healthcheck dependency, and volume.
- **BK01:** Google Workspace scope audit mismatch (`drive.readonly` → `drive` in `admin.py:1141`). Added error logging to `refresh_google_token()`.
- **BK03:** Microsoft 365 scope audit mismatch (`Files.Read.All` → `Files.ReadWrite.All` in `admin.py:1130`).

### Audited (Non-Code)
- **BK04:** Mediation module audit complete — 97% production-ready. Backend has 24 firm + 12 portal endpoints (all real code). Frontend has 4 pages built. Gaps: missing alembic migration for 7 mediation tables, no sidebar nav link, ProposalStatusUpdate schema unused, no portal document delete.
- **BK05:** Trust & Estates module audit complete. Estate backend + frontend fully built. Trust Accounting backend fully built (9 endpoints, 2 models, migration 017) but has **zero frontend** — no pages, no API functions, no routes.

## [0.13.6] — 2026-06-05

### Fixed
- **Calendar page:** Single "Sync Calendar" button auto-detects which provider (Microsoft/Google) the user has configured, instead of showing both buttons unconditionally.
- **Estate creation:** Map human-readable estate types (Probate, Trust Administration, etc.) to snake_case backend values, fixing 422 validation errors on estate creation.
- **Time tracking:** Hide `hourly_rate` field from non-admin users in time entry form. Time entries now auto-use the user's `default_billing_rate` set by admin.
- **Admin users tab:** New inline-editable "Rate" column for setting each user's `default_billing_rate`.
- **Reports schema:** `budget_currency` made Optional with "USD" default to prevent potential schema validation 500s.

### Added
- `GET /api/auth/calendar-providers` endpoint — returns which calendar providers the current user has configured tokens for.
- `default_billing_rate` field added to `UserPatchRequest` so admin can set rates via `PATCH /admin/users/{user_id}`.

### Changed
- `TimeEntryCreate.hourly_rate` field is now Optional, defaults to user's `default_billing_rate`.
- Time entry update endpoint recalculates amount correctly using Decimal precision.

## [0.13.5] — 2026-06-05

### AppShell Layout — Restore Consistent UI Across All Pages

### Added
- **`AppShell.jsx`:** Shared layout component wrapping all authenticated pages with sidebar (always visible on desktop, hamburger overlay on mobile) + top header bar with prominent Admin button (Shield icon) for admin users.
- **`AppShellContext`:** React context for shared conversations/documents state across sidebar and ChatPage.
- **AdminPage collapsible tabs:** Toggle button to collapse/expand the admin tab bar; dropdown picker when collapsed for mobile-friendly tab switching.

### Changed
- **`App.jsx`:** Created `ShellRoute` wrapper that composes `ProtectedRoute` + `AppShell` for all 25+ authenticated routes. ChatPage, MatterPortfolio, Admin, Calendar, Communications, TimeTracking, Invoices, Reports, Templates, Billing, Contacts, Tasks, Intake, Plugins, Plugin, Profile, RenewalTracker, EstatePortfolio, EstateDetail, MediationPortfolio, MediationDetail, MCP, OnboardingWizard — all now share the AppShell layout.
- **`Sidebar.jsx`:** Logout icon changed from Settings (gear) to sign-out arrow for clarity. Already had mobile overlay support from task 1110.
- **`ChatHeader.jsx`:** Removed admin button from "More" dropdown (moved to AppShell header).
- **`ChatPage.jsx`:** Refactored to use shared `AppShellContext` for conversations/documents; sidebar rendering removed (handled by AppShell).
- **Multiple pages:** Removed redundant `min-h-screen bg-brand-bg` outer wrappers from MatterPortfolioPage, BillingPage, IntakePage, ReportsPage, ContactsPage, AdminPage.

### Fixed
- Regression where sidebar was only visible in ChatPage — now present across all authenticated pages.
- Admin button was hidden in ChatHeader dropdown menu — now prominently in AppShell top-right for all pages.
- AdminPage now has collapsible tab navigation for better mobile UX.

## [0.13.4] — 2026-06-05

### Cloud Drive Integration Fix — Google Drive + OneDrive Folder Creation

### Fixed
- **`integrations.py`:** Google admin OAuth scope changed from `drive.readonly` → `drive` — all write operations (folder creation, sharing) were returning HTTP 403 silently.
- **`integrations.py`:** Microsoft admin OAuth scope changed from `Files.Read.All` → `Files.ReadWrite.All` — OneDrive folder creation and sharing require write permissions.
- **`integrations.py`:** Added `_ensure_cloud_root()` call after admin re-auth so re-authorizing automatically backfills `claritylegal-records` root folder for tenants that completed onboarding with broken scopes.

### Added
- **`integrations.py`:** `POST /api/integrations/cloud-init/retry` endpoint — admin-only, re-creates the `claritylegal-records` root folder and backfills all matters with `cloud_folder = null`, returning `{root, matters_initialized, matters_failed}`.
- **`cloud_init.py`:** `initialize_matter_folders()` now stores `url` for OneDrive (via `_get_onedrive_web_url`) and Google Drive (direct `drive.google.com/drive/folders/{id}` URL) so matter detail pages can link directly to folders.
- **`api.js`:** Added `retryCloudInit()` call for the new retry endpoint.
- **`IntegrationsPanel.jsx`:** "Retry cloud setup" button in overall status row — triggers backfill and shows count of matters initialized. Updated scope labels to reflect write scopes.
- **`MatterDetailPage.jsx`:** Cloud Storage row in Case Details card — shows "OneDrive" and/or "Google Drive" pill buttons linking to the matter's cloud folder when `matter.cloud_folder` is populated.

---

## [0.13.3] — 2026-06-05

### Task 1111 — Operator Console: Error Diagnostics & API Traffic Logs

### Fixed
- **`PlatformPage.jsx`:** Fixed `LIMIT is not defined` ReferenceError by capturing `limit` from API response and replacing all hardcoded `LIMIT` variable references.
- **`PlatformPage.jsx`:** Masked user emails in tenant detail view — now shows `full_name` (or "User XXXX…") and user ID prefix instead of exposing email addresses.

### Added
- **Platform error log endpoints** in `platform.py`: `GET /api/platform/logs` (cross-tenant errors, paginated, filterable by tenant/severity/type/days/unresolved), `GET /api/platform/logs/summary` (by_severity, by_type, by_tenant top 20, daily trend), `GET /api/platform/logs/tenant/{id}`, `GET /api/platform/logs/tenant/{id}/summary`. All endpoints anonymize user_id.
- **`ApiAccessLog` model** (`api_access_log.py`) + migration 038: metadata-only request logging (tenant_id, endpoint, method, status_code, latency_ms, ip_address, user_agent_short).
- **`ApiAccessLogMiddleware`** (`middleware/access_log.py`): Logs every request after TenantMiddleware resolves tenant_id. Skips /health, /docs, /api/platform, /static.
- **Platform access log endpoints**: `GET /api/platform/access-logs` (paginated, filterable by tenant/endpoint/status/hours), `GET /api/platform/access-logs/summary` (total_requests, by_status, avg_latency, by_endpoint top 20, by_tenant top 20).
- **Operator Console Logs tab**: 3 sub-tabs — System Errors (summary cards + filterable/paginated table), Tenant Logs (per-tenant drill-down with selector), API Traffic (access log with summary statistics). Added FileText/Globe/AlertTriangle icons.

### Changed
- **`models/__init__.py`:** Registered `ApiAccessLog` model.
- **`main.py`:** Registered `ApiAccessLogMiddleware` in middleware stack (after TenantMiddleware, before RateLimitMiddleware).
- **`frontend/src/api.js`:** Added 6 platform log/access-log API functions.

---

## [0.13.2] — 2026-06-05

### Task 1109 — Calendar Sync Multi-User Fix

### Fixed
- **`token_vault.py`:** `get_fresh_user_token()` now logs a warning (user_id, provider, reason) on every silent `None` return instead of failing invisibly.
- **`calendar_sync.py`:** Replaced bare `RuntimeError` with `ValueError` carrying a user-readable message ("No Microsoft calendar token. Please reconnect your calendar in Settings.") for both missing-token and HTTP-failure cases.
- **`email_agent.py`:** Sync endpoint catches `ValueError` from calendar service and returns `HTTP 401` with the readable detail instead of crashing as a 500.
- **`CalendarPage.jsx` + `api.js`:** Added "Sync to Calendar" button with spinner; success/error banner displayed after each attempt so users know exactly what failed.

---

### Task 1110 — Mobile Responsive UI Overhaul

### Added
- **Sidebar mobile overlay:** Hamburger button in `ChatHeader` (hidden on md+) opens sidebar as a slide-in overlay with backdrop on mobile. Sidebar uses `position: fixed md:relative` so it doesn't push content on desktop. State managed via `sidebarOpen` in `ChatPage`.
- **iOS safe-area bottom padding:** `ChatInput` uses `env(safe-area-inset-bottom)` so the input bar clears the home indicator on iPhone.

### Changed
- **`Sidebar.jsx`:** Accepts `isOpen`/`onClose` props. All nav clicks close the sidebar on mobile. Desktop layout unchanged (always visible, in-flow).
- **`ChatHeader.jsx`:** Hamburger button (md:hidden), model selector hidden on mobile (sm:hidden), public case law toggle hidden on small screens (md:hidden), gap reduced on small viewports.
- **`ChatInput.jsx`:** Horizontal padding responsive `px-4 md:px-8`.
- **`MatterDetailPage.jsx`:** Topbar and content padding responsive. Tab bar `overflow-x-auto` with `flex-shrink-0` on each tab. Edit form grids `grid-cols-1 sm:grid-cols-2`. Billing stats `grid-cols-1 sm:grid-cols-3`. Team add form `flex-col sm:flex-row`.
- **`AdminPage.jsx`:** Topbar `px-4 md:px-8`, content `px-4 md:px-8 py-8 md:py-12`, tab nav `overflow-x-auto` with `whitespace-nowrap` and smaller gap on mobile.
- **`MatterPortfolioPage.jsx`:** Topbar and content padding responsive `px-4 md:px-8`.
- **`index.css`:** Sidebar slide transition classes made unconditional (not wrapped in media query) so Tailwind `md:translate-x-0` override works correctly.

---

## [0.13.1] — 2026-06-04

### Sprint 10 Post-Review Bug Fixes

### Fixed
- **Agent sync broken (CRITICAL):** `share_id` sent in JSON body, but router expects query param — sync operations silently failed. Moved `share_id` to `params` in agent's `api_client.py`.
- **`_scan_share` wrong type annotation + duplicate args (CRITICAL):** Removed unused `scanner: SaaSClient` param, fixed call sites to pass correct args.
- **Content fetch tasks never succeed (CRITICAL):** `task_worker.py` called `read_content(session=None)` without SMB session. Moved `register_session` call to always execute before reading.
- **`tomli_w` inline import in `config.py` (CRITICAL):** Moved to top-level import with `ImportError` fallback; `save_config` falls back to JSON if TOML unavailable.
- **Pairing code registration — no tenant isolation:** `register_agent` query selects by pairing code across all tenants. Added optional `tenant_id` filter param.
- **Share CRUD — no tenant RLS validation:** `update_share`, `delete_share`, `list_shares` queried without tenant filter. Added `tenant_id` conditions to all DML.
- **Content fetch task — no tenant ownership check:** `get_content_status` endpoint polled any `task_id` without tenant validation. Added file ownership check.
- **Frontend field name mismatches:** `SmbAdminPage` used `agent.name`/`agent.version` (should be `agent_name`/`agent_version`) and `MatterSmbSharesTab` used non-existent `s.name`/`s.share_name`/`s.server_host` (should be `display_name`/`share_path`).
- **Sync file count cap race condition:** Added `db.flush()` before count query to see pending inserts.

### Changed
- **SmbShare model:** Added `ForeignKey("tenants.id", ondelete="CASCADE")` on `tenant_id` and `Index("ix_smb_shares_tenant_id", "tenant_id")`.
- **SMB auth rate limiting:** Added Redis-based rate limiter (30 req/60s) on `X-Agent-API-Key` endpoint.
- **RAG integration:** `rag.py` now uses `SmbService` directly instead of duplicating through `smb_search.py` module.
- **Content fetch polling:** Added `poll_content_result()` with exponential backoff (1s → 8s) to `SmbService`, replacing fixed 2s polling in `smb_search.py`.

### Added
- **Per-share file extension filtering:** `SmbScanner.scan_share()` accepts `file_extensions` parameter, propagated from share config through `_scan_share()`.
- **`build_smb_context()`** on `SmbService` (static method) — consolidates context formatting from `smb_search.py`.
- **JSON config fallback** in agent `config.py` — `load()` supports both TOML and JSON formats.

### Design — Legal MCP Database & CourtListener Ingest Pipeline

- **Architecture Design Doc**: `docs/legal_rag.md` — full schema, ingest pipeline, embedding migration, MCP tools, metering, deployment architecture. Implementation tabled; only a minimal 2-tool MCP REST endpoint exists in `backend/app/routers/mcp.py`.

---

## [0.12.0] — 2026-06-04

### Sprint 10 — SMB File Share Relay Agent

### Added
- **SMB Agent Models**: `SmbAgent`, `SmbShare`, `SmbFileIndex`, `SmbAccessLog`, `MatterSmbShare` SQLAlchemy models with pgvector-style tsvector/GIN full-text search, RLS policies, and migration 036
- `smb_folders` JSONB column on `matters` table (parallel to `cloud_folder`)
- **Migration 036**: Five new tables (`smb_agents`, `smb_shares`, `smb_file_index`, `smb_access_log`, `matter_smb_shares`) with RLS, GIN index on search_vector, tsvector auto-update trigger, and `smb_folders` column on matters
- **SMB API Router** (`/api/v1/smb`): 19 endpoints — agent registration, pairing, heartbeat, file sync, content fetch task queue, user search, admin stats, matter binding
- **SMB Auth Middleware**: API key authentication for agent endpoints (SHA-256 hashed keys, separate from JWT)
- **SmbService**: Pairing code generation, agent registration, heartbeat, file sync (upsert with ON CONFLICT), content fetch task dispatch via Redis, full-text search, share CRUD, matter binding CRUD
- **SmbSearchService**: tsvector full-text search with `plainto_tsquery`, matter-scoped search via `matter_smb_shares` join, content fetch orchestration, `build_smb_context()` for LLM context injection
- **RetrievalPlanner**: Added `smb_enabled` parameter to planner, `smb` source in PROVIDER_SOURCES, updated prompt to include on-prem file share as search source
- **RAG Integration**: `hybrid_rag_query()` now checks for active SMB agents and runs tsvector search alongside pgvector and cloud search, merges results into unified context
- **Admin Endpoints**: `GET /admin/smb/status` (agent/share/file counts, last activity) and `GET /admin/smb/activity` (access log)
- **Config**: `SMB_ENABLED`, `SMB_PAIRING_CODE_TTL_MIN`, `SMB_MAX_FILE_INDEX_PER_SHARE`, `SMB_SNIPPET_MAX_CHARS`, `SMB_TASK_POLL_INTERVAL`, `SMB_CONTENT_FETCH_TIMEOUT`
- **Relay Agent Package** (`agent/clarity_agent/`): pip-installable agent with SMB scanner (3-tier change detection), file reader (PDF/DOCX/text extraction), SaaS API client, task worker, heartbeat, local SQLite ledger, and CLI (`clarity-agent register/start/scan/status`)
- **Scheduler Integration**: `smb-heartbeat` agent added to AGENT_REGISTRY — cron job every 15 min pauses agents with no heartbeat for 15+ minutes
- **Frontend: SmbAdminPage** — 4-panel admin page (Status, Agents, Shares, Activity) with pairing code generation, agent pause/resume/revoke, share management, and access log viewer
- **Frontend: MatterSmbSharesTab** — "File Shares" tab on matter detail page for binding SMB shares/folders to matters with add/remove/auto-scan
- **Frontend: API functions** — 9 SMB admin functions + 4 matter binding functions added to api.js

### Changed
- Bug fixes in `services/smb.py` — proper UUID conversion via `_uuid()` helper, correct RLS context in pairing code generation and agent registration, cap-aware sync count, null-safe Redis access
- Bug fixes in `routers/smb.py` — correct FastAPI Body defaults, null-safe Redis via `request.app.state.redis`
- `RetrievalPlanner` — added `smb_enabled` parameter, `smb` source in PROVIDER_SOURCES, updated prompt
- `hybrid_rag_query()` — now checks for active SMB agents, runs tsvector search in parallel with pgvector/cloud, accepts `matter_id` for matter-scoped SMB search
- `AdminPage.jsx` — added "File Shares" tab with SmbAdminPage component
- `MatterDetailPage.jsx` — added "File Shares" tab with MatterSmbSharesTab component

## [0.11.0] — 2026-06-04

### Sprint 9 — Plugin Platform & Matter Workflow Framework

### Added
- Canonical plugin catalog manifest with display metadata, skill IDs, workflow routes, matter type mappings, required/optional integrations (`backend/app/services/plugins/manifest.py`)
- `TenantPluginEntitlement` model: tenant-level plugin purchase/trial/locked state, decoupled from practice profile
- `TenantPluginSetup` model: structured per-plugin configuration with typed schemas (jurisdictions, escalation rules, approval thresholds, templates, source folders, calendars, house style)
- Migration 034: `tenant_plugin_entitlements` table + `matters.primary_plugin` + `matters.plugin_workflow_state`
- Migration 035: `tenant_plugin_setups` table with `setup_data` JSONB + `needs_setup` tracking
- Plugin setup health endpoint (`GET /plugins/{plugin}/setup`) and upsert (`PUT /plugins/{plugin}/setup`)
- Plugin entitlement endpoint (`PUT /plugins/{plugin}/entitlement`) for admin-controlled purchase/trial state
- `GET /api/plugins` now returns canonical catalog with tenant entitlement, profile, and setup status merged per plugin
- PluginPage: setup health badges, capability checks (integrations, credentials), configuration tab with structured fields
- PluginsPage: category grouping, entitlement badges (Included/Trial/Purchase/Setup Required), matter workflow detail cards

### Changed
- Plugin manifest is now the single source of truth — frontend `PLUGIN_META` removed entirely; all plugin metadata derived from backend catalog API
- PluginsPage redesigned with state tabs (Purchased / Trials / Available / Setup Required / Locked) with per-tab counts
- Sidebar consolidated: plugin-specific workflow links replaced with single unified "Matters" link
- `POST /plugins/{plugin}/cold-start` now initializes structured `TenantPluginSetup` row alongside `PracticeProfile`
- `PluginExecutor` enriched with cloud search context via `RetrievalPlanner` + `CloudSearchService` + `build_cloud_context`
- Matters V2 router gained `primary_plugin` and `plugin_workflow_state` in create/update/list/detail
- `MatterContextService` enriched with plugin workflow state for conversation context
- `NewMatterModal` suggests plugins based on practice area, displays plugin assignment field
- `MatterDetailPage` shows assigned plugin + workflow state badge

### Fixed
- Plugin cold-start interview: fixed 422 from mismatched `{message, step}` → `{input_text, context}` request format
- Plugin cold-start interview: backend now returns `step`, `profile_complete`, `profile` alongside LLM result
- Plugin cold-start interview: frontend now reads `res.memo` (SkillResponse field) instead of `res.message`
- Cloud search: search_index and status DB queries wrapped in try/except to return degraded results instead of 500
- Cloud metadata sync: backend returns `total` + `duration_seconds` for frontend result panel
- Microsoft integration: `offline_access` scope now persisted when MS omits it from token response but refresh_token is present
- Google Workspace: added `openid email profile` to admin consent scopes; `last_sync_error` surfaced in audit UI
- Estate portfolio: migration 030 DDL manually applied on hypervisor (was stamped but never ran — missing columns + 7 sub-tables)

## [0.10.0] — 2026-06-03

### Sprint 8 — Tenant Onboarding & Integration Hub

### Added

#### PR #38 — Mediation Platform Module
- `MediationCase` model expanded: case_name, party_a/b, dispute_type, mediation_stage, mediator, attorney, claim_value, scheduled_session, confidentiality_signed
- New models: `MediationParty`, `MediationInvite`, `MediationAsset`, `MediationDocument`, `MediationProposal` with per-table RLS
- Firm router `/api/plugins/mediation/*`: case CRUD + stats, session log, parties + portal invites, asset schedule with attorney approve → send-to-opposing workflow, document vault upload/download, settlement proposals
- Portal router `/api/portal/mediation/*`: invite acceptance (magic link + JWT cookie), case view, asset submission/decision, document upload/download, proposal exchange
- Portal token helpers (`portal_token.py`), shared response builders (`mediation_service.py`), invite email (`email.py`)
- Migration 031 with 5 new tables + expanded mediation_cases
- Backend tests: 7/7 pass (CRUD, sessions, invites, approval workflow, visibility scoping, invite acceptance, proposal chains, tenant isolation)
- Frontend: `MediationPortfolioPage` with create modal, `MediationDetailPage` with 6 tabs (Overview, Parties, Assets, Documents, Proposals, Sessions), `MediationSubTable` generic CRUD component
- Portal frontend: `PortalAcceptPage` (magic link acceptance), `PortalCasePage` (4 tabs: My Assets, Shared With Me, Documents, Proposals)
- Sidebar "Mediation" nav entry, `App.jsx` portal routes outside ProtectedRoute

#### Task 801 — Admin Onboarding Wizard
- 5-step guided wizard after first admin login: Welcome → Connect Integrations → Sync Users → Review → Complete
- `GET/POST /api/admin/onboarding/status|complete|skip|step/{step}` endpoints
- Post-connect hooks in integration callbacks: auto-store granted_by_user_id + service_account_email, auto-trigger user sync
- `OnboardingWizard.jsx` with step indicator, skip option, progress persistence
- AuthCallback redirects new admins to /onboarding if not completed
- Migration 027: +onboarding_completed, onboarding_step, cloud_root_folder, service_account_email, license_active, granted_by_user_id, customer LLM fields

#### Task 802 — License/Seat Management
- `GET /api/admin/licensing` — per-user license status, seat counts, PAYG usage
- `PUT /api/admin/users/{id}/license` — toggle per-user license_active
- `PUT /api/admin/licensing/seats` — flat seat count with over-limit warning
- `LicensingPanel.jsx` — seat slider, usage progress bar, per-user toggle switches

#### Task 803 — Service Account Safety
- Integration callbacks store granted_by_user_id + service_account_email
- `GET /api/admin/integrations/health` — grantor info, deactivation warnings, expiry alerts
- Deactivate user now checks for service account grants; requires ?force=true

#### Task 804 — Cloud Folder Init & Matter Auto-Folders
- `cloud_init.py` — creates "claritylegal-records" root folder in OneDrive/Google Drive
- Auto-creates per-matter subfolders: emails/, documents/, pleadings/, correspondence/, billing/
- Hooked into matter creation (non-fatal) and onboarding completion

#### Task 805 — Customer LLM Configuration
- `POST/DELETE /api/admin/customer-llm/configure` — encrypted API key storage
- AdminPage Settings: Customer LLM section with toggle, provider, key, endpoint

#### Task 806 — Permission Audit → Integrations Hub
- `GET /api/admin/permissions` — granted vs required scope comparison per provider, +synced user count, +last-sync freshness (user_count, last_sync_at, last_sync_total, last_sync_status)
- `IntegrationsPanel.jsx` (renamed from PermissionsAudit): provider cards with scope checkmarks, synced user count display, last-sync timestamp, "Sync now" button
- Admin "Integrations" tab (renamed from "Permissions")
- Migration 030: `last_user_sync_*` columns on `tenant_credentials` for sync-run bookkeeping
- Daily directory user sync: new `user-sync` scheduler job (2:00 AM ET), manually triggerable via `/scheduler/agents/user-sync/run`
- `UserSyncService` persists last-sync state per credential and creates synced users on the free tier (`license_active=False`)

### Changed
- `Tenant` model: +onboarding_completed, onboarding_step, cloud_root_folder (JSON), service_account_email
- `TenantCredential` model: +granted_by_user_id (FK users.id), +last_user_sync_* columns (migration 030)
- `User` model: +license_active (bool, default true)
- `TenantSettings` model: +use_customer_llm, customer_llm_provider, customer_llm_config (JSON)
- AdminPage: +Licensing tab, +Integrations tab (was "Permissions")

### Fixed
- Cloud search status/metadata endpoints: error handling for missing/broken `cloud_metadata_index` table (returns degraded status instead of 500)
- Cloud metadata sync endpoint: added `total` and `duration_seconds` fields so the frontend "Sync Metadata Now" result panel renders correctly
- Microsoft integration: `offline_access` scope not persisted when MS omits it from token response despite granting it (refresh_token presence now forces scope inclusion)
- Google Workspace integration: added `openid email profile` to admin consent scopes so `id_token` is returned (needed for service account email extraction and proper scope audit)
- Google Workspace sync: `last_sync_error` now surfaced in permissions audit response and displayed in the Integrations panel

## [0.9.0] — 2026-06-03

### Added — Prompt Management System & Missing Skill Prompts

- `PromptOverride` model + migration 021: per-tenant prompt customization with RLS
- `PromptResolver` service: cache-aware resolution (tenant override → code default → generic fallback)
- Redis prompt caching with invalidation on override save/reset
- Admin prompt CRUD routes: list tree, get detail, upsert, reset, test-run prompts
- Admin console UI: "Prompts" tab with skill tree, code editor, variable reference, test panel
- 11 new prompt templates for previously missing skills (portfolio-status, legal-hold, renewal-tracker, reg-gap-analysis, diligence-review, closing-checklist, hire-review, marketing-claims, CND-triage, impact-assessment, vendor-ai-review, policy-diff, NPRM-comment)
- `ALL_DEFAULT_PROMPTS` dict wiring all 44 skill entries across 9 plugins
- Fixed: missing `run_conflict_check` import in plugins.py

## [0.8.0] — 2026-06-03

### Sprint 7 — Calendar, Communications & Matter Operations

### Added

#### Task 801 — Deadline Calendar
- `GET /api/calendar/events` endpoint aggregating task due_dates, matter key_dates, and renewal dates with `?start=&end=` range filter
- CalendarPage.jsx — month/week calendar view with color-coded events by type; click to navigate to matter/task detail

#### Task 802 — Communications Router
- Full CRUD router for `communication_logs` at `/api/communications` with filters by matter_id, contact_id, channel, date range
- CommunicationsPage.jsx — log list with filters and quick-log form (channel, subject, body, matter link)

#### Task 803 — Lead-to-Matter Conversion
- `POST /api/intake/leads/{id}/convert` — creates a Matter from a qualified Lead; sets `client_contact_id` from lead's contact; marks lead `status = matter_opened`; returns `{matter_id, matter_name, lead_id, status}`
- `LeadConvertRequest` schema (matter_name, matter_type, role, jurisdiction, counterparty)
- IntakePage: "Convert to Matter" button on engaged leads; modal with all required Matter fields; navigates to new matter on success
- `convertLead(id, data)` API helper in `frontend/src/api.js`

#### Task 804 — Matter Budget Tracking
- Migration 024: added `budget_amount` (Numeric 12,2) and `budget_currency` (String 3, default "USD") to `matters` table
- `GET /api/reports/matters/{id}/budget` — sums billable time entries (hours + amount) vs budget; returns utilization percentage
- `MatterBudgetReport` Pydantic schema (matter_id, matter_name, budget_amount, budget_currency, total_hours, total_billed, utilization_pct)
- `MatterResponse` and `MatterUpdate` schemas now include `budget_amount` and `budget_currency` fields
- MatterDetailPage.jsx: budget utilization badge in header (progress bar with color thresholds: green ≤70%, amber ≤90%, red >90%); budget amount and currency fields in edit form
- `getMatterBudget(matterId)` API helper in `frontend/src/api.js`

#### Task 805 — Document Templates
- `DocumentTemplate` model: title, body (Text with `{{variable}}` placeholders), category (engagement_letter/retainer/NDA/motion/other), is_active
- Migration 025: `document_templates` table with RLS (ENABLE + FORCE ROW LEVEL SECURITY, tenant_isolation policy)
- `GET /api/templates` — list active templates sorted by created_at desc
- `POST /api/templates` — create template with category validation (422 on invalid category)
- `GET/PATCH/DELETE /api/templates/{id}` — detail, update (validates category), delete
- `POST /api/templates/{id}/render` — `{{variable}}` regex substitution; optional `matter_id` creates a `MatterDocument` with `document_category="generated"`; verifies matter belongs to tenant (404 if not found)
- `render_template(template_body, variables)` — pure function re.sub replacer; unused variables preserved as-is (`{{name}}`)
- TemplatesPage.jsx — template library grid with category color badges, active/inactive toggle; create/edit modal (title, body textarea, category select); generate modal with auto-detected variable fields, preview render, option to save to a matter
- Sidebar nav link to `/templates` (FileSignature icon)
- Route `/templates` in App.jsx behind ProtectedRoute
- API helpers: `getTemplates`, `createTemplate`, `getTemplate`, `updateTemplate`, `deleteTemplate`, `renderTemplate`

### Changed
- Main.py: registered 7 new routers (contacts, tasks, communications, intake, matter_parties, matter_documents, reports, calendar, document_templates)
- Sidebar.jsx: added Reports, Calendar, Communications, Templates nav items
- App.jsx: added /reports, /calendar, /communications, /templates routes

### Fixed
- Recurring fix: router imports in main.py kept in sync after each task (formatter strips unused, added manually)
- RLS policies use correct `app.current_tenant_id` setting name with `, true` fallback
- Path traversal protection in matter document upload (os.path.basename)
- conflict_status uses "conflict-found" not "flagged" (standardized enum)
- Task reminders deduplicated via reminder_sent_at column (23h cooldown)

### Tests
- All endpoints verified with tenant isolation checks via spec/quality review cycle
- Frontend build succeeds for all 5 tasks

---

## [0.7.0] — 2026-06-03

### Sprint 6 — Matters, Document Management & Firm Reporting

### Added

#### MatterParty — Multi-Party Matter Support (701)
- `MatterParty` model — M:N link between matters and contacts with role (client/opposing_party/counsel/witness/expert/other), is_primary flag, notes
- Migration 021: `matter_parties` table with RLS tenant isolation
- `GET/POST /api/matters/{id}/parties` — list and add parties to a matter
- `PATCH/DELETE /api/matters/{id}/parties/{party_id}` — update role/notes, remove party
- Frontend: Parties tab in MatterDetailPage with role badges, add/remove form, contact dropdown

#### MatterDocument — Case File Attachments (702)
- `MatterDocument` model — file attachments linked to matters (separate from RAG document store)
- Migration 022: `matter_documents` table with RLS tenant isolation
- `POST /api/matters/{id}/documents/upload` — multipart file upload (50MB limit) with path traversal protection
- `GET/PATCH/DELETE /api/matters/{id}/documents/{doc_id}` — list, update metadata, delete
- `GET /api/matters/{id}/documents/{doc_id}/download` — FileResponse download
- Frontend: MatterDocumentsTab component with upload form, category badges (pleading/contract/evidence/correspondence/other), inline edit, download

#### Conflict Check Service (703)
- `backend/app/services/conflict_check.py` — shared conflict check service extracted from contacts router
- Auto-runs on matter create: sets `conflicts_status` ("not-run"/"clear"/"conflict-found") automatically
- `POST /api/plugins/litigation/matters/{id}/conflict-check` — manual re-run endpoint
- Frontend: conflicts_status badge + Re-run Check button in MatterDetailPage with match list display

#### Task Email Reminders (704)
- `send_task_reminder()` method in email service with HTML + plaintext body
- `_check_task_reminders` hourly APScheduler job — queries tasks due within 24h, sends per-assignee reminders
- Migration 023: `reminder_sent_at` column on tasks prevents duplicate hourly sends (23h cooldown)
- `POST /api/tasks/{task_id}/remind` — manual reminder trigger (202 Accepted)
- Frontend: Bell icon remind button per task row with inline "Sent!" confirmation

#### Firm Reporting (705)
- `GET /api/reports/matters` — matter counts by status, matter_type, risk_level
- `GET /api/reports/intake` — lead counts by status + conversion rate (matter_opened / total)
- `GET /api/reports/overdue-tasks` — overdue tasks with matter context
- `GET /api/reports/bundle` — all three reports in one request
- Frontend: `/reports` route, Sidebar nav link, ReportsPage with 3 summary cards

### Changed
- `contacts.py` conflict_check endpoint now delegates to shared `conflict_check` service (behavior unchanged)
- MatterDetailPage extended with Parties tab, Documents tab, conflict status badge

### Fixed
- Missing `matter_parties_router`, `matter_documents_router`, `reports_router` imports in `main.py`
- RLS policy in migration 021 corrected to use `app.current_tenant_id` (matching the app's `set_tenant_context`)
- Path traversal vulnerability in document upload fixed with `os.path.basename(filename)`
- `conflicts_status` value standardized to "conflict-found" (was "flagged" in initial implementation)

### Tests
- Integration: all new endpoints verified with tenant isolation checks via spec/quality review cycle

---

## [0.6.0] — 2026-06-03

### Added — CRM, Contacts, Tasks & Client Communication

#### Contact/Client Data Model
- `Contact` model — person or organization with entity_type, contact_type (client/opposing_party/witness/expert/vendor/referral/other), email, phone, address (JSON), tags, soft-delete
- `Lead` model — intake pipeline with status lifecycle (new→contacted→qualified→conflict_checked→engaged→matter_opened|declined), source, conflict_check_status, estimated_value
- Migration 018: `contacts` table with RLS; nullable `client_contact_id` FK added to `matters`
- `GET /api/contacts` — list with search (`q=`), contact_type/entity_type filters
- `POST /api/contacts` — create person or organization
- `GET/PATCH /api/contacts/{id}` — detail + inline edit
- `DELETE /api/contacts/{id}` — soft-delete (sets is_active=False)
- `GET /api/contacts/{id}/matters` — linked matters via client_contact_id
- `GET /api/contacts/{id}/communications` — communication history for contact
- `POST /api/contacts/conflict-check` — fuzzy name/email match against contacts + matter counterparty strings; returns clear/matches with matter linkage
- QBO sync: uses `Contact.display_name` when matter has `client_contact_id` set (fallback to `counterparty` string)

#### Task & Deadline Management
- `Task` model — task_type (deadline/hearing/filing/deposition/call/follow_up/review/general), status (pending/in_progress/completed/cancelled), priority (low/medium/high/urgent), due_date, matter_id, contact_id, assigned_to, source (manual/email_agent/calendar_sync)
- Migration 019: `tasks` table with RLS + performance indexes
- `GET /api/tasks` — list with filters: matter_id, contact_id, assigned_to, status, priority, task_type, due_before/after
- `POST /api/tasks` — create task
- `PATCH /api/tasks/{id}` — update; auto-sets `completed_at` on status→completed
- `GET /api/tasks/overdue` — tasks past due date, not completed/cancelled
- `GET /api/tasks/upcoming?days=7` — tasks due in next N days

#### Communication Log
- `CommunicationLog` model — direction (inbound/outbound), channel (email/call/letter/meeting/portal/sms/other), subject, summary, matter_id, contact_id, occurred_at, external_ref
- Migration 020: `communication_logs` + `leads` tables with RLS
- `GET /api/communications` — list with filters: matter_id, contact_id, channel, direction, occurred_after
- `POST /api/communications` — log entry
- `PATCH /api/communications/{id}` — update

#### Intake Pipeline
- `GET /api/intake` — list leads (filter by status, assigned_to, practice_area)
- `POST /api/intake` — create lead with inline Contact creation if needed
- `PATCH /api/intake/{id}` — update status/notes
- `POST /api/intake/{id}/convert` — convert to Matter (creates Matter with client_contact_id, marks lead as matter_opened)

#### Email Agent Integration
- Auto-create `CommunicationLog` (inbound/email/received) for each classified email
- Auto-create `Task` (type=deadline, source=email_agent) when classification returns `deadline_mentioned`
- Date parsing via `python-dateutil` with fuzzy parsing

#### Frontend
- `ContactsPage` (`/contacts`) — list/search contacts with type/entity filters, quick-create modal
- `ContactDetailPage` (`/contacts/:id`) — tabs: Profile | Matters | Communications | Tasks; inline edit
- `ContactPicker` component — search-as-you-type autocomplete for linking contacts in forms
- `TasksPage` (`/tasks`) — grouped sections: Overdue / Due Today / Upcoming / No Due Date / Completed; create modal with ContactPicker; filter by status/priority/type
- `IntakePage` (`/intake`) — pipeline view with stage counters; advance/convert actions; convert-to-matter modal
- Sidebar: added Contacts, Tasks, Intake nav links

### Changed
- `backend/app/models/plugin.py` — added nullable `client_contact_id` FK to `Matter`
- `backend/app/services/qbo_sync.py` — prefer Contact name over counterparty string when available
- `backend/app/services/email_agent.py` — auto-log communications and tasks on email classification
- `backend/requirements.txt` — added `python-dateutil==2.9.0`
- `frontend/src/api.js` — added 20 new API functions for contacts, tasks, communications, intake

## [0.5.2] — 2026-06-02

### Fixed — Security & Bug Fixes

#### Critical Bug Fixes
- `app/services/qbo_sync.py` — SQL injection in QBO query strings: escape single quotes in display_name, item_name, and customer_name via `_safe_qbo_string()` helper
- `app/routers/billing_extended.py` — Added `set_tenant_context()` to all 4 list endpoints (time entries, expenses, invoices, payments) for RLS correctness
- `app/routers/billing_extended.py` — `delete_time_entry` now hard-deletes unbilled entries (was incorrectly soft-deleting with `status=written_off` while returning 204)
- `app/routers/qbo.py` — QBO OAuth fallback state dicts now evict expired entries on each write to prevent unbounded memory growth
- `app/services/cache.py` — Fixed `invalidate_user_cache` key-pattern to match actual key format (`{type}:{tenant_id}|{user_id}|{suffix}`)
- `app/services/pii_detection.py` — Tightened `driver_license` regex (requires 9+ digits after letters) and `bank_account` regex (lookahead/behind to reduce false positives on phone numbers)

#### Sprint 2 Audit Fixes
- `app/routers/billing_extended.py` — Added missing `import asyncio` and `async_session_maker` (QBO sync fire-and-forget was broken at runtime)
- `app/services/rag.py` — Fixed SQL injection in pgvector queries: embedding vectors now passed as bind parameters instead of f-string interpolation
- `app/routers/billing_extended.py` — Added `logger.warning()` to silent `except Exception: pass` blocks in QBO sync tasks
- `app/routers/admin.py` — Added missing error schema imports (`ErrorLogResponse`, `SystemErrorLogsResponse`, `ErrorResolveRequest`, etc.)
- `app/routers/chat.py` — Wrapped `_trigger_auto_memory_generation` in try/except to prevent memory failures from breaking chat responses

## [0.5.1] — 2026-06-02

### Added — Trust Accounting + PDF Export

#### Trust Accounting CRUD
- `TrustAccount` CRUD endpoints (`POST/GET/PATCH /api/trust/accounts`, `POST /api/trust/accounts/{id}/close`)
- `TrustTransaction` endpoints (`POST/GET /api/trust/transactions`) with balance tracking
- Three-way IOLTA reconciliation (`POST /api/trust/accounts/{id}/reconcile`)
  - Bank balance vs trust liability vs unallocated funds
  - Auto-marks transactions as reconciled when balanced
  - Outstanding deposits/disbursements tracking
  - Reconciliation status endpoint (`GET /api/trust/accounts/{id}/reconciliation`)
- `TrustAccountCreate/Update/Response`, `TrustTransactionCreate/Response` Pydantic schemas
- `ReconciliationRequest/Response` with reconciling items detail
- `backend/app/routers/trust_accounting.py` — 8 endpoints
- `backend/app/schemas/trust_accounting.py` — 11 schemas

#### PDF Invoice Export
- `InvoicePDFService` — professional legal invoice PDF generation via ReportLab
- Clean letterhead layout: firm name, invoice details grid, line items table with totals, payments section, balance due
- `POST /api/billing/invoices/{id}/export` format=pdf returns `application/pdf`

### Changed
- `app/routers/__init__.py` — added trust_accounting_router
- `app/services/__init__.py` — added generate_invoice_pdf
- `app/main.py` — wired trust_accounting_router
- `requirements.txt` — added reportlab==4.2.5

## [0.5.0] — 2026-06-01

### Added — Billing & QBO Integration Foundation

#### Core Billing Models
- `TimeEntry` — billable time with matter link, UTBMS task/activity codes, status lifecycle (draft→billed→written_off)
- `Expense` — disbursements with category tracking (filing fees, court reporter, expert witness, etc.)
- `Invoice` — auto-numbered (INV-YYYY-XXXXXX), Stripe payment link, QBO sync status, LEDES export tracking
- `InvoiceLineItem` — polymorphic source tracking (time_entry/expense/flat_fee/adjustment/discount)
- `Payment` — multi-method (stripe/check/wire/trust_account/cash/other) with QBO sync
- 23 Pydantic v2 schemas in `schemas/billing.py`
- Migration 015: billing tables with RLS policies

#### QBO Integration
- `QBOIntegration` model — per-tenant QBO OAuth2 tokens (Fernet AES-256-GCM encryption, same pattern as TenantCredential)
- Full OAuth2 flow: `GET /api/integrations/qbo/connect` → callback → token exchange + encrypted storage
- Token refresh with refresh_token grant, sandbox/production toggle
- State-based CSRF protection with Redis fallback
- `QBOSyncService` — Matter→QBO Customer, TimeEntry→TimeActivity, Invoice→Invoice, Payment→Payment sync
- Migration 016: qbo_integrations table with RLS

#### Time Tracking & Billing CRUD
- TimeEntry CRUD: create, list (by matter/status/unbilled), detail, edit, soft-delete
- Expense CRUD: create, list (by matter/category/unbilled), detail, edit, delete
- Invoice generation: gather unbilled time+expenses → compute line items → auto-number → link sources
- Invoice CRUD: list, detail (with line items + payments), status transitions
- Payment recording with auto invoice status update (paid/partially_paid)
- Stripe Payment Link generation on invoice

#### Legal Billing Compliance
- LEDES 1998B pipe-delimited export (24-field format, full UTBMS task/activity code maps)
- Litigation (L100-L220), Counseling (C100-C800), Project (P100-P500), Bankruptcy (B100-B190) codes
- CSV invoice export

#### Trust Accounting Foundations
- `TrustAccount` model — per-matter IOLTA accounts with auto-replenish support
- `TrustTransaction` model — deposit/disbursement/transfer/replenishment/fee/adjustment types
- Migration 017: trust_accounts + trust_transactions tables with RLS

### Changed
- `app/config.py` — added QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REDIRECT_URI, QBO_ENVIRONMENT, QBO_WEBHOOK_VERIFIER
- `app/models/__init__.py` — registered 8 new models
- `app/schemas/__init__.py` — registered 28 new schemas
- `app/routers/__init__.py` — registered qbo_router, billing_extended_router
- `app/services/__init__.py` — registered QBOSyncService, export_ledes_1998b
- `app/main.py` — wired qbo_router, billing_extended_router

## [0.4.0] — 2026-06-02

### Added - Enhanced User Model & Context Management

#### User Preferences & Expertise Tracking
- `User.practice_areas` — JSON array of legal specializations (commercial, litigation, privacy, employment, product, IP, AI governance, regulatory, trust & estate, mediation)
- `User.expertise_level` — Proficiency classification: "junior", "mid", "senior" (drives cache TTLs and response complexity)
- `User.default_skill` — Preferred plugin/skill for routing (stored on user profile)
- `User.privacy_mode` — Strict PII handling flag (affects context injection and scrubbing)
- `User.memory_summary` — Auto-generated summary of user interactions and preferences
- `User.last_memory_update` — Timestamp for memory freshness tracking
- Migration 010: Add columns to `users` table with sensible defaults; index on practice_areas

#### Per-User Memory & Interaction Context
- `UserMemory` model with type-based storage:
  - `memory_type`: "preference" (user-set), "expertise" (observed), "matter_context" (case-specific), "interaction_pattern" (learned behavior)
  - `key` / `value` — Flexible key-value store (e.g., `preferred_rag_source_type`, `client_X_context`)
  - `confidence` — Relevance score 0–1 (how certain we are about this memory)
  - Timestamps and tenant/user isolation
- Migration 011: Create `user_memory` table with RLS
- `MemoryService` — CRUD ops + auto-summarization via LLM
- Auto-memory trigger: every 10 messages → `summarize_conversation()` → extract key facts/decisions → store as interaction_pattern
- Update `User.memory_summary` after each summary

#### PII Detection & Scrubbing
- 8 PII pattern types: SSN, credit card, phone, email, IP address, passport, driver's license, bank account
- Input scanning: detect PII in user messages before RAG query
- Output scrubbing: mask PII in assistant responses while preserving intent (e.g., "[MASKED_SSN]" instead of actual SSN)
- `PII Detection Service` (`services/pii_detection.py`):
  - `detect_pii(text: str)` — Returns list of {type, location, confidence}
  - `scrub_pii(text: str)` — Replaces with placeholders
  - `assess_pii_risk(text: str)` — Returns "low" | "medium" | "high"
- Guardrails integration: `apply_guardrails()` now returns `(cleaned_text, needs_retry, pii_findings)`
- Conversation flagging: Message.pii_flags stores detected PII metadata for audit
- User opt-in: privacy_mode=true enables stricter scrubbing

#### Explicit Context Usage Tracking
- Extended `Message` model:
  - `context_used` — JSON array of source IDs (document chunks, precedents, regulations) used in response
  - `context_relevance_scores` — Dict mapping source_id → relevance score (0–1)
  - `skill_applied` — Which plugin/skill was active for this message
  - `pii_flags` — Array of detected PII with type and confidence
- Chat response footer: **"### Sources & Context"** section shows:
  - Relevance scores for top 3 sources
  - Source type (Case law, Regulation, Firm material)
  - Hit rate summary (used X of Y retrieved)
- Migration 012: Add columns to `messages` table

#### Skill-Based Chat Routing
- Extended `MessageCreate` schema:
  - Optional `skill` field: route to specific plugin (e.g., "commercial-legal", "litigation-matter-intake")
  - Optional `matter_id` field: inject case context into conversation
- Chat endpoint enhancements:
  - If skill provided: prepend skill context to RAG prompt
  - If matter provided: load matter details, scrub PII if privacy_mode=true, inject into conversation history
  - Track applied skill in Message model + UsageRecord
- Skill-aware response templates (already in plugin system, now injected into RAG)

#### Tenant Settings & Feature Flags
- `TenantSettings` model (one per tenant, unique constraint):
  - Cache controls: `cache_enabled`, `cache_ttl_multiplier` (0.5–2.0)
  - User defaults: `default_expertise_level`, `default_practice_areas` (array), `default_privacy_mode`
  - Feature flags: `enable_auto_memory`, `enable_pii_detection`, `enable_skill_routing`, `enable_matter_context`
  - Rate limiting: `max_requests_per_minute`, `max_daily_tokens`
  - Custom config: JSON blob for tenant-specific overrides
  - Notes: Admin annotations
- Migration 014: Create `tenant_settings` table with RLS + indexes
- System defaults applied at tenant signup; admins override per-tenant
- New admin endpoints:
  - `GET /admin/settings` — Retrieve tenant settings
  - `PUT /admin/settings` — Update (admin only)

#### Expertise-Aware Caching
- `ExpertiseCacheManager` service — Three-tier caching by expertise level:
  - **Junior** (paralegal): RAG 1h, LLM 30m, matter 2h (40% hit target)
  - **Mid** (associate): RAG 30m, LLM 15m, matter 1h (25% hit target)
  - **Senior** (partner): RAG 15m, LLM 5m, matter 30m (10% hit target)
- Skill-based TTL multipliers:
  - Commercial 1.5x (higher complexity, longer cache OK)
  - Employment 1.3x
  - Litigation 0.7x (time-sensitive, shorter cache)
  - Renewal 2.0x (static data)
- Methods:
  - `get_cached_rag_results()`, `set_cached_rag_results()`
  - `get_cached_llm_response()`, `set_cached_llm_response()`
  - `get_cached_matter_context()`, `set_cached_matter_context()`
  - `invalidate_user_cache()` — Clear on privilege change
  - `get_cache_config()` — Retrieve active config for user
- Extended `UsageRecord` with cache tracking:
  - `cache_hit_rag` — Boolean, did RAG query hit cache?
  - `cache_hit_llm` — Boolean, did LLM response hit cache?
  - `cache_hit_matter` — Boolean, did matter context hit cache?
- Cache analytics endpoint: `GET /admin/cache-analytics`

#### Enhanced Admin Console
- New admin endpoints:
  - `GET /admin/tenant/detailed` — Full tenant profile with analytics:
    - User counts (total, active)
    - Message volume, total cost
    - Cache hit rate, avg response time
  - `GET /admin/users/{user_id}` — User detail with:
    - Practice areas, expertise, privacy mode, memory summary
    - Last activity, created/updated timestamps
  - `GET /admin/cache-analytics` — Cache performance metrics:
    - Total requests, cache hits, hit rate (%)
    - Per-tier hit rates (RAG, LLM, matter)
    - Estimated cost savings
- New schemas in `schemas/admin.py`:
  - `UserDetailResponse` — Full user profile
  - `TenantSettingsResponse`, `TenantSettingsUpdate`
  - `TenantDetailResponse` — Analytics-rich tenant view
  - `CacheAnalytics` — Performance metrics

#### Error Logging & Support Management
- `ErrorLog` model — Global error tracking:
  - Per-user and system-level logging (user_id nullable for system errors)
  - Error classification: api_error, rag_query_error, llm_error, cache_error, database_error, authentication_error, validation_error, timeout_error, rate_limit_error, permission_error
  - Severity levels: critical, error, warning, info
  - Request context: endpoint, method, status_code, IP address, user agent
  - Error details: message, stack trace, request ID
  - Conversation context: conversation_id, query_text for debugging
  - Resolution tracking: is_resolved, resolved_at, resolution_notes
  - Composite indexes for efficient 72-hour rolling per-user queries and system-level recent errors
- Migration 015: Create `error_logs` table with RLS
- Admin endpoints (pending implementation):
  - `GET /admin/errors/user/{user_id}?days=3` — Per-user 72-hour rolling error logs
  - `GET /admin/errors/system?days=3` — System-level errors
  - `GET /admin/errors/summary` — Error metrics and top issues

### Changed
- Chat endpoint: integrated cache manager, matter context loading with PII scrubbing, PII detection in user input
- Guardrails: extended to include PII detection alongside prohibited phrase checking
- Message model: now tracks context usage, skill applied, and PII flags for full audit trail
- Admin dashboard: enhanced tenant view with detailed analytics and user drill-down
- User model: expertise-driven system behavior (cache TTLs, response length, confidence thresholds)
- Auth schemas: use validated emails and password length constraints

#### Auth Hardening
- Existing tenant domains now require admin invitation/account pre-provisioning instead of automatic self-registration joins
- OAuth login callbacks now use short-lived frontend exchange codes instead of bearer JWTs in redirect URLs
- Integration OAuth connects now require authenticated initiating users and bind callback state to user, tenant, intent, and role
- Google OAuth login now rejects unverified Google email claims
- Backend-side auth rate limits now cover login, registration, forgot-password, and reset-password endpoints
- OAuth token storage now fails closed when `TOKEN_ENCRYPTION_KEY` is missing or invalid
- OAuth token expiry writes now use timezone-aware datetimes matching the database schema
- Per-user OAuth token lookup now includes explicit tenant filtering in addition to RLS
- Tenant RLS context is now set with a bound `set_config` parameter and UUID validation

### Migration Summary
- 010: Enhance user model (practice_areas, expertise_level, default_skill, privacy_mode, memory_summary, last_memory_update)
- 011: Create user_memory table
- 012: Extend message context tracking (skill_applied, context_used, context_relevance_scores, pii_flags)
- 013: Add cache tracking to usage_records (cache_hit_rag, cache_hit_llm, cache_hit_matter)
- 014: Create tenant_settings table (per-tenant feature flags and cache config)
- 015: Create error_logs table (per-user and system error tracking)

### Tests
- Lint: all new files pass ruff validation
- Auth: targeted ruff, Python compile, schema probe, frontend build, and regression grep checks for hardened auth modules
- Models: SQLAlchemy validation for RLS policies
- Schemas: Pydantic model_config set to "from_attributes=True" for ORM binding

## [0.3.0] — 2026-06-01

### Added
- CourtListener public RAG pipeline
  - `scripts/ingest_courtlistener.py` now extracts/chunks only and inserts `public_chunks` rows pending Jetson embeddings
  - `scripts/jetson_embed_worker.py` remains the BGE-small embedding writer for `public_chunks.embedding`
  - `scripts/create_public_chunks_index.sql` builds the IVFFlat index after embedding
  - `scripts/courtlistener_jetson_pipeline.md` documents the single-Jetson same-network workflow
  - RAG now searches `public_chunks` with optional BGE query embeddings alongside tenant document chunks
- Phase 1: OAuth token persistence — encrypted token vault with Fernet (AES-256-GCM)
  - `TenantCredential` and `UserOAuthToken` SQLAlchemy models with RLS
  - `TokenVault` service with auto-refresh for MS Graph + Google APIs
  - `GET /api/integrations/microsoft/connect|callback` — admin/user OAuth flows
  - `GET /api/integrations/google/connect|callback` — admin/user OAuth flows
  - `GET /api/integrations/status` — admin-only integration health
  - `POST /api/integrations/{provider}/disconnect` — revoke tokens
- Phase 2: Email agentic pipeline + Calendar sync
  - `MicrosoftMailService` — per-user/per-tenant inbox read via Graph API
  - `GoogleMailService` — Gmail API inbox read with label-aware filtering
  - `EmailAgent` — LLM classification (legal_query/court_filing/client_comm/etc) + draft response generation
  - `CalendarSyncService` — read/write M365 + Google Calendar; bidirectional deadline sync
  - `POST /api/email/scan` — scan + classify + draft responses
  - `POST /api/email/calendar` — list events + optional deadline sync
- Phase 3: Document sync for RAG
  - `DocumentSyncService` — sync from OneDrive, SharePoint, Google Drive
  - `GET /api/sync/documents/stats` — cross-drive document counts
  - `POST /api/sync/documents/list` — list legal documents by provider
  - `POST /api/sync/documents/sync-and-ingest` — background download + RAG pipeline ingestion
- Phase 4: Gemini + Azure OpenAI LLM providers
  - `LLMService._complete_gemini()` — Google Gemini 2.0 Flash via REST API
  - `LLMService._complete_azure()` — Azure OpenAI (GPT-4o) via SDK
  - Provider routing via `provider=` param on `LLMService.complete()`
- Phase 5: Admin user sync dashboard
  - `UserSyncService` — M365 Graph `/users` + Google Directory API sync
  - `POST /api/sync/users/microsoft` — sync M365 users to Clarity
  - `POST /api/sync/users/google` — sync Google Workspace users
  - `POST /api/sync/users/all` — sync both providers
- Config: `TOKEN_ENCRYPTION_KEY`, `AZURE_OPENAI_*`, `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_*`
- Migration 009: `tenant_credentials` + `user_oauth_tokens` tables with RLS
- New deps: `cryptography`, `google-auth-oauthlib`, `google-api-python-client`, `google-genai`

### Changed
- CourtListener sync tooling now targets `public_chunks` instead of tenant-scoped sentinel rows in `chunks`
- Jetson launcher defaults to one `JETSON_HOST`, with optional multi-host `JETSON_HOSTS`
- Auth OAuth flows: added `offline_access` scope to MS and Google login
- LLMService: added optional `provider` parameter for Gemini/Azure routing

### Tests
- Lint: all new files pass ruff (28 pre-existing issues in other files remain)

## [0.2.0] — 2026-05-31

### Added
- Email/password registration (`POST /auth/register`) with company details form
- Email/password login (`POST /auth/login`)
- Password reset flow (`POST /auth/forgot-password`, `POST /auth/reset-password`)
- SignupPage, ForgotPasswordPage, ResetPasswordPage (React)
- `password_hash` column to User model (005 migration)
- `company_name`, `staff_size`, `address`, `phone` columns to Tenant model (005 migration)
- JWT `iat` (issued-at) and `jti` (JWT ID) claims
- Token blacklist on logout via Redis (fallback to in-process dict)
- Healthchecks for postgres, redis, backend, frontend in docker-compose
- Production frontend Dockerfile (multi-stage Vite build + serve)
- `/health`, `/docs`, `/openapi.json`, `/redoc` proxying through nginx

### Changed
- Registration reuses existing domain tenant; first user gets admin
- Login queries scoped by created_at desc + limit(1)
- Logout now blacklists JWT tokens
- Backend Dockerfile: added wget for healthcheck
- Frontend Dockerfile: multi-stage build serving via `serve` instead of `vite dev`

### Fixed
- Sidebar: `documents.map` and `conversations.length` crashes (Array.isArray guards)
- Registration: missing `db.commit()` after user creation
- Login: `is_active` check added
- `passlib[bcrypt]` → `bcrypt>=4.0,<5.0` in requirements.txt (incompatibility)
- Reset tokens hidden when `DEV_MODE=false`
- Fallback dict TTL garbage collection
- CORS: added `https://172.16.16.202`

### Security
- `SECRET_KEY` regenerated
- `DEV_MODE=false` on hypervisor
- Credentials removed from `.env`
- `PRIMARY_LLM` reverted to `deepseek-chat`

## [0.1.0] — Initial

### Added
- Multi-tenant architecture with domain-based tenant isolation
- Row-Level Security (RLS) on all tables
- OAuth authentication (Microsoft, Google)
- Chat with DeepSeek + Claude Opus (RAG via pgvector)
- Document upload with vector embedding
- Plugin system: Litigation Matters, Commercial Renewals
- Admin dashboard (tenant users, usage stats)
