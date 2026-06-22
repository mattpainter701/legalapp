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
  const visible =
    filter === 'all' ? callers : callers.filter((c) => c.source === filter)

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
