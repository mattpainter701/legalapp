# Call Inbox Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the receptionist intake dashboard into a two-pane "Call Inbox" — a live, auto-refreshing (15s) call feed on the left and a per-call work panel on the right — with an in-page toast + chime when a new call (manual or webhook-imported) lands, framed source-agnostically so any tenant's call integration lights up the same feed.

**Architecture:** Backend surfaces already-stored call facts (answered-by, result, duration, recording/transcript, source) on the existing `recent-callers` feed and batches its enrichment queries. Frontend splits the 1340-line page into focused components plus two hooks: `useCallFeedPolling` (visibility-aware interval + new-call diff) and `useCallAlerts` (toast + WebAudio chime + per-tenant mute).

**Tech Stack:** FastAPI, SQLAlchemy async + Postgres RLS, pytest-asyncio (backend); React 18 + Vite + Tailwind + lucide-react (frontend — no JS test runner, gate is `npm run build` + manual checks).

**Spec:** `docs/superpowers/specs/2026-06-22-call-inbox-redesign-design.md`

**Conventions:** Windows `py` launcher. Backend tests run from `backend/` against the demo Postgres (container `legalapp-demo-postgres`, host port 15432) with the env prefix below. Commit messages imperative, **no `Co-Authored-By`**. Work on branch `feat/call-inbox-redesign` (already created).

**Backend test env prefix** (prepend to every `py -m pytest` command):

```bash
export TEST_DATABASE_URL="postgresql+asyncpg://test:test@localhost:15432/legalapp_test" \
  DATABASE_URL="$TEST_DATABASE_URL" \
  SECRET_KEY="test-secret-key-for-local-pytest-runs-only-1234567890" \
  PLATFORM_SECRET_KEY="test-platform-key" \
  TOKEN_ENCRYPTION_KEY="$(py -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

---

## File Structure

| File | Responsibility | Action |
|-|-|-|
| `backend/app/schemas/intake_dashboard.py` | Add 6 fields to `RecentIntakeCaller` | Modify |
| `backend/app/routers/intake_dashboard.py` | Populate fields in mapper; relax limit; batch enrichment | Modify |
| `backend/tests/test_intake_dashboard.py` | New-field + limit=5 + batching regression tests | Modify |
| `frontend/src/hooks/useCallFeedPolling.js` | Visibility-aware 15s poll + new-call id diff | Create |
| `frontend/src/hooks/useCallAlerts.js` | Toast queue + WebAudio chime + per-tenant mute | Create |
| `frontend/src/components/intake/CallFeedItem.jsx` | One feed row (badge, answered-by, duration, chips) | Create |
| `frontend/src/components/intake/CallFeed.jsx` | Left pane: list + source filter + conditional Sync | Create |
| `frontend/src/components/intake/CallFacts.jsx` | Caller-facts card | Create |
| `frontend/src/components/intake/NewCallToasts.jsx` | Toast stack renderer | Create |
| `frontend/src/components/intake/RecordsTabs.jsx` | Tabbed Export / Partner log / Rotation | Create |
| `frontend/src/pages/IntakeDashboardPage.jsx` | Orchestrate two-pane layout; integration gating | Modify (large) |

`WorkPanel` lives inline in the page (it owns the capture-form state already there). The existing `ResultCard`, `RotationAdmin`, `IntakeExportPanel`, `PartnerLogPanel` move out to `RecordsTabs` / stay imported.

---

## Phase 1 — Backend feed fields

### Task 1: Surface call facts + allow limit=5

**Files:**
- Modify: `backend/app/schemas/intake_dashboard.py` (`RecentIntakeCaller`)
- Modify: `backend/app/routers/intake_dashboard.py` (`recent_callers`)
- Test: `backend/tests/test_intake_dashboard.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_intake_dashboard.py` (imports `uuid`, `datetime`, `timezone`, `CommunicationLog`, `User`, `Contact` already present in the file):

```python
@pytest.mark.asyncio
async def test_recent_callers_exposes_source_and_call_facts(
    client, db_session, test_tenant, test_user
):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Zoom Phone inbound call: Zed Caller",
                summary="Zoom call",
                external_ref="zoom_phone:call:abc123",
                participants={
                    "caller_name": "Zed Caller",
                    "phone": "701-555-7777",
                    "callee_name": "Front Desk",
                    "result": "answered",
                    "duration_seconds": 142,
                    "recording_url": "https://zoom.example/rec",
                    "transcript_url": "https://zoom.example/txt",
                    "provider": "zoom_phone",
                },
                occurred_at=now,
            ),
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="logged",
                subject="Inbound call: Manny Manual",
                summary="Walk-in style",
                participants={"caller_name": "Manny Manual", "phone": "701-555-0000"},
                created_by_user_id=test_user.id,
                occurred_at=now - timedelta(minutes=3),
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 5}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 5
    by_name = {c["caller_name"]: c for c in data["callers"]}

    zed = by_name["Zed Caller"]
    assert zed["source"] == "zoom_phone"
    assert zed["answered_by"] == "Front Desk"
    assert zed["result"] == "answered"
    assert zed["duration_seconds"] == 142
    assert zed["recording_url"] == "https://zoom.example/rec"
    assert zed["transcript_url"] == "https://zoom.example/txt"

    manny = by_name["Manny Manual"]
    assert manny["source"] == "manual"
    assert manny["answered_by"] is None
    assert manny["recording_url"] is None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && <env prefix> py -m pytest tests/test_intake_dashboard.py::test_recent_callers_exposes_source_and_call_facts -q
