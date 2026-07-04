# Integrations Remediation & Enhancement Plan (Microsoft 365 + Google Workspace)

**Status:** Proposed
**Date:** 2026-07-01
**Supersedes:** `docs/cloud-integrations-enhancement-plan.md` (2026-06-12)
**Scope:** OAuth (login + integrations), token vault, storage integration (OneDrive/SharePoint/Google Drive) and matter↔folder mappings, mail/calendar sync, background jobs, observability, tests

---

## 1. Executive summary

A full review of the M365/Google integration layer (OAuth flows, token lifecycle, storage mappings, mail/calendar sync, scheduler jobs, tests) confirms the layer is fragile — but the fragility is systemic, not a set of independent bugs. Three root patterns explain almost every symptom:

| Root pattern | Effect |
|---|---|
| **A. Silent-failure design** — token refresh failures, provisioning errors, and sync errors are caught, logged at `warning`, and swallowed (`return None` / `except Exception: pass`) throughout the layer | Integrations die silently; `/api/integrations/status` reports "connected" indefinitely; partial folder provisioning is marked `provisioned`; nothing reaches the admin `ErrorLog` |
| **B. No shared HTTP/token discipline** — ~35 of ~40 integration `httpx` calls have **no timeout**; no retry/backoff or 429 handling outside `teams.py`/`qbo_sync.py`; token refresh logic copy-pasted ~5× with **no locking** (races against Microsoft's rotating refresh tokens); Graph/Drive listing implemented 3× in parallel | Hangs, throttling outages, refresh-token family invalidation, inconsistent error contracts, divergence risk |
| **C. Polling-only mapping with no reconciliation** — no Graph `/delta`, no Drive `changes.list`, no webhooks; `cloud_metadata_index` is upsert-only (never pruned); scheduled sync indexes **top-level folders only**; `matter_documents` is never reconciled against cloud reality; cloud-doc delete is a 501 stub | Stale/orphaned index entries, dead links, un-deletable documents, matter subfolders invisible to search |

On top of these, the review found **security defects** the June plan did not cover: Microsoft login id_tokens are never signature-verified, there is no PKCE, disconnect never revokes tokens at the provider, tenant-wide access rides on a single admin's delegated token with very broad scopes, and all tokens are encrypted under one non-rotatable Fernet key.

### Status of the June 2026 plan

| June plan item | Status |
|---|---|
| 0.1 MS token-exchange scope parity (shared constants) | ✅ Done — `MICROSOFT_USER_SCOPES` etc. used in both authorize URL and exchange (`integrations.py:94-113, 239-283`) |
| 0.2 User-level scope upgrades | ✅ Done — MS user has `Files.ReadWrite.All Calendars.ReadWrite`; Google user has `drive calendar gmail.readonly` |
| 0.6 Scheduled email scan | ✅ Partially — `correspondence-capture` scheduler job ships (`scheduler.py:87, 1299`); the LLM `email_agent` pipeline remains manual-only |
| Task calendar push per-user tokens | ✅ Done (TASKS.md 2026-06-11) |
| 0.3–0.5, 0.7 (provisioning race, `storage_backend` column, error classification, scheduled calendar sync) | ❌ Not done |
| Phase 1 health spine (`integration_sync_runs`, token health columns, scope audit) | ✅ Done — shipped as remediation Phase 2 via migration `073_integration_observability`; admin health cards now show token health, refresh errors, reconnect state, and recent sync runs |
| Phase 2 calendar engine (`external_calendar_events`) | ❌ Not done |
| Phase 3 email pipeline (`email_matter_links`, matching v2, delta sync) | ❌ Not done |
| Phase 4 RAG email | ❌ Not done |
| Phase 5 file polish (reconcile job, cloud import UI, backfill) | ❌ Not done |

This plan absorbs the un-shipped June items and re-sequences them behind the security and reliability fixes that must land first.

---

## 2. Current-state map

### 2.1 Two OAuth surfaces

| Purpose | Router | Redirect base | Notes |
|---|---|---|---|
| Login / SSO (identity) | `backend/app/routers/auth.py` | `/api/auth/{provider}/callback` | Authorization-code, confidential client. Tokens not persisted; identity claims upsert tenant+user. Callback mints app JWT → one-time Redis code → frontend `AuthCallback.jsx` exchanges for httpOnly cookies. |
| Integrations (data access) | `backend/app/routers/integrations.py` | `/api/integrations/{provider}/callback` | `intent=admin` → `TenantCredential` (tenant-wide); `intent=user` → `UserOAuthToken` (per-user). Same shape for Microsoft, Google, Zoom, Zoom Phone. |

Neither surface uses PKCE or an OIDC `nonce`. `MICROSOFT_TENANT_ID` defaults to `common` (multi-tenant app). The "admin" connect uses the ordinary `/authorize` endpoint with `prompt=select_account` — **not** the `/adminconsent` endpoint — and relies on the connecting user actually holding Entra admin rights; the app-side check is only its own `role == "admin"`.

### 2.2 Token vault (`backend/app/services/token_vault.py`)

Fernet-encrypted at rest under a single global `TOKEN_ENCRYPTION_KEY` (validated at boot — the only env var that is). Two grains: `get_fresh_token` (tenant) and `get_fresh_user_token` (per-user), each with per-provider refresh POSTs (~5 near-duplicate blocks). 60s expiry skew. No locking, no retry, no health persistence, no tests.

### 2.3 Storage mapping model

- Files live in the **customer's** cloud under `claritylegal-records/{matter}/{emails,documents,pleadings,correspondence,billing}`.
- Tenant root IDs in `tenants.cloud_root_folder` (JSON); per-matter folder/subfolder IDs in `matters.cloud_folder` (JSON, includes `_status`).
- `matter_documents.storage_path` stores the provider **webUrl**; `storage_backend` is a derived property that substring-sniffs the URL (`models/matter_document.py:61-83`). No provider object-ID column.
- Provisioning: matter create fires `asyncio.create_task(_provision_cloud_folders(...))` (`routers/matters.py:685`, fire-and-forget); `cloud_init.py` `_ensure_*_folder` helpers list-then-create with partial 409 recovery.
- Uploads: `matter_file_store.store_matter_file` tries providers in order, falls back to local disk; chunked/resumable upload above 4/5 MiB.
- Change detection: `cloud-sync` scheduler job every `CLOUD_METADATA_SYNC_INTERVAL_MIN` (15 min) re-lists **top-level** children into `cloud_metadata_index` (PG upsert). No delta/webhooks. `cloud_search.py` does live provider search at chat/RAG time, scoped to matter folder IDs when available.
- SMB relay (`smb.py`) is a third, orthogonal storage backend (agent-paired on-prem shares).

### 2.4 Mail / calendar / directory

- Mail readers: `google_mail.py`, `microsoft_mail.py` (delegated `/me` endpoints; N+1 metadata loop for Gmail).
- Calendar: `calendar_sync.py` (read/create/delete + deadline sync — manual trigger only, no scheduled job), `google_calendar.py` / `microsoft_calendar.py` (task push, dedupe via extended properties).
- Directory: `user_sync.py` (Graph `/users`, Google Admin Directory) — the best-instrumented service (persisted `last_user_sync_*` state); auto-creates users with `license_active=True`.
- Email→matter: `correspondence_capture.py` (scheduled, rule-based `.eml` archiving) and `email_agent.py` (manual, LLM classification) — two parallel pipelines.
- Scheduler (`scheduler.py`): 11 agents, pg advisory-lock guarded (good), per-tenant failure isolation (good), but failures land only in `SchedulerLog` — never in the admin-visible `ErrorLog` (`error_log.py` / `error_tracker.py` exist and are unused by this layer).

---

## 3. Findings register

Severity: **P0** = security or data-integrity defect; **P1** = reliability defect actively causing fragility; **P2** = quality/debt.

### Security

| # | Sev | Finding | Where |
|---|---|---|---|
| S1 | P0 | Microsoft login id_token **signature never verified** — payload base64-decoded and trusted; no aud/iss/nonce validation | `auth.py:509-521` |
| S2 | P0 | Google id_token verification **skipped entirely in `DEV_MODE`**; `DEV_MODE` also exposes reset tokens | `auth.py:635-639, 1186` |
| S3 | P0 | Domain-keyed tenants + **auto-admin for first user of a new domain** — signup at an unregistered domain grants admin of that domain's tenant | `auth.py:314-331, 379` |
| S4 | P1 | **No provider-side token revocation on disconnect** — only local rows deleted; tokens stay live at MS/Google until natural expiry | `integrations.py:1298-1357` |
| S5 | P1 | No PKCE; state not session-bound; MS/Google callbacks don't verify `state.provider` matches the route (only Zoom Phone does) | `integrations.py:254, 441, 772` |
| S6 | P1 | Tenant-wide integration = **one admin's delegated token**, not app permissions / real admin consent; breaks when that admin's account changes; scopes over-broad (full `drive`, `Files.ReadWrite.All`, org-wide `Mail.Read`) | `integrations.py:94-101, 212-251` |
| S7 | P1 | Single global Fernet key; no rotation (`MultiFernet`), no per-tenant keys | `token_vault.py:17-24` |
| S8 | P2 | ~~`tenant_credentials` lacks a `(tenant_id, provider)` unique constraint~~ — **corrected on implementation**: migration 009 already added `ix_tenant_credentials_tenant_provider` (unique), so this was never exploitable in production; the ORM model just didn't declare it, so the `Base.metadata.create_all()`-built test schema lacked the same protection (now fixed). `user_oauth_tokens` unique index omitted `tenant_id` (fixed: widened to `(tenant_id, user_id, provider)` in migration 072) | `models/tenant_credential.py`, `models/user_oauth_token.py`, `migrations/versions/009_oauth_tokens.py`, `migrations/versions/072_cred_unique_constraints.py` |
| S9 | P2 | Only `TOKEN_ENCRYPTION_KEY` validated at boot; integration connect endpoints don't pre-check client config (login's `_oauth_configured` is not reused) | `config.py:200-224`, `auth.py:398-406`, `integrations.py:212, 402` |
| S10 | P2 | OneDrive folder search interpolates unescaped user query into the Graph URL (SharePoint variant escapes) | `cloud_search.py:985` vs `:1042` |
| S11 | P2 | Cloud search under tenant-level tokens is not user-permission-filtered (one user's chat can surface another's mail metadata) | `rag.py` / `cloud_search.py` (June plan §2.4.4) |

