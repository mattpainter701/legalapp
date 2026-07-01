# Cloud Integrations Enhancement Plan (Microsoft 365 + Google Workspace)

**Status:** Superseded by [`integrations-remediation-plan.md`](integrations-remediation-plan.md) (2026-07-01)
**Date:** 2026-06-12
**Scope:** Matter file share mappings, calendar sync, email-to-matter linking, email access from chat RAG

> **Note (2026-07-01):** Of this plan, items 0.1/0.2 (scope parity + user-scope upgrades), the scheduled
> correspondence-capture job, and per-user task calendar push shipped. The health spine, calendar engine,
> email-links tables, RAG email work, and file polish did not. Those items are carried forward — re-sequenced
> behind newly identified security and reliability fixes — in `integrations-remediation-plan.md`.

---

## 1. Executive summary

Four user-facing symptoms were investigated:

1. Matter file share mappings are not working (documents don't land in OneDrive/Google Drive)
2. Calendar events are not syncing to Outlook/Google calendars
3. Emails are not linking to matters
4. RAG from chat is not accessing user emails

The investigation found these are **not four independent bugs**. They share three systemic root causes:

| Root cause | Affects |
|---|---|
| **A. OAuth scope defects** — per-user token exchange drops `Calendars.ReadWrite` (Microsoft) and never requests Drive/file-write scopes at user level for either provider | Calendar sync, file uploads, cloud search |
| **B. Silent-failure design** — every cloud call is wrapped in `except Exception: pass` / `return None`; uploads fall back to local disk, calendar pushes are fire-and-forget, chat cloud search swallows all errors. Nothing is surfaced to users or admins | All four symptoms |
| **C. Nothing runs automatically** — email→matter scanning has no scheduled trigger, calendar sync is a manual button (matter key dates only), task sync only fires on create (Google only), folder provisioning races with first upload | Calendar sync, email linking, file mappings |

The plan below fixes the root causes first (Phase 0), builds an observability spine so failures are visible (Phase 1), then completes each feature vertical (Phases 2–5).

---

## 2. Root-cause findings (verified, with file references)

### 2.1 Matter file share mappings

**Flow today:** matter creation fires `asyncio.create_task(_provision_cloud_folders(...))` (`backend/app/routers/matters.py:540-544`) which creates `claritylegal-records/{matter_slug}/{emails,documents,pleadings,correspondence,billing}` and stores folder IDs in `matters.cloud_folder` JSON (`backend/app/services/cloud_init.py:82-147`). Uploads route through `matter_file_store.store_matter_file()` (`backend/app/services/matter_file_store.py:32-101`): try preferred provider → other provider → local disk.

**Defects:**

1. **Provisioning race.** Folder creation is fire-and-forget; an upload right after matter creation sees `cloud_folder = null` and silently goes to local disk (`matters.py:540-544`, `matter_file_store.py:60-66`).
2. **Silent fallback.** `_try_store_onedrive` / `_try_store_google_drive` return `None` on *any* error — 401, 403, 404, timeout — and the endpoint never reports where the file actually landed (`matter_file_store.py:144-154, 207-217`). `MatterDocument.storage_path` doesn't distinguish cloud vs local.
3. **Stale folder IDs.** If a folder is renamed/deleted in the customer's drive, the cached ID 404s forever; there is no reconcile/repair mechanism.
4. **User-level scopes are read-only.** Microsoft user intent requests `Files.Read.All` (no write); Google user intent requests no Drive scope at all (`backend/app/routers/integrations.py:124, 315`). Only tenant-admin tokens can write files.
5. **No provider validation.** `primary_cloud_provider` can point at a provider with no active credential; upload tries it, fails, falls back silently.
6. The frontend "Cloud Drives" import tab is a stub (`frontend/src/components/FileUpload.jsx:100` — `TODO: Implement cloud file import API call`).

### 2.2 Calendar sync

**Flow today:** internal calendar aggregates tasks/key dates/renewals/estate deadlines (`backend/app/routers/calendar.py:24-160`). Outbound sync exists in `backend/app/services/calendar_sync.py` and `google_calendar.py`, but:

1. **CRITICAL scope bug (Microsoft):** the authorize URL for `intent=user` requests `offline_access Mail.Read Files.Read.All Calendars.ReadWrite` (`integrations.py:124`), but the **token exchange** requests `offline_access Mail.Read Files.Read.All` — *without* `Calendars.ReadWrite` (`integrations.py:166-168`). The stored user token cannot write calendar events; every `ms_create_event()` 403s. This alone breaks Outlook calendar sync for all per-user connections.
2. **Task sync is create-only, Google-only.** Task create fires a fire-and-forget Google push (`backend/app/routers/tasks.py:180-194`); task **update** and **delete** never sync (`tasks.py:220-276`), and Microsoft is never pushed at all.
3. **No scheduled sync job.** The scheduler runs 8+ agents (`backend/app/services/scheduler.py:392-476`) but none for calendar. The only trigger is the manual "Sync Calendar" button, which syncs matter key dates only (`backend/app/routers/email_agent.py:106-157`).
4. **No event ID mapping.** Created events aren't tracked (except a `clarity_task_id` extended property on Google), so there's no update/delete/dedupe story and no two-way sync foundation.
5. Hardcoded `America/New_York` timezone in `ms_create_event()` (`calendar_sync.py:46-50`).

### 2.3 Email-to-matter linking

**Flow today:** `email_agent.process_emails()` reads last-7-days mail via Graph/Gmail, classifies with LLM, then `_auto_log_and_task()` matches sender/recipients against `Contact.email` and creates `CommunicationLog` + `MatterNote` (+ deadline `Task`) (`backend/app/services/email_agent.py:15-162`).

**Defects:**

1. **Never triggered automatically.** `process_emails` is only reachable via manual `POST /api/email/scan` (`backend/app/routers/email_agent.py:25-75`). No scheduler job, no webhook. (The `cloud-sync` scheduler job at `scheduler.py:458-466` syncs *metadata* into `cloud_metadata_index` every 15 min, but never runs the matching/linking pipeline.)
2. **Weak matching.** Contact match uses `ILIKE %address%` (false positives on substring matches); no matter-number/subject-pattern matching; no thread continuity (replies in a linked thread aren't auto-linked).
3. **Single-matter linking.** `CommunicationLog.matter_id` gets only `matched_matter_ids[0]` (`email_agent.py:47`); multi-matter emails lose links.
4. **No UI.** There is no email browser over `cloud_metadata_index`, no "link this email to a matter" action, and no email tab on the matter page. CommunicationsPage requires pasting matter UUIDs to filter.
5. **7-day hard window, no delta sync, no webhooks** (`backend/app/services/cloud_sync.py:224`).

### 2.4 RAG access to user emails from chat

**Flow today:** chat calls `hybrid_rag_query()` (`backend/app/services/rag.py:265-398`) = pgvector chunks + optional live "cloud search" (Gmail/Outlook/Drive via `cloud_search.py`) chosen by an LLM retrieval planner. **Emails are never embedded/ingested** — by design, only metadata is stored locally; bodies are fetched live at query time.

**Why it appears broken:**

1. **Depends on per-user OAuth tokens** that most users don't have (only admins complete the connect flow), and user-level scope sets are incomplete (see 2.1.4). Token refresh returns `None` silently when refresh token is absent (`backend/app/services/token_vault.py:238-245`).
2. **Triple-gated and fully silent:** requires `_connected_providers()` to find a token, the retrieval planner LLM to set `should_search=true` and include `gmail`/`outlook_mail` in sources, and then the whole block is wrapped in `except Exception: pass` (`rag.py:301-388`). When any link in the chain fails, chat just answers without email context and no one knows why.
3. **Metadata index may be empty** if the `cloud-sync` job has been failing (token problems again) — keyword search against providers still works but matter-folder-scoped search doesn't.
4. Cloud search results are not user-permission-filtered when falling back to tenant-level admin tokens — a privacy issue to address (one user's chat could surface another user's mail metadata).

---

## 3. Target architecture

```
                       ┌─────────────────────────────────────────┐
                       │   Integration Health & Sync Spine        │
                       │  (sync_state, sync_runs, token health,   │
                       │   admin dashboard, user re-auth banners) │
                       └────────────────┬────────────────────────┘
          ┌─────────────┬───────────────┼────────────────┬──────────────┐
          ▼             ▼               ▼                ▼              ▼
   OAuth/Token     Files vertical   Calendar vertical  Email vertical  RAG vertical
   - scope parity  - provision      - outbound sync    - scheduled     - hardened live
   - scope audit     w/ retry +       engine (both       ingest +        cloud search
     on callback     status           providers)         matching       - per-matter
   - refresh       - upload        - event-mapping     - email_matter    email index
     telemetry       receipts         table              _links M:N      (opt-in)
   - re-consent    - reconcile     - reconcile job     - review queue  - user-scoped
     prompts         job           - webhooks (P2)     - webhooks (P2)   permissions
```

Key principles:

- **No silent failures.** Every cloud operation records an outcome (success/skip/fail + reason) in a sync-state table; UI surfaces it.
- **Every external write is tracked** with an external-ID mapping row so we can update, delete, dedupe, and reconcile.
- **Token health is a first-class concept** — scope sets are validated at callback time and monitored; users/admins are prompted to re-consent when scopes or refresh tokens are missing.
- **Background jobs do the work; buttons just force a run.**

---

## 4. Phased plan

### Phase 0 — Critical fixes (unblocks everything) — ~3–4 days

| # | Fix | Files |
|---|---|---|
| 0.1 | **Microsoft token-exchange scope parity**: use the same scope string in the token exchange as the authorize URL (extract one shared constant per intent). Fixes per-user Outlook calendar writes. | `integrations.py:121-168` |
| 0.2 | **Upgrade user-level scope sets**: Microsoft user → add `Files.ReadWrite.All` (or `Files.ReadWrite`), keep `Calendars.ReadWrite`; Google user → add `drive.file` (or `drive`) and decide on `gmail.readonly` vs `gmail.modify`. Bump a `scopes_version`; users with stale scopes get a re-consent banner. | `integrations.py:68-75, 124, 315` |
| 0.3 | **Fix provisioning race**: make `_provision_cloud_folders` awaited during matter creation with a short timeout, or have `store_matter_file()` lazily provision missing folders on first upload (preferred — idempotent `_ensure_*_folder` already exists). | `matters.py:540-544`, `matter_file_store.py`, `cloud_init.py` |
| 0.4 | **Record upload destination**: add `storage_backend` (`onedrive`/`google_drive`/`local`) + `storage_error` columns to `matter_documents`; return destination in the upload response so the UI can show "Saved to OneDrive ✓" or "Saved locally — cloud unavailable ⚠". | `models/matter_document.py`, `matter_file_store.py`, `matter_documents.py`, migration |
| 0.5 | **Differentiate cloud errors**: in `_try_store_*`, classify 401/403 (auth → flag credential unhealthy), 404 (stale folder → trigger re-provision and retry once), 5xx/timeout (retry with backoff, then fall back). Log with structured reason codes. | `matter_file_store.py:103-217`, `token_vault.py` |
| 0.6 | **Schedule the email scan**: add an `email-agent` scheduler job (e.g. every 30 min per tenant with an active mail-capable credential) that runs `process_emails` in auto-link mode (classification optional/cheap-tier to control LLM cost). | `scheduler.py`, `email_agent.py` |
| 0.7 | **Schedule calendar deadline sync**: add a `calendar-sync` job (hourly) running `sync_deadlines_to_calendar` + task event reconciliation per connected user. | `scheduler.py`, `calendar_sync.py` |

Deliverable: with 0.1–0.7 alone, all four reported symptoms materially improve.

### Phase 1 — Integration health & observability spine — ~1 week

1. **New tables**
   - `integration_sync_runs` (tenant_id, provider, job_type [files/calendar/mail/metadata], started_at, finished_at, status, items_ok, items_failed, error_summary).
   - Token health fields on `TenantCredential`/`UserOAuthToken`: `last_refresh_at`, `last_refresh_error`, `health` (healthy/expired/revoked/missing_scopes), `scopes_version`.
2. **Scope audit at callback**: compare granted `scope` string to required set; persist gap; mark credential `missing_scopes` rather than discovering at call time.
3. **Admin dashboard** (extend `IntegrationsPanel.jsx` / `CloudSearchAdmin.jsx`): per-provider health, last sync runs per job type, counts of documents on local vs cloud, failing matters, one-click "Re-provision folders" and "Run sync now".
4. **User-facing re-auth prompts**: banner + settings card when the user's own token is missing/expired/under-scoped ("Connect your Microsoft account to sync your calendar and search your email in chat").
5. **Stop swallowing exceptions** in `rag.py:336-388` cloud-search block: catch, record reason (no token / planner declined / provider error) into a debug field returned with chat sources, and log structured events. Failure stays non-fatal to chat, but becomes diagnosable.

### Phase 2 — Calendar sync engine — ~1.5 weeks

1. **`external_calendar_events` mapping table**: (tenant_id, user_id, provider, source_type [task/matter_key_date/renewal/estate_deadline], source_id, external_event_id, external_calendar_id, last_pushed_at, content_hash, status).
2. **Unified outbound sync service** replacing ad-hoc pushes:
   - create/update/delete handlers for tasks (both providers, not just Google), matter key dates, renewals, estate deadlines;
   - upsert by mapping row (replaces the Google extended-property dedupe hack);
   - per-user routing: events go to calendars of users assigned to the matter (assignment roles already exist);
   - tenant/user timezone setting replaces hardcoded `America/New_York`.
3. **Wire CRUD hooks**: task update/delete (`tasks.py:220-276`), matter key-date changes, renewal/estate deadline changes → enqueue sync; the hourly scheduler job reconciles drift (compare `content_hash`).
4. **Frontend**: last-synced timestamp + per-provider toggle on CalendarPage; per-user "sync my tasks to my calendar" preference.
5. *(P2, optional)* **Two-way sync**: Graph change-notification subscriptions + Google Calendar watch channels to pull external edits back. Explicitly out of MVP scope; the mapping table is designed to enable it.

### Phase 3 — Email-to-matter pipeline — ~2 weeks

1. **`email_matter_links` table** (M:N): (tenant_id, provider, message_id, thread/conversation_id, matter_id, link_source [auto_contact/auto_matter_ref/auto_thread/manual], confidence, linked_by_user_id, created_at). `CommunicationLog` keeps its single `matter_id` for back-compat; links become the source of truth.
2. **Matching engine v2** (`email_agent.py`):
   - exact-normalized email-address match (drop `ILIKE %…%` substring matching);
   - matter-reference detection in subject/body (matter number patterns, e.g. `ABC-123`, configurable regex per tenant);
   - thread continuity: if any message in the conversation is linked, link the rest;
   - link **all** matched matters, with confidence scores; below-threshold matches go to a review queue instead of auto-linking.
3. **Delta sync**: store and use Gmail `historyId` and Graph delta tokens in `cloud_metadata_index.sync_cursor` (column already exists) instead of the fixed 7-day window; widen initial backfill to a configurable window.
4. **Frontend**:
   - **Emails tab on MatterDetailPage**: linked emails (from `email_matter_links` joined to `cloud_metadata_index`), full body fetched live on open;
   - **Review queue**: "12 emails need filing" — suggested matter(s) with one-click confirm/relink;
   - replace UUID paste in CommunicationsPage filters with matter/contact pickers.
5. **Outbound linking**: emails sent via `POST /matters/{id}/email-client` record `external_ref` (message-id) and an `email_matter_links` row so replies thread-match.
6. *(P2)* **Webhooks**: Graph change notifications + Gmail Pub/Sub push for near-real-time ingestion; polling remains the fallback.

### Phase 4 — Email access from chat RAG — ~1.5 weeks

Two complementary tracks:

1. **Harden the existing live cloud search** (keeps the privacy-friendly "no email bodies at rest" default):
   - fix the token chain: with Phase 0 scopes + Phase 1 re-auth prompts, per-user tokens will actually exist;
   - make the retrieval planner deterministic for explicit asks ("search my email for…" → always include mail source, bypass the LLM gate);
   - per-user permission filter when only a tenant token exists: restrict mail search to the requesting user's mailbox (Graph `/users/{me}`), never tenant-wide, unless the user is an admin running a firm-wide search;
   - surface email sources in chat citations with provider/web links (structure already exists in `chat.py:686-727`);
   - expose "searched: Gmail (3 hits), Drive (2 hits)" / "email search skipped: account not connected" in the chat response metadata so users understand coverage.
2. **Opt-in matter email indexing** (makes linked emails first-class RAG citizens):
   - when an email is linked to a matter (Phase 3), embed its body into the existing `chunks` pipeline as a synthetic document (`source="email"`, matter-scoped, with `owner_user_id` for permission filtering);
   - bounded scope (only matter-linked emails, not whole mailboxes) keeps embedding cost and data-residency exposure small;
   - retrieval filter: email-sourced chunks only returned to users assigned to the matter (or per tenant privacy setting);
   - tenant-level setting `index_matter_emails` (default off) to respect the current architecture decision in `docs/ARCHITECTURE.md`.

### Phase 5 — File-share polish — ~1 week

1. **Folder reconcile job**: weekly per-tenant verification that `cloud_root_folder`, matter folders, and subfolder IDs still resolve; auto-repair (recreate + update JSON) and report in sync runs.
2. **Re-provision endpoint + UI**: admin button per matter ("Repair cloud folders"), plus bulk repair from the dashboard.
3. **Cloud file import**: implement the API behind the `FileUpload.jsx` Cloud Drives tab (browse matter folder / search drive, import file as `MatterDocument` referencing the cloud item rather than copying).
4. **Local→cloud backfill**: one-shot migration task that pushes documents stored locally (identifiable via the new `storage_backend` column) up to the correct matter folder once a healthy credential exists.

---

## 5. Data model changes (summary)

| Change | Type |
|---|---|
| `matter_documents.storage_backend`, `storage_error` | columns (Phase 0) |
| `integration_sync_runs` | new table (Phase 1) |
| token health columns on `tenant_credentials`, `user_oauth_tokens` (`health`, `last_refresh_error`, `scopes_version`) | columns (Phase 1) |
| `external_calendar_events` | new table (Phase 2) |
| `email_matter_links` | new table (Phase 3) |
| `chunks.source` extension for email-sourced chunks + `owner_user_id` | columns (Phase 4, behind setting) |
| `tenant_settings.index_matter_emails`, timezone | columns (Phases 2/4) |

All via Alembic migrations following the existing numbered-migration convention.

---

## 6. Sequencing, effort, risk

| Phase | Effort | Depends on | Risk notes |
|---|---|---|---|
| 0 Critical fixes | 3–4 days | — | Scope changes require users/admins to re-consent (Microsoft admin consent for `Files.ReadWrite.All` may need AAD admin approval). Plan comms/banner. |
| 1 Health spine | 1 week | 0 | Low risk, additive. |
| 2 Calendar engine | 1.5 weeks | 0, 1 | Dedupe against events created by the old code (match by `clarity_task_id` extended property where present). |
| 3 Email pipeline | 2 weeks | 0, 1 | LLM classification cost at scale — make classification optional per tenant; matching itself is non-LLM. |
| 4 RAG email | 1.5 weeks | 0, 1, 3 (for indexing track) | Privacy: per-user scoping must be enforced before enabling tenant-token mail search; indexing is opt-in. |
| 5 File polish | 1 week | 0, 1 | Backfill must dedupe against files already manually placed in drives. |

**Total: ~7–8 engineering weeks**, with the four reported symptoms substantially fixed after Phase 0 (under a week) and fully robust after Phases 2–4. Webhooks/two-way calendar sync are deliberately deferred (P2) — the mapping tables in Phases 2–3 are designed so they bolt on without rework.

## 7. Testing & rollout

- **Unit/integration:** mock Graph/Google APIs (httpx mock transport) for scope-failure paths (401/403/404), token refresh expiry, folder 404 → re-provision, matching engine fixtures (multi-matter, thread continuity, matter-number patterns).
- **E2E sandbox tenants:** one M365 dev tenant + one Google Workspace test domain exercised in CI-adjacent smoke runs: connect → create matter → upload → verify file in drive; create task → verify calendar event → update/delete → verify; send/receive email → verify auto-link.
- **Rollout:** Phase 0 ships behind nothing (bug fixes); re-consent banner drives scope upgrades. Phases 2–4 ship per-tenant feature-flagged (`tenant_settings`), enabled for a pilot tenant first. Dashboards from Phase 1 are the acceptance gate: cloud-upload success rate, calendar sync success rate, % emails auto-linked, chat email-search coverage.
