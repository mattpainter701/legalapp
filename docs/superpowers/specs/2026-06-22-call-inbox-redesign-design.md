# Call Intake Dashboard — "Call Inbox" Redesign

**Date:** 2026-06-22
**Status:** Approved design, pending implementation plan
**Goal:** Rework the receptionist intake dashboard (`IntakeDashboardPage.jsx`) into a functional, two-pane "Call Inbox": a live, auto-refreshing call feed on the left and a per-call work panel on the right. New calls (manual or webhook-imported) surface within ~15s with an in-page toast + chime. Framed source-agnostically so any tenant can enable a call source (Zoom Phone today, others later) and have it light up the same inbox.

## Context — what already exists

- `frontend/src/pages/IntakeDashboardPage.jsx` (~1340 lines, single file): a stacked page with `RecentCallersPanel`, `ZoomPhoneCallsPanel`, search, `History Matches`, `IntakeExportPanel`, `PartnerLogPanel`, the Call Capture form, an MVP-boundary card, and `RotationAdmin`. Panels load **once on mount** — no polling.
- Backend `GET /api/intake/dashboard/recent-callers` returns the last 10/20/50 inbound `CommunicationLog` rows (channel=`call`, direction=`inbound`) — this **already includes Zoom-imported calls**, enriched with lead/task/assignee. N+1 queries per row (lead + task + user-name lookups).
- `GET /api/intake/dashboard/zoom-phone/calls` is a filtered view of the same rows; `POST .../zoom-phone/sync` is an admin backfill.
- Post-call webhooks (`POST /api/integrations/zoom-phone/webhook[/{tenant_id}]`) write new `CommunicationLog` rows **immediately** on call completion (per-tenant URL + per-tenant secret + per-tenant `TenantCredential`). The DB updates instantly; the UI just doesn't reflect it without a manual refresh — this is the core gap.
- Zoom import already stores everything needed in `CommunicationLog.participants`: `caller_name`, `callee_name` (= answered-by), `phone`/`caller_number`/`callee_number`, `direction`, `result` (disposition: missed/answered), `duration_seconds`, `recording_url`, `transcript_url`, and `provider == "zoom_phone"`. Today's `RecentIntakeCaller` schema does **not** expose answered-by, result, duration, recording/transcript, or a source flag.
- Multi-tenant: all intake/Zoom endpoints are tenant-scoped with RLS; Zoom integration is per-tenant (tenant-owned OAuth app, `TenantCredential` provider `zoom_phone`, per-tenant webhook secret). `GET /api/integrations/zoom-phone/status` reports connection state.

## Design principles

1. **Source-agnostic.** Feed items carry a `source` string (`"manual"`, `"zoom_phone"`, future providers). Affordances (answered-by / recording / transcript chips) render off the **presence of data**, never off a hardcoded brand check. A future phone integration drops into the same feed with no feed changes.
2. **Conditional integration UI.** A tenant with no call integration sees a clean manual-logging inbox — no Sync button, no provider filter, no dangling affordances. Integration controls appear only when that tenant has the integration (driven off `zoom-phone/status` / sources actually present).
3. **Tenant isolation.** No global/shared state. Feed, polling, and alerts are scoped to the authenticated user's tenant (RLS-enforced server-side). The only client-persisted bit — the mute toggle — is namespaced by tenant id.
4. **Never interrupt data entry.** A background poll updates the feed only; it must not clobber the selected call or reset an in-progress capture form.

## Architecture

### A. Backend — additive fields on the recent-callers feed

**`backend/app/schemas/intake_dashboard.py` — `RecentIntakeCaller`:** add
- `answered_by: Optional[str]` (from `participants["callee_name"]`)
- `result: Optional[str]` — call disposition (missed/answered)
- `duration_seconds: Optional[int]`
- `source: str = "manual"` — `"zoom_phone"` when `external_ref` starts `zoom_phone:call:` or `participants["provider"] == "zoom_phone"`, else `"manual"`
- `recording_url: Optional[str]`, `transcript_url: Optional[str]`

**`backend/app/routers/intake_dashboard.py` — `recent_callers`:**
- Populate the new fields from `participants` in the mapper.
- Relax the limit guard from `{10, 20, 50}` to include **5** (`{5, 10, 20, 50}`); default stays a feed-friendly value (20).
- **Perf:** refactor the per-row N+1 enrichment (lead/task/user-name) into bulk maps, mirroring the pattern already in `export_call_records` (build `lead_by_contact_id`, `task_by_log_id` / `task_by_lead_id`, `users_by_id` in one pass). Polling every 15s makes the current N+1 hot; batched output must match the current per-row output (regression-tested).

No new endpoints. `zoom-phone/calls` stays for backfill; `zoom-phone/sync` stays (admin). The unified feed reads from `recent-callers`; the Zoom-only filter is client-side on `source`.

### B. Frontend — component split

Break the single page into focused units:

| Unit | Responsibility |
|-|-|
| `IntakeDashboardPage` | Orchestrator: owns selected-call state, integration status, wires panels |
| `CallFeed` | Left pane: list, source filter chips, Sync button (conditional), new-call detection |
| `CallFeedItem` | One row: caller, status badge, answered-by, duration, time, recording/transcript chips |
| `WorkPanel` | Right pane: caller-facts card + history matches + capture form (or empty/search state) |
| `CallFacts` | Caller-facts card (name, phone, status, answered-by, duration, recording ▶, transcript) |
| `useCallFeedPolling` | Visibility-aware 15s interval + new-call diff; returns callers + `newCallIds` |
| `useCallAlerts` | Toast queue + chime + per-tenant mute toggle |
| `RecordsTabs` | Tabbed wrapper for `IntakeExportPanel`, `PartnerLogPanel`, `RotationAdmin` |

