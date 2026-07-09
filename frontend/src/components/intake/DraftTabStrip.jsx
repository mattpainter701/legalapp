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
  const inactiveDrafts = sorted.filter((draft) => draft.draft_id !== activeDraft?.draft_id)
  const activeAge = ageParts(activeDraft?.updated_at || activeDraft?.created_at)
  const activeStatus = statusMeta(activeDraft)
  const ActiveStatusIcon = activeStatus.icon

  return (
    <div className="rounded-2xl border border-brand-line bg-brand-bg-soft/40 p-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-brand-muted">
              Active call
            </span>
            {activeDraft && (
              <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold ${activeStatus.className}`}>
                <ActiveStatusIcon size={11} className={activeStatus.iconClass} />
                {activeStatus.label}
              </span>
            )}
          </div>

          <div className="mt-1 flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
            <button
              type="button"
              onClick={() => activeDraft?.draft_id && onSwitch(activeDraft.draft_id)}
              disabled={disabled || !activeDraft}
              className="max-w-full truncate text-left font-serif text-lg font-bold text-brand-ink disabled:cursor-default"
            >
              {activeDraft ? formatLabel(activeDraft) : 'No call started'}
            </button>
            {activeDraft?.phone && (
              <span className="font-mono text-xs text-brand-muted">{activeDraft.phone}</span>
            )}
          </div>

          <p className={`mt-1 text-xs ${activeAge.stale ? 'text-brand-amber' : 'text-brand-muted'}`}>
            {activeDraft
              ? `${activeAge.stale ? 'Recovered draft last edited' : 'Last edited'} ${activeAge.label}`
              : 'Start a new call to capture intake notes.'}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {activeDraft && (
            <button
              type="button"
              onClick={() => onClose?.(activeDraft.draft_id)}
              disabled={disabled}
              className="inline-flex items-center gap-1 rounded-lg border border-brand-line bg-white px-3 py-2 text-xs font-bold text-brand-muted hover:border-brand-rose/40 hover:text-brand-rose disabled:opacity-50"
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
            className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-3 py-2 text-xs font-bold text-white hover:bg-brand-ink-2 disabled:opacity-50"
          >
            <Plus size={14} />
            New call
          </button>
        </div>
      </div>

      {inactiveDrafts.length > 0 && (
        <div className="mt-3 border-t border-brand-line pt-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-brand-muted">
              Recent open calls
            </span>
            <span className="text-[10px] text-brand-muted">{inactiveDrafts.length} waiting</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {inactiveDrafts.slice(0, 6).map((draft, index) => {
              const meta = statusMeta(draft)
              const age = ageParts(draft.updated_at || draft.created_at)
              const StatusIcon = meta.icon
              return (
                <div
                  key={draft.draft_id || index}
                  className="group inline-flex max-w-full items-center gap-2 rounded-lg border border-brand-line bg-white px-2.5 py-2 text-xs shadow-sm"
                >
                  <button
                    type="button"
                    onClick={() => onSwitch(draft.draft_id)}
                    disabled={disabled}
                    className="min-w-0 text-left"
                  >
                    <span className="block max-w-[160px] truncate font-bold text-brand-ink">
                      {formatLabel(draft)}
                    </span>
                    <span className="mt-0.5 flex items-center gap-1 text-[10px] text-brand-muted">
                      <StatusIcon size={10} className={meta.iconClass} />
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
                    className="inline-flex h-6 w-6 items-center justify-center rounded-md text-brand-muted opacity-70 hover:bg-brand-rose/10 hover:text-brand-rose group-hover:opacity-100 disabled:opacity-40"
                  >
                    <X size={12} />
                  </button>
                </div>
              )
            })}
            {inactiveDrafts.length > 6 && (
              <span className="inline-flex items-center rounded-lg border border-brand-line bg-white px-3 py-2 text-xs font-bold text-brand-muted">
                +{inactiveDrafts.length - 6} more
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
