import React, { useMemo } from 'react'
import { AlertCircle, CheckCircle2, Clock3, Plus, X } from 'lucide-react'

function formatLabel(draft) {
  const fallback = [draft?.caller_name, draft?.phone]
    .filter(Boolean)
    .find(Boolean) || 'New intake call'
  return fallback.slice(0, 48)
}

function ageParts(value) {
  if (!value) return { label: 'not saved yet', stale: false }
  const started = new Date(value).getTime()
  if (Number.isNaN(started)) return { label: 'not saved yet', stale: false }

  const minutes = Math.floor(Math.max(0, Date.now() - started) / 60000)
  if (minutes < 1) return { label: 'just now', stale: false }
  if (minutes < 60) return { label: `${minutes} min ago`, stale: false }

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return { label: `${hours} hr ago`, stale: false }

  const days = Math.floor(hours / 24)
  return { label: `${days} day${days === 1 ? '' : 's'} ago`, stale: true }
}

function statusText(draft) {
  if (draft?._syncError) return 'Needs review'
  if (draft?._syncing) return 'Saving'
  if (draft?.dirty || draft?._dirty) return 'Unsaved'
  if (draft?._localOnly || draft?._local_only) return 'Local'
  return 'Saved'
}

function statusMeta(draft) {
  const state = statusText(draft)
  if (state === 'Needs review') {
    return {
      label: state,
      icon: AlertCircle,
      className: 'border-brand-rose/30 bg-brand-rose/10 text-brand-rose',
      iconClass: 'text-brand-rose',
    }
  }
  if (state === 'Unsaved' || state === 'Saving') {
    return {
      label: state,
      icon: Clock3,
      className: 'border-brand-amber/30 bg-brand-amber/10 text-brand-ink',
      iconClass: 'text-brand-amber',
    }
  }
  if (state === 'Local') {
    return {
      label: state,
      icon: Clock3,
      className: 'border-brand-line bg-brand-bg-soft text-brand-muted',
      iconClass: 'text-brand-muted',
    }
  }
  return {
    label: state,
    icon: CheckCircle2,
    className: 'border-brand-green/30 bg-brand-green/10 text-brand-green',
    iconClass: 'text-brand-green',
  }
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

  const activeDraft = sorted.find((draft) => draft.draft_id === activeDraftId) || sorted[0] || null
  const activeAge = ageParts(activeDraft?.updated_at || activeDraft?.created_at)
  const activeStatus = statusMeta(activeDraft)
  const ActiveStatusIcon = activeStatus.icon
  const showOpenCalls = sorted.length > 1

  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-2 rounded-xl border border-brand-line bg-white px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {activeDraft && (
            <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold ${activeStatus.className}`}>
              <ActiveStatusIcon size={11} className={activeStatus.iconClass} />
              {activeStatus.label}
            </span>
          )}
          <span className={`truncate text-xs ${activeAge.stale ? 'text-brand-amber' : 'text-brand-muted'}`}>
            {activeDraft
              ? `${activeAge.stale ? 'Recovered call edited' : 'Saved'} ${activeAge.label}`
              : 'Start a new call to capture intake notes.'}
          </span>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {activeDraft && (
            <button
              type="button"
              onClick={() => onClose?.(activeDraft.draft_id)}
              disabled={disabled}
              className="inline-flex items-center gap-1 rounded-lg border border-brand-line bg-white px-3 py-1.5 text-xs font-bold text-brand-muted hover:border-brand-rose/40 hover:text-brand-rose disabled:opacity-50"
            >
              <X size={13} />
              Discard
            </button>
          )}
          <button
            type="button"
            onClick={onNew}
            disabled={disabled}
            title="Alt+Shift+N"
            className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-3 py-1.5 text-xs font-bold text-white hover:bg-brand-ink-2 disabled:opacity-50"
          >
            <Plus size={14} />
            New call
          </button>
        </div>
      </div>

      {showOpenCalls && (
        <div className="flex flex-wrap gap-1.5">
          {sorted.slice(0, 8).map((draft, index) => {
            const meta = statusMeta(draft)
            const age = ageParts(draft.updated_at || draft.created_at)
            const StatusIcon = meta.icon
            const selected = draft.draft_id === activeDraft?.draft_id
            return (
              <div
                key={draft.draft_id || index}
                className={`group inline-flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs ${
                  selected ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line bg-white text-brand-ink'
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSwitch(draft.draft_id)}
                  disabled={disabled}
                  className="min-w-0 text-left"
                >
                  <span className={`block max-w-[150px] truncate font-bold ${selected ? 'text-white' : 'text-brand-ink'}`}>
                    {formatLabel(draft)}
                  </span>
                  <span className={`mt-0.5 flex items-center gap-1 text-[10px] ${selected ? 'text-white/70' : 'text-brand-muted'}`}>
                    <StatusIcon size={10} className={selected ? 'text-white/70' : meta.iconClass} />
                    {meta.label} / {age.label}
                  </span>
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
                  className={`inline-flex h-6 w-6 items-center justify-center rounded-md opacity-70 hover:bg-brand-rose/10 hover:text-brand-rose group-hover:opacity-100 disabled:opacity-40 ${
                    selected ? 'text-white/70' : 'text-brand-muted'
                  }`}
                >
                  <X size={12} />
                </button>
              </div>
            )
          })}
          {sorted.length > 8 && (
            <span className="inline-flex items-center rounded-lg border border-brand-line bg-white px-2.5 py-1.5 text-xs font-bold text-brand-muted">
              +{sorted.length - 8} more
            </span>
          )}
        </div>
      )}
    </div>
  )
}