### Token lifecycle

| # | Sev | Finding | Where |
|---|---|---|---|
| T1 | P0 | **Refresh race**: no row locking; concurrent refreshes can consume/clobber Microsoft rotating refresh tokens and invalidate the family → integration silently bricked | `token_vault.py:47-105, 304-406` |
| T2 | P1 | **Refresh failures swallowed** — every refresher `return None`; `is_active` never flipped; no error recorded; status endpoints report "connected" forever | `token_vault.py` (all refreshers), `integrations.py:1244, 1273` |
| T3 | P1 | No retry/backoff on token endpoints (transient 5xx/429 = hard failure) | `token_vault.py` |
| T4 | P2 | Refresh POST duplicated ~5× (tenant + per-user × provider); per-user block is a near-verbatim copy | `token_vault.py:47-217, 304-406` |
| T5 | P1 | `token_vault.py` has **zero tests** | `backend/tests/` |

### HTTP layer

| # | Sev | Finding | Where |
|---|---|---|---|
| H1 | P1 | **~35 of ~40 integration `httpx.AsyncClient()` calls have no timeout** — a hung endpoint stalls requests and scheduler jobs indefinitely | `token_vault.py` ×6, `calendar_sync.py` ×8, `google_calendar.py`/`microsoft_calendar.py` ×7, `user_sync.py` ×2, mail list calls, `cloud_sync.py`, `document_sync.py`, OAuth callbacks |
| H2 | P1 | No 429/`Retry-After`/backoff anywhere except `teams.py:145-195` and `qbo_sync._retry_with_backoff` | all cloud services |
| H3 | P2 | `GRAPH_BASE`/Google bases re-declared in 6+ files; Graph/Drive listing implemented 3× (`cloud_sync`, `document_sync`, `cloud_search`) with divergent fields/filters/pagination | services layer |
| H4 | P2 | Inconsistent error contracts: `RuntimeError` (mail) vs `ValueError`→424 (calendar reads) vs log-and-return-`None` (creates/pushes) vs bare bool (deletes) | services layer |
| H5 | P2 | Gmail read is an N+1 loop (one GET per message), no batching/concurrency | `google_mail.py:57-72` |