```
Expected: FAIL — `limit=5` returns 422 ("Limit must be 10, 20, or 50") and/or `KeyError: 'source'`.

- [ ] **Step 3: Add the schema fields**

In `backend/app/schemas/intake_dashboard.py`, add to `RecentIntakeCaller` (after `occurred_at`):

```python
    occurred_at: datetime
    source: str = "manual"
    answered_by: Optional[str] = None
    result: Optional[str] = None
    duration_seconds: Optional[int] = None
    recording_url: Optional[str] = None
    transcript_url: Optional[str] = None
```

- [ ] **Step 4: Populate in the mapper + relax the limit**

In `backend/app/routers/intake_dashboard.py`, add a source helper near `_log_participant`:

```python
def _log_source(log: CommunicationLog) -> str:
    participants = log.participants or {}
    if participants.get("provider") == "zoom_phone" or (
        log.external_ref or ""
    ).startswith("zoom_phone:call:"):
        return "zoom_phone"
    return "manual"


def _log_int(log: CommunicationLog, key: str) -> int | None:
    value = (log.participants or {}).get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
```

In `recent_callers`, change the limit guard:

```python
    if limit not in {5, 10, 20, 50}:
        raise HTTPException(status_code=422, detail="Limit must be 5, 10, 20, or 50")
```

And add the new fields to each `RecentIntakeCaller(...)` built in the loop (after `occurred_at=log.occurred_at,`):

```python
                occurred_at=log.occurred_at,
                source=_log_source(log),
                answered_by=_log_participant(log, "callee_name"),
                result=_log_participant(log, "result"),
                duration_seconds=_log_int(log, "duration_seconds"),
                recording_url=_log_participant(log, "recording_url"),
                transcript_url=_log_participant(log, "transcript_url"),
```

- [ ] **Step 5: Run it to verify it passes**

```bash
cd backend && <env prefix> py -m pytest tests/test_intake_dashboard.py::test_recent_callers_exposes_source_and_call_facts -q
```
Expected: PASS.

- [ ] **Step 6: Run the full intake suite (no regressions)**

```bash
cd backend && <env prefix> py -m pytest tests/test_intake_dashboard.py -q
```
Expected: PASS (existing recent-callers test still green — new fields are additive).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/intake_dashboard.py backend/app/routers/intake_dashboard.py backend/tests/test_intake_dashboard.py
git commit -m "feat: surface call source and facts on recent-callers feed"
```

### Task 2: Batch the recent-callers enrichment

`recent_callers` runs per-row lead/task/user-name queries (N+1). Polling every 15s makes that hot. Replace the per-row loop with bulk maps, reusing the exact pattern in `export_call_records` (same file). Output must be unchanged.

**Files:**
- Modify: `backend/app/routers/intake_dashboard.py` (`recent_callers`)
- Test: `backend/tests/test_intake_dashboard.py` (existing tests are the regression guard)

- [ ] **Step 1: Add a regression test pinning enrichment under batching**

Append to `backend/tests/test_intake_dashboard.py`:

