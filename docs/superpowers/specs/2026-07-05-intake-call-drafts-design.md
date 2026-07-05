# Intake Call Drafts + Action Receipts — Design Spec

**Date:** 2026-07-05
**Status:** Approved (approach A of three; see brainstorm history)
**Primary users:** Intake / front desk
**Pains addressed:** (1) in-progress call capture lost between calls or on
navigation/refresh; (2) unclear feedback after actions (assign, save, sync).

## Goals

- A capture-in-progress ("draft") is never lost: survives refresh, navigation,
  tab close, backend outage, and machine switch.
- Front desk can juggle multiple simultaneous calls without losing state.
- Every mutation produces explicit, persistent, per-call feedback; failures
  are never silent and are retryable in one click.
- Ship reusable feedback primitives (toast, async button) that seed a later
  app-wide consistency pass.

## Non-goals (explicit)

- No re-layout of the intake dashboard (search, history, feed, rotation
  admin, exports, partner log stay where they are). The "Live Call mode vs
  Admin mode" split is a separate follow-up project.
- No changes to other pages beyond introducing the shared `ToastProvider`.
- No draft merging/conflict resolution beyond last-write-wins.
- No supervisor UI for viewing others' drafts (the backend table enables it
  later; no frontend for it now).

## 1. Draft persistence

### Data model

```
CallDraft {
  draft_id: uuid (client-generated)
  form: { caller_name, phone, practice_area, notes, ...all Call Capture fields }
  linked_history_contact_id?: uuid
  receipts: Receipt[]            // see §3
  created_at, updated_at: iso timestamps
}
```

### Two-tier persistence

1. **localStorage (source of immediacy).** Key `intake.drafts.<draft_id>`,
   written on a ~300ms debounce per keystroke. An index key
   `intake.drafts.index` lists live draft ids. Works with zero backend
   dependency.
2. **Backend (source of durability).** `intake_call_drafts` table:
   `(id uuid PK, tenant_id uuid, created_by uuid, payload jsonb,
   created_at, updated_at)` with RLS policy matching existing intake tables
   (hardened `current_setting(..., true)` form). Endpoints:
   - `GET /api/intake/drafts` — current user's drafts
   - `PUT /api/intake/drafts/{draft_id}` — upsert (client-generated id)
   - `DELETE /api/intake/drafts/{draft_id}`
   Autosave on ~5s debounce + on blur/card-switch. Autosave failure never
   blocks typing; card shows a subtle "local only" indicator until a later
   save succeeds.

### Lifecycle

- **Create:** "+ New call" chip, `Ctrl+Shift+N`, or "Start capture" on an
  incoming-call toast (pre-filled with phone / Zoom caller-ID; auto-links a
  matching contact).
- **Convert:** submitting Call Capture posts the real call record (existing
  endpoint), then deletes the draft (both tiers). Deletion failure after a
  successful submit is logged, non-blocking, and cleaned up on next load.
- **Discard:** one confirm dialog, deletes both tiers.
- **Stale:** drafts older than 24h render under a "Yesterday & older"
  divider. Never auto-deleted.
- **Load:** on page mount, merge localStorage + backend lists; for the same
  draft_id, newest `updated_at` wins (last-write-wins, no merging).

## 2. Call-card UI

- **Tab strip** directly above the Call Capture form: one chip per draft
  showing caller name → phone → "Unnamed call" (first non-empty), elapsed
  time badge, unsaved-dot. `+ New call` chip at the end. Clicking swaps the
  form contents; the form itself does not move.
- **Keyboard:** `Ctrl+Shift+N` new draft; `Alt+1..9` switch to Nth card
  (`Ctrl+1..9` is reserved by browsers for tab switching).
- **History integration:** when the active draft has a linked contact, the
  History Matches panel scopes to that contact automatically.
- **Visual language:** existing tokens only — `border-brand-line` chips,
  active chip gets the sidebar-style `brand-accent` left bar, no new colors
  or fonts.

## 3. Action receipts + shared toast primitive

### Receipts

```
Receipt { id, label, status: 'ok' | 'failed' | 'pending', at: timestamp,
          retry?: { method, url, payload } }
```

- Appended to the owning draft on every mutation: save, assign, task
  creation, convert.
- Rendered as one compact line under the form (latest 3, expandable).
- Failure receipts render as red chips with **Retry** — one click re-sends
  the stored payload.
- Persist with the draft (both tiers), so feedback history survives refresh.

### Async-aware buttons

Mutating buttons: idle → spinner → ✓ (800ms) → idle. Implemented as a small
`AsyncButton` component reused across the intake page.

### Toasts

- New shared primitive: `<ToastProvider>` + `useToast()` in
  `frontend/src/components/toast/` (~100 lines, no external library).
- Used for background events (Zoom sync complete, new incoming call) and
  failure surfacing. Replaces intake-page-local `message`/`status` useState
  patterns. Other pages adopt opportunistically later (out of scope here).

### Failure rules

- Failures are never silent: red receipt + toast, always.
- Transient failures (network error, 5xx) get exactly one automatic retry
  before surfacing.

## 4. Error handling

- localStorage writes wrapped in try/catch (quota, private mode): degrade to
  in-memory drafts + one-time warning toast.
- Backend autosave failures: non-blocking, "local only" indicator, retried on
  next debounce tick.
- Draft endpoints follow the post-commit tenant-context rules documented in
  `docs/backend-500-review-2026-07-05.md` (no post-commit DB work without
  re-bind; prefer returning the in-memory object).

## 5. Testing

- **Backend:** draft CRUD, RLS tenant isolation, upsert idempotency —
  mirroring `backend/tests/test_intake_dashboard.py` patterns.
- **Frontend:** draft survives simulated unmount/remount (localStorage
  round-trip); receipt appended on mocked success and failure; retry re-sends
  stored payload; toast renders on failure.
- **Manual smoke:** start capture from an incoming-call toast, switch cards
  mid-typing, refresh the page, submit — no data loss, receipts intact.

## 6. Component boundaries

| Unit | Purpose | Depends on |
|-|-|-|
| `useCallDrafts()` hook | draft CRUD, two-tier persistence, LWW merge | localStorage, drafts API |
| `DraftTabStrip` | chip strip, keyboard switching | `useCallDrafts` |
| `ReceiptTrail` | render + retry receipts | draft object, api client |
| `AsyncButton` | mutation button states | none (generic) |
| `ToastProvider`/`useToast` | app-level toasts | none (generic) |
| `intake_call_drafts` router | draft endpoints | RLS conventions |

Call Capture form itself is refactored only as far as binding its fields to
the active draft — no field-level redesign.
