import React, { useMemo } from 'react'
import { CircleDashed, Plus, PlusCircle, X } from 'lucide-react'

function formatLabel(draft) {
  const fallback = [draft?.caller_name, draft?.phone]
    .filter(Boolean)
    .find(Boolean) || 'Unnamed call'
  return fallback.slice(0, 18)
}

function formatElapsed(value) {
  if (!value) return ''
  const now = Date.now()
  const started = new Date(value).getTime()
  const deltaMs = Math.max(0, now - started)
  const minutes = Math.floor(deltaMs / 60000)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
}

function statusText(draft) {
  if (draft?.dirty || draft?._dirty) return 'Unsaved'
  if (draft?._localOnly || draft?._local_only) return 'Local'
  return 'Saved'
}

function statusClass(type) {
  if (type === 'Unsaved') return 'text-brand-amber'
  if (type === 'Local') return 'text-brand-muted'
  return 'text-brand-green'
}

export default function DraftTabStrip({
  drafts = [],
  activeDraftId = null,
  onSwitch,
  onNew,
  onClose,
  disabled,
}) {
  const sorted = useMemo(() => [...drafts].sort((a, b) => {
    const aTime = new Date(a.updated_at || a.created_at || 0).getTime()
    const bTime = new Date(b.updated_at || b.created_at || 0).getTime()
    return bTime - aTime
  }), [drafts])

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-brand-line bg-white p-2">
      {sorted.map((draft, index) => {
        const isActive = draft.draft_id === activeDraftId
        const state = statusText(draft)
        return (
          <div
            key={draft.draft_id || index}
            className={`relative inline-flex min-h-10 shrink-0 items-center gap-2 rounded-full border px-2.5 py-1.5 text-left text-xs font-bold transition-all ${
              isActive
                ? 'border-brand-ink bg-white text-brand-ink shadow-sm'
                : 'border-brand-line bg-brand-bg-soft text-brand-muted hover:border-brand-accent'
            }`}
          >
            {isActive && <span className="absolute left-0 top-1 bottom-1 w-[2px] rounded-full bg-brand-accent" />}
            <button
              type="button"
              onClick={() => onSwitch(draft.draft_id)}
              disabled={disabled}
              className="inline-flex min-w-0 items-center gap-2 text-left"
            >
              <span className="max-w-[130px] truncate">
                {formatLabel(draft)}
              </span>
              {draft.phone && <span className="truncate text-[10px] font-normal text-brand-muted">• {draft.phone}</span>}
              <span className={`inline-flex items-center gap-1 text-[10px] ${statusClass(state)}`}>
                {(draft.dirty || draft._dirty || draft._localOnly || draft._local_only) ? <CircleDashed size={11} /> : <span />}
                {formatElapsed(draft.created_at || draft.updated_at)}
              </span>
              <span className={`text-[10px] ${statusClass(state)}`}>{state}</span>
            </button>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onClose?.(draft.draft_id)
              }}
              disabled={disabled}
              title="Close draft"
              aria-label={`Close ${formatLabel(draft)} draft`}
              className="ml-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose disabled:opacity-40"
            >
              <X size={12} />
            </button>
          </div>
        )
      })}
      <button
        type="button"
        onClick={onNew}
        disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full border border-dashed border-brand-line px-3 py-1.5 text-xs font-bold text-brand-muted hover:border-brand-accent hover:text-brand-ink"
      >
        <Plus size={13} /> New call
      </button>
      {!disabled && (
        <span className="ml-1 inline-flex items-center gap-1 text-[10px] text-brand-muted">
          <span>Alt+1..9 switch</span>
          <span>•</span>
          <span>Alt+Shift+N new</span>
          <span className="inline-flex items-center"><PlusCircle size={11} /></span>
        </span>
      )}
    </div>
  )
}