```python
@pytest.mark.asyncio
async def test_recent_callers_batched_enrichment_matches(
    client, db_session, test_tenant, test_user
):
    partner = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id, email="bq@f.com",
        full_name="Batch Partner", role="user", is_active=True,
    )
    contact = Contact(
        tenant_id=test_tenant.id, first_name="Bea", last_name="Quary",
        phone="701-555-3333", created_by_user_id=test_user.id,
    )
    db_session.add_all([partner, contact])
    await db_session.flush()
    lead = Lead(
        tenant_id=test_tenant.id, contact_id=contact.id, status="qualified",
        practice_area="divorce", assigned_to_user_id=partner.id,
        created_by_user_id=test_user.id,
    )
    db_session.add(lead)
    await db_session.flush()
    log = CommunicationLog(
        tenant_id=test_tenant.id, direction="inbound", channel="call",
        status="logged", subject="Inbound call: Bea Quary", summary="Batched",
        participants={"caller_name": "Bea Quary", "phone": "701-555-3333"},
        contact_id=contact.id, created_by_user_id=test_user.id,
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(log)
    db_session.add(
        Task(
            tenant_id=test_tenant.id, title="Urgent intake follow-up: Bea Quary",
            description="x", task_type="follow_up", status="pending",
            priority="urgent", due_date=date.today(), contact_id=contact.id,
            assigned_to_user_id=partner.id, created_by_user_id=test_user.id,
            source="intake_dashboard",
            external_ref=f"intake-dashboard:lead:{lead.id}:follow-up",
        )
    )
    await db_session.commit()

    resp = await client.get("/api/intake/dashboard/recent-callers", params={"limit": 10})
    assert resp.status_code == 200
    caller = resp.json()["callers"][0]
    assert caller["caller_name"] == "Bea Quary"
    assert caller["lead_id"] == str(lead.id)
    assert caller["lead_status"] == "qualified"
    assert caller["assigned_to_name"] == "Batch Partner"
    assert caller["task_status"] == "pending"
    assert caller["created_by_name"] == test_user.full_name or caller["created_by_name"] == test_user.email
```

- [ ] **Step 2: Run it (passes on current N+1 code — it's the guard)**

```bash
cd backend && <env prefix> py -m pytest tests/test_intake_dashboard.py::test_recent_callers_batched_enrichment_matches -q
```
Expected: PASS (current code already produces this; the test pins behavior before refactor).

- [ ] **Step 3: Refactor `recent_callers` to batch lookups**

Replace the body of `recent_callers` after the `rows = (... ).all()` fetch with bulk maps. Build, in order: `lead_by_contact_id` (one query over all `contact_id`s, newest lead per contact), `task_by_log_id` (query `external_ref IN ["intake-dashboard:call:{log.id}:general-task"...]`), `task_by_lead_id` (query `external_ref IN ["intake-dashboard:lead:{lead.id}:follow-up"...]`), and `users_by_id` (one query over all `created_by_user_id` + assignee ids). Then build each `RecentIntakeCaller` from the maps instead of calling `_recent_lead_for_log` / `_assignment_task_for_log` / `_user_name` per row. Mirror the exact map-building code in `export_call_records` (same file, lines ~780–891) — `assigned_to_user_id` precedence is task assignee → lead assignee, and `created_by_name` comes from the joined `creator` row. Keep the explicit `tenant_id` filters (RLS is off in tests).

- [ ] **Step 4: Run the regression + full suite**

```bash
cd backend && <env prefix> py -m pytest tests/test_intake_dashboard.py -q
```
Expected: PASS (both the new batching test and the pre-existing `test_recent_callers_returns_recent_dashboard_calls_tenant_scoped`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/intake_dashboard.py backend/tests/test_intake_dashboard.py
git commit -m "perf: batch recent-callers enrichment queries"
```

---

## Phase 2 — Frontend hooks

> No JS test runner in this repo. Each frontend task gates on `cd frontend && npm run build` (clean) plus the stated manual check. Provide complete code.

### Task 3: `useCallFeedPolling` hook

**Files:**
- Create: `frontend/src/hooks/useCallFeedPolling.js`

- [ ] **Step 1: Write the hook**

```js
import { useCallback, useEffect, useRef, useState } from 'react'
import { getRecentIntakeDashboardCallers } from '../api'

const POLL_MS = 15000

// Visibility-aware poll of the recent-callers feed. Returns the current callers,
// the ids that are new since the last successful fetch (empty on first load),
// loading state, and a manual refresh(). Never throws on a failed poll — keeps
// the last good feed so the desk never blanks on a transient error.
export function useCallFeedPolling(limit = 20) {
  const [callers, setCallers] = useState([])
  const [loading, setLoading] = useState(true)
  const [newCallIds, setNewCallIds] = useState([])
  const seenRef = useRef(null) // null until first successful load (so no alert on mount)
  const timerRef = useRef(null)

  const fetchOnce = useCallback(async () => {
    try {
      const data = await getRecentIntakeDashboardCallers({ limit })
      const next = data.callers || []
      const ids = next.map((c) => c.id)
      if (seenRef.current === null) {
        setNewCallIds([])
      } else {
        const fresh = ids.filter((id) => !seenRef.current.has(id))
        setNewCallIds(fresh)
      }
      seenRef.current = new Set(ids)
      setCallers(next)
    } catch {
      // keep last good feed; no alert
      setNewCallIds([])
    } finally {
      setLoading(false)
    }
  }, [limit])

  const start = useCallback(() => {
    if (timerRef.current) return
    timerRef.current = setInterval(() => {
      if (document.visibilityState === 'visible') fetchOnce()
    }, POLL_MS)
  }, [fetchOnce])

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    fetchOnce()
    start()
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        fetchOnce()
        start()
      } else {
        stop()
      }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      stop()
    }
  }, [fetchOnce, start, stop])

  return { callers, loading, newCallIds, refresh: fetchOnce }
}
```

- [ ] **Step 2: Build check**

```bash
cd frontend && npm run build
```
Expected: builds clean (no import/syntax errors). The hook is exercised once wired in Task 8.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useCallFeedPolling.js
git commit -m "feat: add visibility-aware call feed polling hook"
```