### Storage / mappings

| # | Sev | Finding | Where |
|---|---|---|---|
| M1 | P0 | **Cloud-doc delete is a 501 stub** — neither the cloud file nor the DB row can be removed | `routers/matter_documents.py:198-205` |
| M2 | P1 | Polling only — Graph `/delta` and Drive `changes.list` unused; `CloudMetadata.sync_cursor` written but never read; index upsert-only, deletions never pruned → stale search hits, orphaned rows | `cloud_sync.py`, `models/cloud_metadata.py` |
| M3 | P1 | Scheduled sync enumerates **top-level `root/children` only** — matter subfolders (the point of the tree) are never indexed; `document_sync.py` fetches a single page (~100 cap, silent truncation) | `cloud_sync.py:575`, `document_sync.py:218-288` |
| M4 | P1 | Folder-name inconsistency: background create `"{name} ({id[:8]})"` vs bare `slug` on provision/sync endpoints vs SharePoint always slug → duplicate/diverging folders across providers and code paths | `matters.py:251-255, 2298, 2578`, `cloud_init.py:229` |
| M5 | P1 | Provisioning is fire-and-forget `asyncio.create_task` (lost on restart, no retry); **partial success still marked `provisioned`**; Google `_ensure_gdrive_folder` has no 409 recovery (TOCTOU duplicate folders) | `matters.py:263-264, 685`, `cloud_init.py:561-575` |
| M6 | P1 | OneDrive root is `/me/drive` — the connecting **admin's personal drive** — while sync/search resolve tenant tokens; write/read identity mismatch, root is per-user not per-firm | `cloud_init.py:47`, `matter_file_store.py:171` |
| M7 | P1 | No reconciliation between `matter_documents` and cloud reality (dead "Open in OneDrive" links); local-disk fallback silently downgrades cloud firms with no backfill | `matter_file_store.py:507`, `MatterDocumentsTab.jsx:258` |
| M8 | P2 | OneDrive/SharePoint upload `conflictBehavior=rename` with no pre-check → `file 1.docx`, `file 2.docx` on re-upload (only Google dedupes) | `matter_file_store.py:171, 477` |
| M9 | P2 | `storage_backend` derived by URL substring sniffing (custom SharePoint domains mislabeled); no provider object-ID column on `matter_documents` | `models/matter_document.py:61-83` |
| M10 | P2 | Per-matter "Sync folder" button triggers tenant-wide `sync_all` (O(tenant) work per click, self-inflicted rate-limit risk) | `matters.py:2593` |
| M11 | P2 | Matter delete cascades DB rows but orphans cloud folders/files; `cloud_folder` JSON written last-writer-wins across concurrent remap/context edits | `matters.py`, `models/plugin.py:283` |