Reuses existing `ResultCard`, `RotationAdmin`, `IntakeExportPanel`, `PartnerLogPanel`, and the Call Capture form (moved into `WorkPanel`). The UI build uses the **frontend-design** skill.

### C. Layout (Call Inbox)

- **Header:** "Reception Desk" + live status ("updates every 15s") + a 🔔 mute toggle.
- **Left pane — unified live feed** (~280–320px): items from `recent-callers`. Filter chips **All / [provider]** derived from `source` values present (only shown when >1 source exists, i.e. a tenant has an integration). Newest 5 visually emphasized; scroll loads ~20. Newly-arrived ids get a green highlight ring. `Sync` button shown only for admins of a tenant with the integration connected.
- **Right pane — work panel:** select a call → `CallFacts` → auto-run history search (existing `selectRecentCaller` → `runSearchFor`) → pre-filled capture/route form (`source_communication_id` linked). Nothing selected → search box + empty prompt (walk-ins / no-call callers use the same flow).
- **Records & Settings:** `RecordsTabs` below the two panes — Call records (export), Partner log, Rotation (admin only).

### D. Live updates

`useCallFeedPolling`:
- On mount fetch, then `setInterval(15_000)` **only while `document.visibilityState === "visible"`**; clear on hide, refetch on `visibilitychange` → visible.
- Track seen call ids. After each fetch, `newIds = fetched ids − previously-seen ids`. On mount, seed seen-set without alerting (no toast for the initial load).
- Return `{ callers, newCallIds, refresh }`. Selection is kept by id, so a refresh never drops the selected call or the form state.

### E. Alert (toast + sound)

`useCallAlerts`:
- For each id in `newCallIds`, enqueue a toast: "New call — {caller} ({status})" with a **View** action that selects that call; auto-dismiss ~6s; stack multiples.
- Play a short chime per new-call batch unless muted. Mute toggle persisted at `localStorage["intake.mute." + tenantId]`.
- **Autoplay constraint:** browsers block audio before a user gesture. Until the first page interaction, show a subtle "click to enable sound" hint; after any click, audio is unlocked. Toasts always work regardless.

## Data flow

```
Call source (manual entry │ Zoom webhook /webhook/{tenant_id})
        └─▶ CommunicationLog (tenant-scoped, RLS)
                    │
useCallFeedPolling ─15s, tab-visible─▶ GET /recent-callers (source, answered_by, result, duration, recording/transcript)
                    │                         │
        new ids diff ─▶ useCallAlerts (toast + chime)
                    │
        select call ─▶ CallFacts + auto history search ─▶ pre-filled capture form ─▶ POST /calls
        (selection kept by id across refreshes — never clobbered)
```

## Error handling

- Feed fetch failure on a poll: keep the last good feed, no toast, silent retry next interval (don't blank the desk on a transient error). Initial-load failure shows the existing empty/error state.
- No integration connected: feed still renders manual calls; provider filter + Sync hidden; no error surfaced.
- Audio blocked (pre-gesture): toast still fires; show the enable-sound hint, no console error.
- A poll arriving mid-form-edit updates only the feed list; selected call + form untouched.

## Testing

**Backend**
- `recent_callers` exposes `answered_by`, `result`, `duration_seconds`, `source`, `recording_url`, `transcript_url`; Zoom-imported row → `source == "zoom_phone"` with answered-by/recording populated; manual row → `source == "manual"`.
- `limit=5` accepted; invalid limit still 422.
- Batched enrichment output equals the current per-row output for lead/task/assignee (regression).
- RLS: a tenant only sees its own callers (unchanged, guarded).

**Frontend** (manual + build)
- `npm run build` clean.
- A newly-arrived call fires exactly one toast + one chime; no alert on initial load.
- Polling pauses when the tab is hidden, resumes + refetches on focus.
- A poll during form entry does not reset the form or change the selected call.
- All / provider filter works; provider filter + Sync hidden for a manual-only tenant.
- Mute toggle persists per tenant.

## Out of scope (explicit)

- True realtime push (WebSocket/SSE) — polling at 15s is the agreed mechanism.
- OS/browser notifications (Web Notifications API) — in-page toast + sound only.
- Live ringing/in-progress call events — only post-call records, per the existing webhook decision.
- New call-source integrations beyond Zoom Phone — the `source` field makes them additive later.

## Build order

1. Backend: add fields to `RecentIntakeCaller` + `recent_callers` mapper; relax limit; batch the enrichment (regression test).
2. Frontend hooks: `useCallFeedPolling` (visibility-aware + new-call diff), `useCallAlerts` (toast + chime + per-tenant mute).
3. Component split: `CallFeed` / `CallFeedItem` / `CallFacts` / `WorkPanel` / `RecordsTabs`; move the capture form into `WorkPanel`.
4. Wire the two-pane layout in `IntakeDashboardPage`; conditional integration UI off `zoom-phone/status` + sources present.
5. Tests + `npm run build`; update `TASKS.md` / `CHANGELOG.md`.