### Task 4: `useCallAlerts` hook (toast + chime + per-tenant mute)

**Files:**
- Create: `frontend/src/hooks/useCallAlerts.js`

- [ ] **Step 1: Write the hook**

```js
import { useCallback, useEffect, useRef, useState } from 'react'

// In-page call alerts: a toast queue + a WebAudio chime, with a mute toggle
// persisted per tenant. No browser-notification permission needed. Audio only
// plays after the first user gesture (browser autoplay policy); `soundReady`
// reflects whether audio is unlocked so the UI can show a hint until then.
export function useCallAlerts(tenantId) {
  const muteKey = `intake.mute.${tenantId || 'unknown'}`
  const [muted, setMuted] = useState(() => {
    try {
      return localStorage.getItem(muteKey) === '1'
    } catch {
      return false
    }
  })
  const [toasts, setToasts] = useState([])
  const [soundReady, setSoundReady] = useState(false)
  const ctxRef = useRef(null)
  const nextId = useRef(1)

  // Unlock audio on the first user gesture.
  useEffect(() => {
    const unlock = () => {
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext
        if (Ctx && !ctxRef.current) ctxRef.current = new Ctx()
        if (ctxRef.current?.state === 'suspended') ctxRef.current.resume()
        setSoundReady(true)
      } catch {
        /* ignore — toasts still work */
      }
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
    window.addEventListener('pointerdown', unlock)
    window.addEventListener('keydown', unlock)
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [])

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      const next = !m
      try {
        localStorage.setItem(muteKey, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }, [muteKey])

  const playChime = useCallback(() => {
    const ctx = ctxRef.current
    if (!ctx) return
    try {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = 880
      gain.gain.setValueAtTime(0.0001, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start()
      osc.stop(ctx.currentTime + 0.36)
    } catch {
      /* ignore */
    }
  }, [])

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  // Call with an array of new caller objects ({id, caller_name, result, ...}).
  const notify = useCallback(
    (callers) => {
      if (!callers || callers.length === 0) return
      const added = callers.map((c) => ({
        id: nextId.current++,
        callId: c.id,
        title: c.caller_name || 'Unknown caller',
        status: c.result || c.lead_status || 'logged',
      }))
      setToasts((list) => [...added, ...list].slice(0, 4))
      added.forEach((t) => setTimeout(() => dismiss(t.id), 6000))
      if (!muted) playChime()
    },
    [muted, playChime, dismiss]
  )

  return { toasts, notify, dismiss, muted, toggleMute, soundReady }
}
```

- [ ] **Step 2: Build check**

```bash
cd frontend && npm run build
```
Expected: builds clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useCallAlerts.js
git commit -m "feat: add in-page call alert hook with chime and per-tenant mute"
```

---

## Phase 3 — Components

### Task 5: `CallFeedItem` + `CallFeed`

**Files:**
- Create: `frontend/src/components/intake/CallFeedItem.jsx`
- Create: `frontend/src/components/intake/CallFeed.jsx`

- [ ] **Step 1: Write `CallFeedItem.jsx`**

```jsx
import React from 'react'
import { ExternalLink, PhoneIncoming, PhoneMissed } from 'lucide-react'

