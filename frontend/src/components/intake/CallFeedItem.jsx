import React from 'react'
import { Clock, ExternalLink, PhoneIncoming, PhoneMissed, PhoneOutgoing } from 'lucide-react'

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

// Time only for today's calls; "Jun 21, 3:45 PM" for older ones.
function whenLabel(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (d.toDateString() === new Date().toDateString()) return time
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`
}

// Relative "12m ago" / "3h ago" / "2d ago" so reception sees recency at a glance.
function agoLabel(iso) {
  if (!iso) return ''
  const diffMs = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(diffMs)) return ''
  const sec = Math.round(diffMs / 1000)
  if (sec < 45) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr}h ago`
  return `${Math.round(hr / 24)}d ago`
}

export default function CallFeedItem({ caller, selected, isNew, onSelect }) {
  const status = STATUS[(caller.result || '').toLowerCase()]
  const direction = String(caller.direction || '').toLowerCase()
  const Icon = (caller.result || '').toLowerCase() === 'missed'
    ? PhoneMissed
    : direction === 'outbound'
    ? PhoneOutgoing
    : PhoneIncoming
  const internalLabel = caller.internal_call_type === 'internal_to_internal'
    ? 'internal'
    : caller.internal_call_type === 'internal_outbound'
    ? 'internal outbound'
    : null
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
      <div className="flex min-w-0 items-center gap-2">
        <Icon size={14} className="shrink-0 text-brand-muted" />
        <p className="truncate text-sm font-bold text-brand-ink">{caller.caller_name}</p>
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-xs">
        <Clock size={12} className="shrink-0 text-brand-accent" />
        <span className="font-bold text-brand-ink">{whenLabel(caller.occurred_at)}</span>
        <span className="font-semibold text-brand-muted">· {agoLabel(caller.occurred_at)}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-brand-muted">
        {status && <span className={`rounded-full px-2 py-0.5 font-bold ${status.cls}`}>{status.label}</span>}
        {caller.phone && <span>{caller.phone}</span>}
        {caller.answered_by && <span>by {caller.answered_by}</span>}
        {durationLabel(caller.duration_seconds) && <span>{durationLabel(caller.duration_seconds)}</span>}
        {direction && <span className="font-bold capitalize">{direction}</span>}
        {internalLabel && <span className="rounded-full bg-slate-200 px-2 py-0.5 font-bold text-slate-700">{internalLabel}</span>}
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
