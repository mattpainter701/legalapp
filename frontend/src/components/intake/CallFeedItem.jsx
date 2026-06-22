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