const STATUS = {
  missed: { label: 'missed', cls: 'bg-red-100 text-red-700' },
  answered: { label: 'answered', cls: 'bg-emerald-100 text-emerald-700' },
}

function durationLabel(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return null
  const m = Math.floor(value / 60)
  const s = value % 60
  return m ? `${m}m ${s}s` : `${s}s`
}

export default function CallFeedItem({ caller, selected, isNew, onSelect }) {
  const status = STATUS[(caller.result || '').toLowerCase()]
  const Icon = (caller.result || '').toLowerCase() === 'missed' ? PhoneMissed : PhoneIncoming
  return (
    <button
      type="button"
      onClick={() => onSelect(caller)}
      className={`w-full rounded-2xl border p-3 text-left transition ${
        selected
          ? 'border-brand-accent bg-white shadow-sm'
          : isNew
          ? 'border-brand-green bg-white shadow-[0_0_0_2px_rgba(58,165,100,0.25)]'
          : 'border-brand-line bg-brand-bg-soft hover:border-brand-accent/60 hover:bg-white'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon size={14} className="shrink-0 text-brand-muted" />
          <p className="truncate text-sm font-bold text-brand-ink">{caller.caller_name}</p>
        </div>
        <span className="shrink-0 text-[10px] font-bold uppercase tracking-widest text-brand-muted">
          {caller.occurred_at
            ? new Date(caller.occurred_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
            : ''}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-brand-muted">
        {status && <span className={`rounded-full px-2 py-0.5 font-bold ${status.cls}`}>{status.label}</span>}
        {caller.phone && <span>{caller.phone}</span>}
        {caller.answered_by && <span>by {caller.answered_by}</span>}
        {durationLabel(caller.duration_seconds) && <span>{durationLabel(caller.duration_seconds)}</span>}
        {caller.source === 'zoom_phone' && <span className="font-bold text-brand-ink">Zoom</span>}
        {caller.recording_url && (
          <a
            href={caller.recording_url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 font-bold text-brand-accent"
          >
            Rec <ExternalLink size={10} />
          </a>
        )}
      </div>
    </button>
  )
}
```

- [ ] **Step 2: Write `CallFeed.jsx`**

```jsx
import React, { useMemo, useState } from 'react'
import { PhoneCall, RefreshCw } from 'lucide-react'
import CallFeedItem from './CallFeedItem'

// Left-pane unified call feed. `sources` present in the data drive the filter
// chips; the filter only renders when more than one source exists (a manual-only
// tenant sees a clean list). Sync shows only when allowed (admin + integration).
export default function CallFeed({
  callers,
  loading,
  newCallIds,
  selectedId,
  onSelect,
  canSync,
  syncing,
  onSync,
}) {
  const [filter, setFilter] = useState('all')
  const newSet = useMemo(() => new Set(newCallIds), [newCallIds])
  const sources = useMemo(
    () => Array.from(new Set(callers.map((c) => c.source).filter(Boolean))),
    [callers]
  )
  const showFilter = sources.length > 1
  const visible = filter === 'all' ? callers : callers.filter((c) => c.source === filter)

  return (
    <section className="rounded-3xl border border-brand-line bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <PhoneCall size={18} className="text-brand-accent" />
          <h2 className="font-serif text-base font-bold text-brand-ink">Call Feed</h2>
        </div>
        {canSync && (
          <button
            type="button"
            onClick={onSync}
            disabled={syncing}
            className="inline-flex items-center gap-1 rounded-xl bg-brand-ink px-2.5 py-1.5 text-[11px] font-bold text-white disabled:opacity-50"
          >
            <RefreshCw size={12} /> {syncing ? 'Syncing…' : 'Sync'}
          </button>
        )}
      </div>

      {showFilter && (
        <div className="mb-3 flex gap-1">
          {['all', ...sources].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setFilter(s)}
              className={`rounded-full px-2.5 py-1 text-[11px] font-bold capitalize ${
                filter === s ? 'bg-brand-ink text-white' : 'bg-brand-bg-soft text-brand-muted'
              }`}
            >
              {s === 'all' ? 'All' : s === 'zoom_phone' ? 'Zoom' : s}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="rounded-2xl border border-dashed border-brand-line bg-brand-bg-soft p-5 text-center text-sm text-brand-muted">
          Loading calls…
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-brand-line bg-brand-bg-soft p-5 text-center text-sm text-brand-muted">
          No calls yet.
        </div>
      ) : (
        <div className="grid max-h-[70vh] gap-2 overflow-y-auto pr-1">
          {visible.map((caller) => (
            <CallFeedItem
              key={caller.id}
              caller={caller}
              selected={selectedId === caller.id}
              isNew={newSet.has(caller.id)}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 3: Build check**

```bash
cd frontend && npm run build
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/intake/CallFeedItem.jsx frontend/src/components/intake/CallFeed.jsx
git commit -m "feat: add unified call feed components"
```

### Task 6: `CallFacts` + `NewCallToasts`

**Files:**
- Create: `frontend/src/components/intake/CallFacts.jsx`
- Create: `frontend/src/components/intake/NewCallToasts.jsx`

- [ ] **Step 1: Write `CallFacts.jsx`**

```jsx
import React from 'react'
import { ExternalLink } from 'lucide-react'

function durationLabel(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return null
  const m = Math.floor(value / 60)
  const s = value % 60
  return m ? `${m}m ${s}s` : `${s}s`
}

export default function CallFacts({ caller }) {
  if (!caller) return null
  const fields = [
    ['Called', caller.occurred_at ? new Date(caller.occurred_at).toLocaleString() : 'Unknown'],
    ['Phone', caller.phone || 'Not captured'],
    ['Status', caller.result || '—'],
    ['Answered by', caller.answered_by || '—'],
    ['Duration', durationLabel(caller.duration_seconds) || '—'],
    ['Source', caller.source === 'zoom_phone' ? 'Zoom Phone' : 'Manual'],
  ]
  return (
    <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-brand-muted">Selected call</p>
          <h3 className="mt-1 font-serif text-lg font-bold text-brand-ink">{caller.caller_name}</h3>
        </div>
        <div className="flex gap-2">
          {caller.recording_url && (
            <a href={caller.recording_url} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 rounded-full bg-brand-bg-soft px-3 py-1 text-[11px] font-bold text-brand-accent">
              ▶ Recording <ExternalLink size={11} />
            </a>
          )}
          {caller.transcript_url && (
            <a href={caller.transcript_url} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1 rounded-full bg-brand-bg-soft px-3 py-1 text-[11px] font-bold text-brand-accent">
              Transcript <ExternalLink size={11} />
            </a>
          )}
        </div>
      </div>
      <dl className="mt-4 grid gap-3 text-xs md:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt className="font-black uppercase tracking-widest text-brand-muted">{label}</dt>
            <dd className="mt-1 text-brand-ink">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
```

- [ ] **Step 2: Write `NewCallToasts.jsx`**

```jsx
import React from 'react'
import { Bell, X } from 'lucide-react'

export default function NewCallToasts({ toasts, onView, onDismiss }) {
  if (!toasts || toasts.length === 0) return null
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} className="flex items-center gap-3 rounded-2xl border border-brand-green/30 bg-white px-4 py-3 shadow-lg">
          <Bell size={16} className="text-brand-green" />
          <div className="min-w-0">
            <p className="truncate text-sm font-bold text-brand-ink">New call — {t.title}</p>
            <p className="text-[11px] text-brand-muted">{t.status}</p>
          </div>
          <button type="button" onClick={() => onView(t.callId)}
                  className="rounded-lg bg-brand-ink px-2.5 py-1 text-[11px] font-bold text-white">
            View
          </button>
          <button type="button" onClick={() => onDismiss(t.id)} className="text-brand-muted hover:text-brand-ink">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Build check**

```bash
cd frontend && npm run build
```
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/intake/CallFacts.jsx frontend/src/components/intake/NewCallToasts.jsx
git commit -m "feat: add call facts card and new-call toast stack"
```

### Task 7: `RecordsTabs`

**Files:**
- Create: `frontend/src/components/intake/RecordsTabs.jsx`

- [ ] **Step 1: Write `RecordsTabs.jsx`** (receives the already-built panels as props so it owns only tab state)

```jsx
import React, { useState } from 'react'

export default function RecordsTabs({ tabs }) {
  // tabs: [{ key, label, node }]
  const [active, setActive] = useState(tabs[0]?.key)
  const current = tabs.find((t) => t.key === active) || tabs[0]
  return (
    <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setActive(t.key)}
            className={`rounded-xl px-3 py-1.5 text-xs font-bold ${
              active === t.key ? 'bg-brand-ink text-white' : 'bg-brand-bg-soft text-brand-muted'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {current?.node}
    </section>
  )
}
```

- [ ] **Step 2: Build check**

```bash
cd frontend && npm run build
```
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/intake/RecordsTabs.jsx
git commit -m "feat: add records tabs wrapper"
```

---

## Phase 4 — Wire the page

### Task 8: Rebuild `IntakeDashboardPage` as the two-pane inbox

Rewire the page to: poll via `useCallFeedPolling`, alert via `useCallAlerts`, render `CallFeed` (left) + a work panel (right: `CallFacts` + History Matches + the existing capture form), and move Export/Partner/Rotation into `RecordsTabs`. The capture-form state, `selectRecentCaller`, `selectZoomPhoneCall`, `runSearch*`, `submitCall`, `assignLead`, `exportCalls`, `syncZoomPhoneCalls`, and the `<form>` JSX already exist in the file — **keep them**; only the top-level data loading and layout change.

**Files:**
- Modify: `frontend/src/pages/IntakeDashboardPage.jsx`
- Remove the standalone `RecentCallersPanel` and `ZoomPhoneCallsPanel` components (replaced by `CallFeed`).

- [ ] **Step 1: Swap imports**

At the top of `IntakeDashboardPage.jsx`, add:

```js
import CallFeed from '../components/intake/CallFeed'
import CallFacts from '../components/intake/CallFacts'
import NewCallToasts from '../components/intake/NewCallToasts'
import RecordsTabs from '../components/intake/RecordsTabs'
import { useCallFeedPolling } from '../hooks/useCallFeedPolling'
import { useCallAlerts } from '../hooks/useCallAlerts'
import { getZoomPhoneStatus } from '../api'
import { Bell, BellOff } from 'lucide-react'
```

- [ ] **Step 2: Replace recent-callers state with the polling hook**

Delete `recentLimit`, `recentCallers`, `recentLoading`, `loadRecentCallers`, and its `useEffect`. Add inside the component:

```js
  const { callers: feedCallers, loading: feedLoading, newCallIds, refresh: refreshFeed } =
    useCallFeedPolling(20)
  const { toasts, notify, dismiss, muted, toggleMute, soundReady } = useCallAlerts(user?.tenant_id)
  const [zoomConnected, setZoomConnected] = useState(false)

  useEffect(() => {
    let cancelled = false
    getZoomPhoneStatus()
      .then((s) => { if (!cancelled) setZoomConnected(Boolean(s?.connected)) })
      .catch(() => { if (!cancelled) setZoomConnected(false) })
    return () => { cancelled = true }
  }, [])

  // Fire alerts whenever the poll surfaces new ids.
  useEffect(() => {
    if (!newCallIds.length) return
    const fresh = feedCallers.filter((c) => newCallIds.includes(c.id))
    notify(fresh)
  }, [newCallIds, feedCallers, notify])
```

(Confirm the auth `user` object carries `tenant_id`; if the field name differs, use the actual id field — grep `useAuth` consumers. If absent, fall back to `'tenant'` so the mute key is still stable per session.)

- [ ] **Step 3: Replace `selectedRecentCaller` handling**

Keep `selectedRecentCaller` state and `selectRecentCaller` (it already pre-fills the form + runs history search). Add a select-by-id used by the toast View button:

```js
  const selectCallById = useCallback((callId) => {
    const caller = feedCallers.find((c) => c.id === callId)
    if (caller) selectRecentCaller(caller)
  }, [feedCallers, selectRecentCaller])
```

In `submitCall` and `syncZoomPhoneCalls`, replace `await loadRecentCallers()` with `await refreshFeed()`. Delete the now-unused Zoom panel state (`zoomPhoneCalls`, `zoomPhoneLoading`, `loadZoomPhoneCalls`) **only if** the unified feed fully replaces it — keep `zoomPhoneSyncing` + `syncZoomPhoneCalls`. Keep `selectZoomPhoneCall` reachable only if still referenced; otherwise remove it.

- [ ] **Step 4: Replace the JSX layout**

Replace the `grid ... xl:grid-cols-[minmax(0,1fr)_420px]` block. New structure:

```jsx
        <div className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
          <CallFeed
            callers={feedCallers}
            loading={feedLoading}
            newCallIds={newCallIds}
            selectedId={selectedRecentCaller?.id}
            onSelect={selectRecentCaller}
            canSync={user?.role === 'admin' && zoomConnected}
            syncing={zoomPhoneSyncing}
            onSync={syncZoomPhoneCalls}
          />

          <div className="space-y-5">
            {selectedRecentCaller && <CallFacts caller={selectedRecentCaller} />}

            {/* existing search <section> stays here */}
            {/* existing History Matches <section> stays here */}
            {/* existing Call Capture <section> (the form) stays here */}

            <RecordsTabs
              tabs={[
                { key: 'export', label: 'Call records', node: (
                  <IntakeExportPanel
                    exportStart={exportStart} exportEnd={exportEnd} exporting={exporting}
                    onExportStartChange={setExportStart} onExportEndChange={setExportEnd}
                    onExport={exportCalls}
                  />
                )},
                { key: 'partner', label: 'Partner log', node: <PartnerLogPanel /> },
                ...(user?.role === 'admin' ? [{ key: 'rotation', label: 'Rotation', node: <RotationAdmin /> }] : []),
              ]}
            />
          </div>
        </div>

        <NewCallToasts toasts={toasts} onView={selectCallById} onDismiss={dismiss} />
```

Add the 🔔 mute toggle into the header next to the title:

```jsx
            <button type="button" onClick={toggleMute}
              className="inline-flex items-center gap-1 rounded-full border border-brand-line bg-white px-3 py-1 text-[11px] font-bold text-brand-muted">
              {muted ? <BellOff size={13} /> : <Bell size={13} />}
              {muted ? 'Muted' : (soundReady ? 'Sound on' : 'Click to enable sound')}
            </button>
```

Remove the old `RecentCallersPanel` and `ZoomPhoneCallsPanel` component definitions and their usages. The `IntakeExportPanel`, `PartnerLogPanel`, `RotationAdmin` definitions stay (now rendered inside `RecordsTabs`).

- [ ] **Step 5: Build check**

```bash
cd frontend && npm run build
```
Expected: clean — resolve any references to deleted state (`recentCallers`, `zoomPhoneCalls`, etc.) until the build is green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/IntakeDashboardPage.jsx
git commit -m "feat: rebuild intake dashboard as two-pane call inbox"
```

---

## Phase 5 — Wrap-up

### Task 9: Suite, docs, manual verification

- [ ] **Step 1: Backend suite**

```bash
cd backend && <env prefix> py -m pytest tests/test_intake_dashboard.py -q
```
Expected: PASS.

- [ ] **Step 2: Frontend build**

```bash
cd frontend && npm run build
```
Expected: clean.

- [ ] **Step 3: Manual verification (dev server)** — run `npm run dev`, open the intake dashboard, and confirm:
  - Feed lists recent calls; selecting one shows facts + auto-searched history + pre-filled form.
  - Logging a call refreshes the feed without losing context.
  - Simulate a new call (insert an inbound `call` `CommunicationLog`, or hit the Zoom webhook) → within ~15s a toast appears + chime plays (after one page click); no toast on initial load.
  - Hide the tab for >15s, then return → feed refetches on focus.
  - Mute toggle persists across reload; key is namespaced per tenant.
  - A manual-only tenant (no Zoom) shows no Sync button and no source filter.

- [ ] **Step 4: Update `TASKS.md` and `CHANGELOG.md`**

Add a `TASKS.md` section "Call Inbox Redesign — 2026-06-22" with checked items (feed facts/source, polling, alerts, two-pane layout, component split, batched enrichment). Add a `CHANGELOG.md` entry with Added/Changed/Tests sections.

- [ ] **Step 5: Commit**

```bash
git add TASKS.md CHANGELOG.md
git commit -m "docs: record call inbox redesign in TASKS and CHANGELOG"
```

- [ ] **Step 6:** Offer to open a PR / deploy (do not push without the user asking).

---

## Self-review notes
- Spec coverage: §A backend fields → T1; batched enrichment → T2; §B component split → T5/T6/T7/T8; §C layout → T8; §D polling → T3/T8; §E alerts → T4/T6/T8; multi-tenant gating (conditional Sync/filter, per-tenant mute) → T5/T8. Testing → T1/T2 backend, T3–T9 build+manual.
- Source-agnostic: `source` field + data-presence affordances (T1, T5, T6); no hardcoded brand checks in feed rendering.
- Property-name consistency: `result`, `answered_by`, `duration_seconds`, `recording_url`, `transcript_url`, `source` used identically across schema (T1), feed item (T5), facts (T6).
- Frontend has no JS test runner → gates are `npm run build` + explicit manual checks (consistent with prior intake plans).
- `user.tenant_id` is verified in T8 Step 2 with a documented fallback.
