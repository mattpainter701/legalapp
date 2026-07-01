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
| Phase 1 health spine (`integration_sync_runs`, token health columns, scope audit) | ❌ Not done |
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
| S8 | P1 | `tenant_credentials` lacks `(tenant_id, provider)` unique constraint while reads use `scalar_one_or_none()` (→ `MultipleResultsFound` risk); `user_oauth_tokens` unique omits `tenant_id` | `models/tenant_credential.py`, `models/user_oauth_token.py:12-16` |
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

| # | Fix | Files |
|---|---|---|
| 0.1 | **Verify Microsoft id_tokens**: JWKS signature + `aud`/`iss`/`exp` validation mirroring `_verify_google_id_token`; add `nonce` to both providers' login flows | `auth.py:409-621, 630-680` |
| 0.2 | **Remove the `DEV_MODE` verification bypass** (or gate it on an explicit localhost allowlist + startup warning); never expose reset tokens outside tests | `auth.py:635-639, 1186`, `config.py:175` |
| 0.3 | **PKCE (S256)** on both OAuth surfaces; store `code_verifier` in state meta; verify `state.provider` matches the callback route (pattern already exists in the Zoom Phone callback) | `auth.py`, `integrations.py:72-89, 254, 441` |
| 0.4 | **Revoke at provider on disconnect**: Google `oauth2.googleapis.com/revoke`, Microsoft best-effort; then delete rows | `integrations.py:1298-1357` |
| 0.5 | **DB constraints migration**: unique `(tenant_id, provider)` on `tenant_credentials` (dedupe existing rows first, keep newest active); extend `user_oauth_tokens` unique to include `tenant_id` | `models/tenant_credential.py`, `models/user_oauth_token.py`, new Alembic migration |
| 0.6 | **Boot/config validation**: warn at startup on blank provider credentials; reuse `_oauth_configured` in `integrations.py` connect endpoints (return 501 like login does) | `config.py`, `integrations.py:212, 402` |
| 0.7 | **State hygiene**: GC for `_fallback_states`/`_fallback_state_data` (port `_gc_fallback_dicts` from `auth.py`); document Redis as required for multi-worker deployments | `integrations.py:48-49` |
| 0.8 | **Harden tenant auto-creation**: require email-domain verification or an explicit signup allowlist before auto-admin of a new domain tenant (decision needed — see §7) | `auth.py:314-392` |
| 0.9 | Escape OneDrive search query (mirror the SharePoint `''` escaping) | `cloud_search.py:985` |

### Phase 1 — Reliability core (~1.5 weeks)

| # | Fix | Files |
|---|---|---|
| 1.1 | **Shared provider HTTP clients**: new `services/graph_client.py` + `services/google_client.py` — single base URLs, default timeout (e.g. 30s), 429/`Retry-After` honoring + bounded transient retry (borrow `teams._graph_request` pattern), typed exceptions (`ProviderAuthError`, `ProviderThrottled`, `ProviderNotFound`, `ProviderError`) | new files; migrate `calendar_sync.py`, `google/microsoft_mail.py`, `google/microsoft_calendar.py`, `user_sync.py`, `cloud_sync.py`, `cloud_init.py`, `cloud_search.py`, `document_sync.py`, `matter_file_store.py` |
| 1.2 | **Consolidate token refresh** into one code path (single `_refresh(provider, row)` used by tenant and user grains); `SELECT … FOR UPDATE` (or `with_for_update(skip_locked)` + short wait/re-read) around refresh to kill the race; always persist rotated refresh tokens | `token_vault.py` |
| 1.3 | **Persist token health**: on refresh failure record `last_refresh_error`/`last_refresh_at`; on `invalid_grant` flip `is_active=False` and set `health='revoked'`; add retry-with-backoff for transient token-endpoint failures | `token_vault.py`, models + migration (shared with 2.1) |
| 1.4 | **Truthful status**: `/api/integrations/status` and `get_calendar_providers` surface `health`/`reconnect_required` instead of row-exists=connected | `integrations.py:1230-1290`, `auth.py:1426-1447` |
| 1.5 | **Consistent error contract**: services raise the typed exceptions from 1.1; routers map `ProviderAuthError`→424 (pattern exists in `routers/calendar.py`), `ProviderThrottled`→503+Retry-After; retire the RuntimeError/None/bool mix | routers + services |
| 1.6 | **Token vault test suite** (first ever): httpx `MockTransport` — encrypt/decrypt round-trip, `_is_fresh` skew, refresh success/rotation persistence, `invalid_grant` → health flip, 5xx retry, concurrent-refresh single-flight | `backend/tests/test_token_vault.py` (new) |