### Observability / jobs / tests

| # | Sev | Finding | Where |
|---|---|---|---|
| O1 | P1 | Integration/scheduler failures never reach the admin-visible `ErrorLog` — `error_log.py`/`error_tracker.py` exist and are unused by this layer | `scheduler.py`, services |
| O2 | P1 | No `integration_sync_runs`, no token-health columns; `cloud_search_status` swallows all exceptions into a fake "disconnected" response | `routers/cloud_admin.py:175-195` |
| O3 | P1 | In-process OAuth state fallback has no GC and is per-worker (memory leak; broken without Redis); refresh/logout revocation also degrade per-worker without Redis | `integrations.py:48-49`, `auth.py:256-261, 1243-1251` |
| O4 | P2 | Directory sync auto-creates users `license_active=True` — silent seat/billing exposure | `services/user_sync.py:141-143, 275-277` |
| O5 | P1 | Test gaps: zero coverage for `token_vault`, mail readers, calendar push, `email_agent`, `correspondence_capture` DB path; no HTTP error-path (401/403/404/429/timeout) tests anywhere | `backend/tests/` |
| O6 | P2 | `scan_and_capture` keyword/direction rule filters documented but unimplemented (dead config) | `correspondence_capture.py:17-19` |

---

## 4. Phased remediation

Phases are ordered so that security lands first, the reliability substrate second (everything later depends on it), observability third (so later phases are measurable), and features last.

### Phase 0 — Security hardening (~1 week)