### Phase 2 — Observability spine (~1 week)

| # | Fix | Files |
|---|---|---|
| 2.1 | **`integration_sync_runs` table** (tenant_id, provider, job_type, started/finished, status, items_ok, items_failed, error_summary) + token-health columns (`health`, `last_refresh_error`, `scopes_version`) on both credential tables | new model + migration |
| 2.2 | **Scope audit at callback**: compare granted `scope` to the required set; persist the gap; mark `missing_scopes` instead of discovering at call time | `integrations.py` callbacks |
| 2.3 | **Wire `error_tracker`/`ErrorLog`** into scheduler jobs and integration services (capture in `_guarded` and per-tenant failure branches); fix `cloud_search_status` to report real errors | `scheduler.py:216, 387`, `routers/cloud_admin.py:175-195` |
| 2.4 | **Admin dashboard**: per-provider health cards (token health, last sync runs per job type, docs local-vs-cloud counts, failing matters) in `IntegrationsPanel.jsx`/`CloudSearchAdmin.jsx`; one-click "Run sync now" / "Repair" |
| 2.5 | **User re-auth prompts**: banner + settings card when the user's own token is missing/expired/under-scoped | frontend |
| 2.6 | **De-silence chat cloud search**: record skip/fail reason (no token / planner declined / provider error) into chat response metadata; failure stays non-fatal but becomes diagnosable | `rag.py:301-388` |
| 2.7 | Make directory-sync licensing explicit: config or admin toggle for auto-`license_active` on synced users | `services/user_sync.py` |

### Phase 3 — Storage correctness (~1.5 weeks)

| # | Fix | Files |
|---|---|---|
| 3.1 | **Implement cloud document delete**: DELETE removes the provider item by object ID (see 3.5) with `ProviderNotFound` tolerated, then the DB row; offer "unlink only" variant. Replaces the 501 stub | `routers/matter_documents.py:198-205`, `matter_file_store.py` |
| 3.2 | **Delta sync + pruning**: adopt Graph `/delta` and Drive `changes.list`/`startPageToken` cursors (persist in the existing `sync_cursor` column); tombstone/prune `cloud_metadata_index` rows on deletion/move; full re-list only as fallback/backfill | `cloud_sync.py`, `models/cloud_metadata.py` |
| 3.3 | **Index matter subfolders**: recursive (or matter-folder-scoped) enumeration in the scheduled sync; add pagination loops to `document_sync.py` | `cloud_sync.py:575`, `document_sync.py` |
| 3.4 | **Unify folder naming**: one `matter_folder_name(matter)` helper used by background provisioning, provision/sync endpoints, and all three providers (incl. SharePoint) | `cloud_init.py`, `matters.py:251-255, 2298, 2578` |
| 3.5 | **Real storage columns**: `storage_backend`, `storage_error`, `provider_object_id` on `matter_documents` (+ backfill migration from URL sniffing); upload response returns destination ("Saved to OneDrive ✓" / "Saved locally ⚠") | `models/matter_document.py`, `matter_file_store.py`, migration |
| 3.6 | **Provisioning robustness**: queue provisioning as a tracked retryable job (or lazy-provision on first upload via the existing idempotent `_ensure_*` helpers); `_status='partial'` with per-provider detail when some providers fail; Google 409/duplicate recovery; upload dedupe pre-check for OneDrive/SharePoint (replace blind `conflictBehavior=rename`) | `matters.py:685`, `cloud_init.py`, `matter_file_store.py` |
| 3.7 | **Folder reconcile job + repair endpoint**: weekly verification that root/matter/subfolder IDs still resolve; auto-repair + report via `integration_sync_runs`; per-matter "Repair cloud folders" button | new scheduler agent, `matters.py`, frontend |
| 3.8 | **Local→cloud backfill** task: push locally-stored docs (now identifiable via 3.5) to the correct matter folder once a healthy credential exists; dedupe against existing files | script/scheduler task |
| 3.9 | Scope the per-matter "Sync folder" action to that matter's folders instead of tenant-wide `sync_all` | `matters.py:2593`, `cloud_sync.py` |
| 3.10 | Merge/retire duplicate listing implementations: `document_sync.py` browse/ingest moves onto the shared clients + `cloud_sync` index | `document_sync.py` |

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