| # | Status | Fix | Files |
|---|---|---|---|
| 0.1 | ✅ Done | **Verify Microsoft id_tokens**: JWKS signature + `aud`/`iss`/`tid` validation via a new shared `verify_microsoft_id_token`/`verify_google_id_token` in `utils/oauth_security.py` (mirrors the previous `_verify_google_id_token`); added `nonce` to both providers' login authorize URLs and verified on callback | `auth.py`, `utils/oauth_security.py` (new) |
| 0.2 | ✅ Done | **Removed the `DEV_MODE` verification bypass** — Google id_token signature verification now always runs, no dev shortcut | `auth.py`, `utils/oauth_security.py` |
| 0.3 | ✅ Done | **PKCE (S256)** added to both OAuth surfaces (login `auth.py` + integrations `integrations.py` for Microsoft/Google); `code_verifier` stored in state meta, sent on token exchange; `state.provider` now checked against the callback route for Microsoft/Google (Zoom Phone already did this) | `auth.py`, `integrations.py`, `utils/oauth_security.py` |
| 0.4 | ✅ Done | **Revoke at provider on disconnect**: added `revoke_provider_token`/`revoke_google_token`/`revoke_microsoft_token` to `token_vault.py` (Google calls the real revoke endpoint; Microsoft is a documented no-op — no safe per-token revoke API exists without over-broad `revokeSignInSessions`); wired into both disconnect endpoints before row deletion | `token_vault.py`, `integrations.py` |
| 0.5 | ✅ Done | **DB constraints migration**: `tenant_credentials` was already protected by a unique index (migration 009) — added the equivalent `UniqueConstraint` to the ORM model so the test schema matches; widened `user_oauth_tokens`' unique index to `(tenant_id, user_id, provider)` via migration 072 (verified upgrade/downgrade/re-upgrade against a live Postgres 16 instance) | `models/tenant_credential.py`, `models/user_oauth_token.py`, `migrations/versions/072_cred_unique_constraints.py` |
| 0.6 | ✅ Done | **Boot/config validation**: `integrations.py` Microsoft/Google connect endpoints now call the shared `is_oauth_client_configured` (moved from `auth.py`'s private `_oauth_configured`) and return 501 on missing/placeholder config, matching login and Zoom | `utils/oauth_security.py`, `auth.py`, `integrations.py` |
| 0.7 | ✅ Done | **State hygiene**: added `_gc_fallback_states` GC to `integrations.py`, called from `_save_state` (same pattern as `auth.py`) | `integrations.py` |
| 0.8 | ⏸ Deferred | **Harden tenant auto-creation** — needs a product decision (domain verification vs. invite-only vs. accept current behavior); not implemented pending input (see §7) | `auth.py:314-392` |
| 0.9 | ✅ Done | Escaped the OneDrive search query (mirrors the SharePoint `''` escaping) | `cloud_search.py` |

### Phase 1 — Reliability core (~1.5 weeks)

| # | Status | Fix | Files |
|-|-|-|-|
| 1.1 | ✅ Done (core + mail slice) | **Shared provider HTTP clients**: added `services/provider_http.py`, `services/graph_client.py`, and `services/google_client.py` with shared base URLs, default timeout, 429/`Retry-After` honoring, bounded transient retry, and typed exceptions (`ProviderAuthError`, `ProviderThrottled`, `ProviderNotFound`, `ProviderError`). Migrated the Microsoft/Gmail mail readers as representative covered callers. | `provider_http.py`, `graph_client.py`, `google_client.py`, `google_mail.py`, `microsoft_mail.py` |
| 1.1b | ⏸ Deferred | Migrate the remaining wide raw-HTTP fanout after storage/provider identity semantics are tightened: `calendar_sync.py`, `google_calendar.py`, `microsoft_calendar.py`, `user_sync.py`, `cloud_sync.py`, `cloud_init.py`, `cloud_search.py`, `document_sync.py`, `matter_file_store.py`, and admin/provider probes. | Phase 3+ service migrations |
| 1.2 | ✅ Done | **Consolidate token refresh** into one code path used by tenant and user grains; `SELECT ... FOR UPDATE` plus a bounded Postgres lock timeout wraps check-then-refresh; rotated refresh tokens are persisted. | `token_vault.py` |
| 1.3 | ✅ Done | **Persist token health**: on refresh failure record `last_refresh_error`/`last_refresh_at`; on `invalid_grant` flip `is_active=False` where applicable and set `health='revoked'`; token endpoints now retry transient 5xx/429/transport failures. | `token_vault.py`, models + migration 073 |
| 1.4 | ✅ Done | **Truthful status**: `/api/integrations/status` and admin health surfaces expose `health`/`reconnect_required` instead of row-exists=connected. Calendar-provider status uses the same persisted token-health fields where available. | `integrations.py`, `admin.py`, credential models |
| 1.5 | ⏸ Deferred | **Consistent router error contract**: services now have typed provider exceptions in the shared layer, but broad router-level remapping (`ProviderAuthError`→424, `ProviderThrottled`→503+Retry-After) remains deferred until the remaining services migrate. | routers + remaining services |
| 1.6 | ✅ Done | **Token vault and provider-client test suites**: coverage for refresh success/rotation persistence, `invalid_grant` health flip, 5xx retry, provider 401/429/5xx mapping, mail reader client usage, and a Postgres-only concurrent-refresh single-flight regression. | `backend/tests/test_token_vault.py`, `test_provider_http.py`, `test_mail_provider_clients.py` |

### Phase 2 — Observability spine (~1 week)

| # | Status | Fix | Files |
|-|-|-|-|
| 2.1 | ✅ Done | **`integration_sync_runs` table** (tenant_id, provider, job_type, started/finished, status, items_ok, items_failed, error_summary) + token-health columns (`health`, `last_refresh_error`, `scopes_version`) on both credential tables | `models/integration_sync_run.py`, `migrations/versions/073_integration_observability.py`, credential models |
| 2.2 | ✅ Done | **Scope audit at callback**: compare granted `scope` to the required set; persist the gap; mark `missing_scopes` instead of discovering at call time | `integrations.py`, `services/integration_observability.py` |
| 2.3 | ✅ Done | **Wire `error_tracker`/`ErrorLog`** into integration scheduler jobs and per-tenant failure branches; fix `cloud_search_status` to report real errors | `services/scheduler.py`, `routers/cloud_admin.py` |
| 2.4 | ✅ Done (health slice) | **Admin dashboard**: per-provider cards now show token health, refresh errors, reconnect state, and recent sync runs; existing "Sync now" remains wired. Docs local-vs-cloud counts/failing matters/repair remain Phase 3 storage work. | `routers/admin.py`, `IntegrationsPanel.jsx` |
| 2.5 | ⏸ Deferred | **User re-auth prompts**: banner + settings card when the user's own token is missing/expired/under-scoped | frontend |
| 2.6 | ⏸ Deferred | **De-silence chat cloud search**: record skip/fail reason (no token / planner declined / provider error) into chat response metadata; failure stays non-fatal but becomes diagnosable | `rag.py:301-388` |
| 2.7 | ⏸ Deferred | Make directory-sync licensing explicit: config or admin toggle for auto-`license_active` on synced users | `services/user_sync.py` |

### Phase 3 — Storage correctness (~1.5 weeks)

| # | Status | Fix | Files |
|-|-|-|-|
| 3.1 | ✅ Done (metadata-backed rows) | **Implement cloud document delete**: DELETE removes the provider item by object ID with `ProviderNotFound` tolerated, then the DB row. Legacy URL-only cloud rows still fail closed because they cannot be safely routed. | `routers/matter_documents.py`, `matter_file_store.py` |
| 3.2 | ⏸ Deferred | **Delta sync + pruning**: adopt Graph `/delta` and Drive `changes.list`/`startPageToken` cursors (persist in the existing `sync_cursor` column); tombstone/prune `cloud_metadata_index` rows on deletion/move; full re-list only as fallback/backfill | `cloud_sync.py`, `models/cloud_metadata.py` |
| 3.3 | ⏸ Deferred | **Index matter subfolders**: recursive (or matter-folder-scoped) enumeration in the scheduled sync; add pagination loops to `document_sync.py` | `cloud_sync.py:575`, `document_sync.py` |
| 3.4 | ⏸ Deferred | **Unify folder naming**: one `matter_folder_name(matter)` helper used by background provisioning, provision/sync endpoints, and all three providers (incl. SharePoint) | `cloud_init.py`, `matters.py:251-255, 2298, 2578` |
| 3.5 | ✅ Done (new writes) | **Real storage columns**: `storage_provider`, `storage_backend`, `storage_error`, `provider_object_id`, `provider_drive_id`, and `provider_parent_id` on `matter_documents`; uploads now return structured provider metadata and new matter uploads persist it. Historical backfill remains deferred. | `models/matter_document.py`, `matter_file_store.py`, migration 074 |
| 3.6 | ⏸ Partial | **Provisioning robustness**: structured upload results now expose failures, and Google Drive folder creation now recovers from 409 duplicate races like OneDrive/SharePoint. Tracked retryable provisioning, partial provider status, and OneDrive/SharePoint upload pre-check dedupe remain open. | `matters.py:685`, `cloud_init.py`, `matter_file_store.py` |
| 3.7 | ⏸ Deferred | **Folder reconcile job + repair endpoint**: weekly verification that root/matter/subfolder IDs still resolve; auto-repair + report via `integration_sync_runs`; per-matter "Repair cloud folders" button | new scheduler agent, `matters.py`, frontend |
| 3.8 | ⏸ Deferred | **Local→cloud backfill** task: push locally-stored docs (now identifiable via 3.5) to the correct matter folder once a healthy credential exists; dedupe against existing files | script/scheduler task |
| 3.9 | ✅ Done | Scope the per-matter "Sync folder" action to that matter's primary/subfolder/context folders instead of tenant-wide `sync_all`; scheduler/admin sync still uses `sync_all`. | `matters.py`, `cloud_sync.py` |
| 3.10 | ⏸ Deferred | Merge/retire duplicate listing implementations: `document_sync.py` browse/ingest moves onto the shared clients + `cloud_sync` index | `document_sync.py` |

### Phase 4 — Feature verticals (carried from June plan; ~4 weeks)

**4A. Calendar sync engine (~1.5 wk)**
- `external_calendar_events` mapping table (tenant, user, provider, source_type [task/key_date/renewal/estate_deadline], source_id, external_event_id, content_hash, status).
- Unified outbound sync service for both providers (create/update/delete), replacing the extended-property dedupe hack; wire task update/delete hooks; hourly `calendar-sync` scheduler agent reconciles drift via `content_hash`.
- Tenant/user timezone setting replaces hardcoded `America/New_York`.
- Frontend: last-synced status, per-user "sync my tasks" preference.

**4B. Email→matter pipeline (~1.5 wk)**
- `email_matter_links` M:N table (provider, message_id, conversation_id, matter_id, link_source, confidence, linked_by).
- Matching v2 in one pipeline (fold `email_agent` matching and `correspondence_capture` rules together): exact-normalized address match (drop `ILIKE %…%`), matter-number regex, thread continuity; below-threshold → review queue.
- Use delta cursors from 3.2 instead of the fixed 7-day window.
- Frontend: Emails tab on MatterDetailPage; review queue ("N emails need filing"); replace UUID paste in CommunicationsPage filters.

**4C. RAG email access (~1 wk)**
- Deterministic retrieval for explicit asks ("search my email for…" bypasses the LLM planner gate).
- Per-user permission filter when only a tenant token exists (restrict to the requester's mailbox) — closes S11.
- Chat metadata: "searched Gmail (3 hits) / email search skipped: account not connected".
- Opt-in matter-email indexing (embed linked emails as matter-scoped chunks; tenant setting `index_matter_emails`, default off).

### Phase 5 — Architectural upgrades (~2 weeks, optional/parallelizable)

| # | Enhancement | Rationale |
|---|---|---|
| 5.1 | **Microsoft application permissions (client credentials) or Google service-account DWD** for tenant-wide operations (directory sync, org drive, mail capture) — real `/adminconsent` flow for MS | Decouples tenant integration from one admin's account lifecycle (S6); `GOOGLE_SERVICE_ACCOUNT_*` env vars already exist unused |
| 5.2 | **`Sites.Selected`** + SharePoint site/shared-drive as the org root instead of `/me/drive` | Least privilege; fixes the per-user-root mismatch (M6) |
| 5.3 | **Per-tenant BYO OAuth apps for MS/Google** — extend the existing `tenant_oauth_apps` (Zoom Phone pattern, `tenant_oauth_apps.py:51-73`) | Larger firms can use their own app registrations; blast-radius isolation |
| 5.4 | **Fernet key rotation** via `MultiFernet` (new key first, old keys accepted for decrypt; background re-encrypt) | S7 |
| 5.5 | **Webhooks**: Graph change-notification subscriptions + Drive push channels for files/mail; polling (with delta) remains the fallback | Near-real-time sync; reduces poll load |
| 5.6 | Optional matter-archive/delete policy for cloud folders on matter deletion (move to `_archived/` rather than orphan) | M11 |

---

## 5. Data-model changes (summary)

| Change | Phase |
|---|---|
| Unique `(tenant_id, provider)` on `tenant_credentials`; `(tenant_id, user_id, provider)` on `user_oauth_tokens` | 0 |
| Token health columns on both credential tables: `health`, `last_refresh_at`, `last_refresh_error`, `scopes_version` | 1–2 |
| `integration_sync_runs` table | 2 |
| `matter_documents.storage_backend`, `storage_error`, `provider_object_id` (+ backfill) | 3 |
| `cloud_metadata_index`: `deleted_at` tombstone; delta cursors persisted per (tenant, provider, source) | 3 |
| `external_calendar_events` table | 4A |
| `email_matter_links` table | 4B |
| `tenant_settings`: timezone, `index_matter_emails`, directory-sync licensing toggle | 2/4 |

All via Alembic following the existing numbered-migration convention.

---

## 6. Sequencing, effort, risk

| Phase | Effort | Depends on | Risk notes |
|---|---|---|---|
| 0 Security | ~1 wk | — | PKCE/nonce changes touch live login — stage behind a flag and test both providers end-to-end; constraint migration must dedupe existing rows first |
| 1 Reliability core | ~1.5 wk | — (parallel with 0) | Client migration is mechanical but wide; migrate service-by-service with the typed-error mapping tests from 1.6 as the gate |
| 2 Observability | ~1 wk | 1 | Low risk, additive |
| 3 Storage correctness | ~1.5 wk | 1, 2 | Delta-sync cutover needs a one-time full re-index; naming unification must map existing folders (resolve by stored ID, never recreate) |
| 4 Feature verticals | ~4 wk | 1–3 | As per June plan: LLM classification cost per tenant; calendar dedupe against events created by old code |
| 5 Architecture | ~2 wk | 0–2 | App-permission consent requires customer AAD admin action — comms plan + dual-mode support during transition |

**Total: ~9–11 engineering weeks.** The fragility symptoms improve materially after Phases 0–2 (~3.5 weeks); Phases 3–4 make the layer correct and feature-complete; Phase 5 removes the remaining architectural risk.

---

## 7. Decisions needed

1. **Tenant auto-creation policy (0.8)** — domain verification vs. invite-only signup vs. accept current behavior for now.
2. **Tenant-wide auth model (5.1)** — commit to app permissions/service accounts, or keep delegated-admin with better health monitoring.
3. **Cloud delete semantics (3.1)** — hard-delete provider file vs. unlink-only default.
4. **Directory-sync licensing (2.7)** — default `license_active` for auto-created users.
5. **Matter deletion policy (5.6)** — orphan (current), archive folder, or delete.

---

## 8. Testing & rollout

- **Unit/integration:** httpx `MockTransport` suites for the shared clients and token vault covering 401/403/404/429/5xx/timeout, refresh rotation, concurrent refresh, delta-cursor resume, tombstoning. Matching-engine fixtures (multi-matter, thread continuity, matter-number regex).
- **E2E sandbox tenants:** one M365 dev tenant + one Google Workspace test domain: connect → create matter → upload → verify in drive → rename in drive → search; disconnect → verify revocation; expire refresh token → verify `reconnect_required` surfaces; create/update/delete task → verify calendar; receive email → verify auto-link.
- **Rollout:** Phases 0–1 ship as fixes (PKCE staged behind a flag); Phase 2 dashboards are the acceptance gate for everything after. Phases 3–4 per-tenant feature-flagged, pilot tenant first. Acceptance metrics: cloud-upload success rate, token-refresh failure rate + time-to-detection, index staleness (deleted-file hit rate), calendar sync success rate, % emails auto-linked.
